from __future__ import annotations

import asyncio
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from time import time_ns

from ...core.channel import ChannelApprovalRequest, ChannelContext
from ...core.models import ApprovalDecision, ApprovalResult
from ...i18n import ENGLISH, Translator, create_translator
from .api import TelegramApiError, TelegramTransportError
from .channel import TelegramChannel
from .identity import parse_provider_thread_id

_CALLBACK_PREFIX = "bcn"
_CALLBACK_ANSWER_TIMEOUT_SECONDS = 10.0
_RESOLVED_TOKEN_LIMIT = 256
_ACTION_MESSAGE_KEYS = {
    "command_execution": "approval.action.command_execution",
    "file_change": "approval.action.file_change",
    "permissions": "approval.action.permissions",
}


@dataclass(slots=True)
class _PendingApproval:
    request_id: str
    token: str
    chat_id: int
    topic_id: int
    prompt_message_id: int | None
    expected_sender_id: str
    future: asyncio.Future[ApprovalResult]


class TelegramApprovalChannel(TelegramChannel):
    def __init__(self, context: ChannelContext, *, token: str) -> None:
        super().__init__(context, token=token)
        self._translator: Translator = context.translator or create_translator(ENGLISH)
        self._pending_approvals: dict[str, _PendingApproval] = {}
        self._approval_tokens_by_request: dict[str, str] = {}
        self._resolved_approval_tokens: dict[str, str] = {}
        self._approval_requests = 0
        self._approval_callbacks = 0
        self._approval_callback_rejections = 0
        self._approval_callback_answer_failures = 0
        self._approval_feedback_failures = 0
        self._approval_decisions = 0

    @property
    def health(self) -> Mapping[str, object]:
        health = dict(super().health)
        health.update(
            {
                "pending_approvals": len(self._pending_approvals),
                "approval_requests": self._approval_requests,
                "approval_callbacks": self._approval_callbacks,
                "approval_callback_rejections": self._approval_callback_rejections,
                "approval_callback_answer_failures": (
                    self._approval_callback_answer_failures
                ),
                "approval_feedback_failures": self._approval_feedback_failures,
                "approval_decisions": self._approval_decisions,
            }
        )
        return health

    async def stop(self, *, timeout: float) -> None:
        decided_at_ms = time_ns() // 1_000_000
        for pending in tuple(self._pending_approvals.values()):
            self._remember_resolved(pending.token, "stopped")
            if not pending.future.done():
                pending.future.set_result(
                    ApprovalResult(
                        request_id=pending.request_id,
                        decision=ApprovalDecision.REJECTED,
                        decided_at_ms=decided_at_ms,
                        reason="channel_stopped",
                    )
                )
        self._pending_approvals.clear()
        self._approval_tokens_by_request.clear()
        await super().stop(timeout=timeout)

    async def request_approval(
        self,
        request: ChannelApprovalRequest,
        *,
        timeout: float,
    ) -> ApprovalResult:
        request_id = request.approval.request_id
        if request_id in self._approval_tokens_by_request:
            raise ValueError("Telegram approval request is already pending")
        api = self._api
        bot_id = self._bot_id
        if api is None or bot_id is None:
            raise RuntimeError("Telegram channel is not ready for approvals")

        identity = parse_provider_thread_id(request.provider_thread_id)
        if identity.bot_id != bot_id:
            raise ValueError("Telegram approval route belongs to another bot")
        if request.provider_sender_id is None:
            raise ValueError("Telegram approval requires the original sender id")

        reply_to_message_id = self._provider_message_id(
            request.provider_reply_to_message_id
        )
        loop = asyncio.get_running_loop()
        token = secrets.token_urlsafe(12)
        future: asyncio.Future[ApprovalResult] = loop.create_future()
        pending = _PendingApproval(
            request_id=request_id,
            token=token,
            chat_id=identity.chat_id,
            topic_id=identity.topic_id,
            prompt_message_id=None,
            expected_sender_id=request.provider_sender_id,
            future=future,
        )
        self._pending_approvals[token] = pending
        self._approval_tokens_by_request[request_id] = token
        self._approval_requests += 1

        payload: dict[str, object] = {
            "chat_id": identity.chat_id,
            "rich_message": {
                "markdown": self._approval_markdown(request),
                "skip_entity_detection": True,
            },
            "reply_markup": {
                "inline_keyboard": [
                    [
                        {
                            "text": self._translator.text("approval.button.approve"),
                            "callback_data": f"{_CALLBACK_PREFIX}:approve:{token}",
                        },
                        {
                            "text": self._translator.text("approval.button.reject"),
                            "callback_data": f"{_CALLBACK_PREFIX}:reject:{token}",
                        },
                    ]
                ]
            },
        }
        if identity.topic_id:
            payload["message_thread_id"] = identity.topic_id
        if reply_to_message_id is not None:
            payload["reply_parameters"] = {"message_id": reply_to_message_id}

        try:
            prompt = await api.send_rich_message(payload, timeout=timeout)
            provider_prompt_message_id = prompt.get("message_id")
            if (
                not isinstance(provider_prompt_message_id, int)
                or isinstance(provider_prompt_message_id, bool)
                or provider_prompt_message_id <= 0
            ):
                raise ValueError("Telegram approval prompt has no message_id")
            if (
                pending.prompt_message_id is not None
                and pending.prompt_message_id != provider_prompt_message_id
            ):
                raise ValueError(
                    "Telegram approval prompt message correlation mismatch"
                )
            pending.prompt_message_id = provider_prompt_message_id

            if future.done():
                if future.cancelled():
                    raise asyncio.CancelledError
                return future.result()
            return await future
        finally:
            self._pending_approvals.pop(token, None)
            self._approval_tokens_by_request.pop(request_id, None)

    async def _dispatch_update(
        self,
        update: Mapping[str, object],
        *,
        update_id: int,
    ) -> None:
        callback_query = update.get("callback_query")
        if isinstance(callback_query, Mapping):
            self._callback_updates_received += 1
            await self._handle_callback_query(callback_query)
            return
        await super()._dispatch_update(update, update_id=update_id)

    async def _handle_callback_query(
        self,
        callback_query: Mapping[str, object],
    ) -> None:
        self._approval_callbacks += 1
        query_id = callback_query.get("id")
        if not isinstance(query_id, str) or not query_id:
            self._approval_callback_rejections += 1
            self._last_update_disposition = "invalid_callback_query_id"
            return

        parsed = self._callback_action(callback_query.get("data"))
        if parsed is None:
            self._approval_callback_rejections += 1
            self._last_update_disposition = "unsupported_callback_query"
            await self._answer_callback(
                query_id,
                self._translator.text("approval.callback.unknown_action"),
            )
            return
        decision, token = parsed
        pending = self._pending_approvals.get(token)
        if pending is None:
            self._approval_callback_rejections += 1
            state = self._resolved_approval_tokens.get(token)
            self._last_update_disposition = "resolved_approval_callback"
            await self._answer_callback(
                query_id,
                self._resolved_callback_text(state),
            )
            return

        message = callback_query.get("message")
        sender = callback_query.get("from")
        if not isinstance(message, Mapping) or not isinstance(sender, Mapping):
            self._approval_callback_rejections += 1
            self._last_update_disposition = "invalid_approval_callback"
            await self._answer_callback(
                query_id,
                self._translator.text("approval.callback.invalid"),
            )
            return
        chat = message.get("chat")
        chat_id = chat.get("id") if isinstance(chat, Mapping) else None
        message_id = message.get("message_id")
        topic_id = self._message_topic_id(message, fallback=0)
        sender_id = sender.get("id")
        if (
            not isinstance(chat_id, int)
            or isinstance(chat_id, bool)
            or chat_id != pending.chat_id
            or not isinstance(message_id, int)
            or isinstance(message_id, bool)
            or message_id <= 0
            or topic_id is None
            or topic_id != pending.topic_id
        ):
            self._approval_callback_rejections += 1
            self._last_update_disposition = "approval_callback_route_mismatch"
            await self._answer_callback(
                query_id,
                self._translator.text("approval.callback.invalid"),
            )
            return
        if (
            pending.prompt_message_id is not None
            and message_id != pending.prompt_message_id
        ):
            self._approval_callback_rejections += 1
            self._last_update_disposition = "approval_callback_message_mismatch"
            await self._answer_callback(
                query_id,
                self._translator.text("approval.callback.invalid"),
            )
            return
        if (
            not isinstance(sender_id, int)
            or isinstance(sender_id, bool)
            or str(sender_id) != pending.expected_sender_id
        ):
            self._approval_callback_rejections += 1
            self._last_update_disposition = "approval_callback_sender_mismatch"
            await self._answer_callback(
                query_id,
                self._translator.text("approval.callback.sender_mismatch"),
            )
            return

        pending.prompt_message_id = message_id
        if pending.future.done():
            self._approval_callback_rejections += 1
            self._last_update_disposition = "duplicate_approval_callback"
            await self._answer_callback(
                query_id,
                self._resolved_callback_text(self._resolved_approval_tokens.get(token)),
            )
            return

        result = ApprovalResult(
            request_id=pending.request_id,
            decision=decision,
            decided_at_ms=time_ns() // 1_000_000,
        )
        state = "approved" if decision is ApprovalDecision.APPROVED else "rejected"
        self._remember_resolved(token, state)
        self._approval_decisions += 1
        pending.future.set_result(result)
        self._last_update_disposition = f"approval_{state}"
        await self._send_approval_feedback(
            pending,
            message_id=message_id,
            decision=decision,
        )
        await self._answer_callback(
            query_id,
            self._translator.text(
                "approval.callback.approved"
                if decision is ApprovalDecision.APPROVED
                else "approval.callback.rejected"
            ),
        )

    async def _send_approval_feedback(
        self,
        pending: _PendingApproval,
        *,
        message_id: int,
        decision: ApprovalDecision,
    ) -> None:
        api = self._api
        if api is None:
            self._approval_feedback_failures += 1
            return
        payload: dict[str, object] = {
            "chat_id": pending.chat_id,
            "rich_message": {
                "markdown": self._translator.text(
                    "approval.feedback.approved"
                    if decision is ApprovalDecision.APPROVED
                    else "approval.feedback.rejected"
                ),
                "skip_entity_detection": True,
            },
            "reply_parameters": {"message_id": message_id},
        }
        if pending.topic_id:
            payload["message_thread_id"] = pending.topic_id
        try:
            await api.send_rich_message(
                payload,
                timeout=_CALLBACK_ANSWER_TIMEOUT_SECONDS,
            )
        except asyncio.CancelledError:
            raise
        except (
            TelegramApiError,
            TelegramTransportError,
            TimeoutError,
            ValueError,
        ):
            self._approval_feedback_failures += 1

    async def _answer_callback(self, query_id: str, text: str) -> None:
        api = self._api
        if api is None:
            self._approval_callback_answer_failures += 1
            return
        try:
            await api.answer_callback_query(
                query_id,
                text=text,
                timeout=_CALLBACK_ANSWER_TIMEOUT_SECONDS,
            )
        except asyncio.CancelledError:
            raise
        except (
            TelegramApiError,
            TelegramTransportError,
            TimeoutError,
            ValueError,
        ):
            self._approval_callback_answer_failures += 1

    def _remember_resolved(self, token: str, state: str) -> None:
        self._resolved_approval_tokens[token] = state
        while len(self._resolved_approval_tokens) > _RESOLVED_TOKEN_LIMIT:
            oldest = next(iter(self._resolved_approval_tokens))
            self._resolved_approval_tokens.pop(oldest, None)

    @staticmethod
    def _provider_message_id(value: str | None) -> int | None:
        if value is None:
            return None
        try:
            message_id = int(value)
        except ValueError as error:
            raise ValueError("Telegram reply message id must be an integer") from error
        if message_id <= 0:
            raise ValueError("Telegram reply message id must be positive")
        return message_id

    @staticmethod
    def _callback_action(
        data: object,
    ) -> tuple[ApprovalDecision, str] | None:
        if not isinstance(data, str):
            return None
        prefix, separator, rest = data.partition(":")
        action, separator2, token = rest.partition(":")
        if prefix != _CALLBACK_PREFIX or not separator or not separator2 or not token:
            return None
        if action == "approve":
            return ApprovalDecision.APPROVED, token
        if action == "reject":
            return ApprovalDecision.REJECTED, token
        return None

    def _approval_markdown(self, request: ChannelApprovalRequest) -> str:
        action_key = _ACTION_MESSAGE_KEYS.get(request.approval.action)
        action = (
            self._translator.text(action_key)
            if action_key is not None
            else request.approval.action.replace("_", " ")
        )
        lines = [
            self._translator.text("approval.prompt.title"),
            "",
            self._translator.text("approval.prompt.action", {"action": action}),
        ]
        description = request.approval.description
        if description:
            fence = TelegramApprovalChannel._markdown_fence(description)
            lines.extend(("", fence, description, fence))
        return "\n".join(lines)

    @staticmethod
    def _markdown_fence(value: str) -> str:
        longest = 0
        current = 0
        for character in value:
            if character == "`":
                current += 1
                longest = max(longest, current)
            else:
                current = 0
        return "`" * max(3, longest + 1)

    def _resolved_callback_text(self, state: str | None) -> str:
        if state == "approved":
            return self._translator.text("approval.callback.already_approved")
        if state == "rejected":
            return self._translator.text("approval.callback.already_rejected")
        return self._translator.text("approval.callback.invalid")


__all__ = ["TelegramApprovalChannel"]
