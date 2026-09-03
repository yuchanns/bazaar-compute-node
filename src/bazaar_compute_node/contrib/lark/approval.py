from __future__ import annotations

import asyncio
import base64
import json
import secrets
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from time import time_ns
from uuid import uuid4

from ...core.approval import (
    approval_action_text,
    approval_description_text,
    resolved_callback_text,
)
from ...core.channel import ChannelApprovalRequest, ChannelContext
from ...core.models import ApprovalDecision, ApprovalResult
from ...core.timerwheel import TimerWheel
from ...core.utils.clock import remaining
from ...i18n import ENGLISH, Translator, create_translator
from .channel import LarkChannel
from .identity import LarkThreadIdentity, parse_provider_thread_id
from .transport import MESSAGE_CARD, LarkAck

_CARD_UPDATE_TIMEOUT_SECONDS = 5.0
_RESOLVED_TOKEN_LIMIT = 256
_CARD_ACTION_EVENT_TYPES = frozenset({"card.action.trigger", "p2.card.action.trigger"})


@dataclass(slots=True)
class _PendingApproval:
    request: ChannelApprovalRequest
    request_id: str
    token: str
    thread: LarkThreadIdentity
    expected_sender_id: str
    prompt_message_id: str | None
    future: asyncio.Future[ApprovalResult]


class LarkApprovalChannel(LarkChannel):
    def __init__(
        self,
        context: ChannelContext,
        *,
        app_id: str,
        app_secret: str,
        region: str,
        base_url: str,
        timer_wheel: TimerWheel,
    ) -> None:
        super().__init__(
            context,
            app_id=app_id,
            app_secret=app_secret,
            region=region,
            base_url=base_url,
            timer_wheel=timer_wheel,
        )
        self._translator: Translator = context.translator or create_translator(ENGLISH)
        self._pending_approvals: dict[str, _PendingApproval] = {}
        self._approval_tokens_by_request: dict[str, str] = {}
        self._resolved_approval_tokens: OrderedDict[str, str] = OrderedDict()
        self._resolved_card_events: OrderedDict[str, str] = OrderedDict()
        self._approval_lock = asyncio.Lock()
        self._approval_requests = 0
        self._approval_callbacks = 0
        self._approval_callback_rejections = 0
        self._approval_card_update_failures = 0
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
                "approval_card_update_failures": self._approval_card_update_failures,
                "approval_decisions": self._approval_decisions,
            }
        )
        return health

    async def stop(self, *, timeout: float) -> None:
        decided_at_ms = time_ns() // 1_000_000
        async with self._approval_lock:
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
        if timeout <= 0:
            return self._timeout_result(request_id)
        api = self._api
        identity = self._identity
        if api is None or identity is None:
            raise RuntimeError("Lark channel is not ready for approvals")
        if request.provider_sender_id is None:
            raise ValueError("Lark approval requires the original sender id")

        thread = parse_provider_thread_id(
            request.provider_thread_id,
            bot_open_id=identity.open_id,
        )
        reply_to_message_id = _provider_text(request.provider_reply_to_message_id)
        if thread.thread_id != "0" and reply_to_message_id is None:
            raise ValueError("Lark approval requires a topic reply anchor")

        loop = asyncio.get_running_loop()
        pending = _PendingApproval(
            request=request,
            request_id=request_id,
            token=secrets.token_urlsafe(18),
            thread=thread,
            expected_sender_id=request.provider_sender_id,
            prompt_message_id=None,
            future=loop.create_future(),
        )
        async with self._approval_lock:
            if request_id in self._approval_tokens_by_request:
                raise ValueError("Lark approval request is already pending")
            self._pending_approvals[pending.token] = pending
            self._approval_tokens_by_request[request_id] = pending.token
            self._approval_requests += 1

        deadline = loop.time() + timeout
        try:
            content = _approval_card_content(
                request,
                pending.token,
                translator=self._translator,
            )
            budget = remaining(deadline)
            if reply_to_message_id is not None:
                prompt_message_id = await api.reply_message(
                    message_id=reply_to_message_id,
                    message_type="interactive",
                    content=content,
                    reply_in_thread=thread.thread_id != "0",
                    uuid=uuid4().hex,
                    timeout=budget,
                )
            else:
                prompt_message_id = await api.send_message(
                    chat_id=thread.chat_id,
                    message_type="interactive",
                    content=content,
                    uuid=uuid4().hex,
                    timeout=budget,
                )
            prompt_message_id = _provider_text(prompt_message_id)
            if prompt_message_id is None:
                raise ValueError("Lark approval prompt has no message_id")
            if (
                pending.prompt_message_id is not None
                and pending.prompt_message_id != prompt_message_id
            ):
                raise ValueError("Lark approval prompt message correlation mismatch")
            pending.prompt_message_id = prompt_message_id

            if pending.future.done():
                return pending.future.result()
            return await pending.future
        finally:
            async with self._approval_lock:
                self._pending_approvals.pop(pending.token, None)
                if self._approval_tokens_by_request.get(request_id) == pending.token:
                    self._approval_tokens_by_request.pop(request_id, None)

    async def _handle_event(
        self,
        message_type: str,
        payload: Mapping[str, object],
        frame: object,
    ) -> object:
        header = payload.get("header")
        event_type = header.get("event_type") if isinstance(header, Mapping) else None
        if message_type == MESSAGE_CARD or event_type in _CARD_ACTION_EVENT_TYPES:
            return await self._handle_card_callback(payload)
        return await super()._handle_event(message_type, payload, frame)

    async def _handle_card_callback(self, payload: Mapping[str, object]) -> LarkAck:
        self._approval_callbacks += 1
        parsed = _parse_card_callback(payload)
        if parsed is None:
            return self._reject_callback("approval.callback.invalid")
        (
            event_id,
            operator_id,
            chat_id,
            message_id,
            action,
            token,
            card_update_token,
        ) = parsed

        async with self._approval_lock:
            resolved_event = self._resolved_card_events.get(event_id)
            if resolved_event is not None:
                return self._card_ack(
                    resolved_callback_text(self._translator, resolved_event)
                )

            if action not in {"approve", "reject"}:
                self._approval_callback_rejections += 1
                return self._card_ack(
                    self._translator.text("approval.callback.unknown_action"),
                    toast_type="warning",
                )
            pending = self._pending_approvals.get(token)
            if pending is None:
                self._approval_callback_rejections += 1
                state = self._resolved_approval_tokens.get(token)
                return self._card_ack(
                    resolved_callback_text(self._translator, state),
                    toast_type="warning",
                )
            if chat_id != pending.thread.chat_id or (
                pending.prompt_message_id is not None
                and message_id != pending.prompt_message_id
            ):
                self._approval_callback_rejections += 1
                return self._card_ack(
                    self._translator.text("approval.callback.invalid"),
                    toast_type="warning",
                )
            if operator_id != pending.expected_sender_id:
                self._approval_callback_rejections += 1
                return self._card_ack(
                    self._translator.text("approval.callback.sender_mismatch"),
                    toast_type="warning",
                )
            if pending.future.done():
                self._approval_callback_rejections += 1
                state = self._resolved_approval_tokens.get(token)
                return self._card_ack(
                    resolved_callback_text(self._translator, state),
                    toast_type="warning",
                )
            if pending.prompt_message_id is None:
                pending.prompt_message_id = message_id

            decision = (
                ApprovalDecision.APPROVED
                if action == "approve"
                else ApprovalDecision.REJECTED
            )
            state = "approved" if decision is ApprovalDecision.APPROVED else "rejected"
            result = ApprovalResult(
                request_id=pending.request_id,
                decision=decision,
                decided_at_ms=time_ns() // 1_000_000,
            )
            self._remember_resolved(token, state, event_id=event_id)
            self._approval_decisions += 1
            pending.future.set_result(result)

        return self._card_ack(
            self._translator.text(
                "approval.callback.approved"
                if decision is ApprovalDecision.APPROVED
                else "approval.callback.rejected"
            ),
            post_ack=lambda: self._update_approval_card(
                pending,
                decision,
                card_update_token,
            ),
        )

    async def _update_approval_card(
        self,
        pending: _PendingApproval,
        decision: ApprovalDecision,
        card_update_token: str,
    ) -> None:
        api = self._api
        if api is None:
            self._approval_card_update_failures += 1
            return
        try:
            card = json.loads(
                _approval_card_content(
                    pending.request,
                    pending.token,
                    translator=self._translator,
                    decision=decision,
                )
            )
            if not isinstance(card, dict):
                raise TypeError("Lark approval card update is not an object")
            await api.update_card(
                card_update_token,
                card=card,
                timeout=_CARD_UPDATE_TIMEOUT_SECONDS,
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            self._approval_card_update_failures += 1

    def _reject_callback(self, key: str) -> LarkAck:
        self._approval_callback_rejections += 1
        return self._card_ack(self._translator.text(key), toast_type="warning")

    def _card_ack(
        self,
        text: str,
        *,
        toast_type: str = "success",
        post_ack: Callable[[], Awaitable[None]] | None = None,
    ) -> LarkAck:
        toast = {
            "toast": {
                "type": toast_type,
                "content": text,
            }
        }
        encoded_toast = base64.b64encode(
            json.dumps(toast, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).decode("ascii")
        payload = json.dumps(
            {"code": 200, "data": encoded_toast},
            separators=(",", ":"),
        ).encode("utf-8")
        return LarkAck(payload=payload, post_ack=post_ack)

    def _remember_resolved(
        self,
        token: str,
        state: str,
        *,
        event_id: str | None = None,
    ) -> None:
        self._resolved_approval_tokens[token] = state
        self._resolved_approval_tokens.move_to_end(token)
        while len(self._resolved_approval_tokens) > _RESOLVED_TOKEN_LIMIT:
            self._resolved_approval_tokens.popitem(last=False)
        if event_id is not None:
            self._resolved_card_events[event_id] = state
            self._resolved_card_events.move_to_end(event_id)
            while len(self._resolved_card_events) > _RESOLVED_TOKEN_LIMIT:
                self._resolved_card_events.popitem(last=False)

    def _timeout_result(self, request_id: str) -> ApprovalResult:
        return ApprovalResult(
            request_id=request_id,
            decision=ApprovalDecision.REJECTED,
            decided_at_ms=time_ns() // 1_000_000,
            reason="approval_timeout",
        )


def _approval_card_content(
    request: ChannelApprovalRequest,
    token: str,
    *,
    translator: Translator,
    decision: ApprovalDecision | None = None,
) -> str:
    action = approval_action_text(translator, request.approval.action)
    markdown = translator.text(
        "approval.prompt.lark",
        {
            "action": action,
            "description": approval_description_text(
                translator, request.approval.details
            ),
        },
    )
    elements: list[dict[str, object]] = [{"tag": "markdown", "content": markdown}]
    if decision is None:
        elements.append(
            {
                "tag": "column_set",
                "columns": [
                    _card_button(
                        "primary",
                        translator.text("approval.button.approve"),
                        {"action": "approve", "token": token},
                    ),
                    _card_button(
                        "danger",
                        translator.text("approval.button.reject"),
                        {"action": "reject", "token": token},
                    ),
                ],
            }
        )
    else:
        status_key = (
            "approval.card.status.approved"
            if decision is ApprovalDecision.APPROVED
            else "approval.card.status.rejected"
        )
        elements.append({"tag": "markdown", "content": translator.text(status_key)})
    card = {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {
            "template": "blue",
            "title": {
                "tag": "plain_text",
                "content": translator.text("approval.prompt.title").lstrip("#").strip(),
            },
        },
        "body": {
            "elements": elements,
        },
    }
    return json.dumps(card, ensure_ascii=False, separators=(",", ":"))


def _card_button(
    button_type: str,
    label: str,
    value: Mapping[str, str],
) -> dict[str, object]:
    return {
        "tag": "column",
        "elements": [
            {
                "tag": "button",
                "type": button_type,
                "text": {"tag": "plain_text", "content": label},
                "behaviors": [{"type": "callback", "value": dict(value)}],
            }
        ],
    }


def _parse_card_callback(
    payload: Mapping[str, object],
) -> tuple[str, str, str, str, str, str, str] | None:
    header = payload.get("header")
    event = payload.get("event")
    if not isinstance(header, Mapping) or not isinstance(event, Mapping):
        return None
    event_id = _provider_text(header.get("event_id"))
    operator = event.get("operator")
    context = event.get("context")
    action = event.get("action")
    if not isinstance(operator, Mapping) or not isinstance(context, Mapping):
        return None
    if not isinstance(action, Mapping) or action.get("tag") != "button":
        return None
    value = action.get("value")
    if not isinstance(value, Mapping):
        return None
    operator_id = _provider_text(operator.get("open_id"))
    chat_id = _provider_text(context.get("open_chat_id"))
    message_id = _provider_text(context.get("open_message_id"))
    action_name = _provider_text(value.get("action"))
    token = _provider_text(value.get("token"))
    card_update_token = _provider_text(event.get("token"))
    if (
        event_id is None
        or operator_id is None
        or chat_id is None
        or message_id is None
        or action_name is None
        or token is None
        or card_update_token is None
    ):
        return None
    return (
        event_id,
        operator_id,
        chat_id,
        message_id,
        action_name,
        token,
        card_update_token,
    )


def _provider_text(value: object) -> str | None:
    if not isinstance(value, str) or not value or "\r" in value or "\n" in value:
        return None
    return value


__all__ = ["LarkApprovalChannel"]
