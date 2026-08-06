from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from time import time_ns

from ..core.command import ICommandService, MessageCheckResult, MessageReadResult
from ..core.lifecycle import TimeoutBudget
from ..core.models import InboundMessage, OutboundMessage
from ..core.orchestration import SessionOrchestrator


def serialize_inbound(message: InboundMessage) -> dict[str, object]:
    return {
        "seq": message.seq,
        "message_id": message.message_id,
        "short_message_id": message.message_id[:8],
        "bcn_session_id": message.bcn_session_id,
        "channel_session_id": message.channel_session_id,
        "channel_slug": message.channel_slug,
        "provider_message_id": message.provider_message_id,
        "received_at_ms": message.received_at_ms,
        "provider_time_ms": message.provider_time_ms,
        "sender_id": message.sender_id,
        "sender_display_name": message.sender_display_name,
        "message_type": message.message_type,
        "canonical_target": message.canonical_target,
        "body": message.body,
        "provider_thread_id": message.provider_thread_id,
        "reply_to_provider_message_id": message.reply_to_provider_message_id,
    }


def serialize_outbound(message: OutboundMessage) -> dict[str, object]:
    return {
        "outbound_message_id": message.outbound_message_id,
        "command_id": message.command_id,
        "bcn_session_id": message.bcn_session_id,
        "channel_session_id": message.channel_session_id,
        "target": message.target,
        "body": message.body,
        "state": message.state.value,
        "fresh_check_state": message.fresh_check_state.value,
        "created_at_ms": message.created_at_ms,
        "snapshot_seq": message.snapshot_seq,
        "current_inbound_seq": message.current_inbound_seq,
        "provider_message_id": message.provider_message_id,
        "provider_receipt_ref": message.provider_receipt_ref,
        "provider_attempted_at_ms": message.provider_attempted_at_ms,
        "completed_at_ms": message.completed_at_ms,
        "draft_saved_at_ms": message.draft_saved_at_ms,
        "error_kind": message.error_kind,
        "error_message": message.error_message,
        "next_action": message.next_action,
    }


class SessionCommandService(ICommandService):
    """Application boundary that exposes only session-scoped core commands."""

    def __init__(
        self,
        orchestrator: SessionOrchestrator,
        timeout_budget: TimeoutBudget,
    ) -> None:
        self._orchestrator = orchestrator
        self._timeout_budget = timeout_budget

    async def check(self, bcn_session_id: str, *, timeout: float) -> MessageCheckResult:
        return await self._orchestrator.check(
            bcn_session_id,
            timeout=min(timeout, self._timeout_budget.command_seconds),
        )

    async def read(
        self,
        bcn_session_id: str,
        *,
        target: str,
        around_message_id: str | None = None,
        limit: int = 100,
        timeout: float,
    ) -> MessageReadResult:
        return await self._orchestrator.read(
            bcn_session_id,
            target=target,
            around_message_id=around_message_id,
            limit=limit,
            timeout=min(timeout, self._timeout_budget.command_seconds),
        )

    async def send(
        self,
        *,
        bcn_session_id: str,
        command_id: str,
        target: str,
        body: str,
        created_at_ms: int,
        timeout: float,
    ) -> OutboundMessage:
        return await self._orchestrator.send(
            bcn_session_id=bcn_session_id,
            command_id=command_id,
            target=target,
            body=body,
            created_at_ms=created_at_ms,
            timeout=min(timeout, self._timeout_budget.command_seconds),
        )


class CommandDispatchError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        draft_saved: bool = False,
        next_action: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.draft_saved = draft_saved
        self.next_action = next_action


ControlHandler = Callable[[Mapping[str, object]], Awaitable[Mapping[str, object]]]


class CommandDispatcher:
    """Translate local JSON requests into core command results."""

    def __init__(
        self,
        service: ICommandService,
        *,
        timeout_budget: TimeoutBudget,
        control_handler: ControlHandler | None = None,
    ) -> None:
        self._service = service
        self._timeout_budget = timeout_budget
        self._control_handler = control_handler

    async def __call__(self, request: Mapping[str, object]) -> Mapping[str, object]:
        try:
            kind = request.get("kind")
            if kind == "command":
                return await self._dispatch_command(request)
            if kind == "control" and self._control_handler is not None:
                result = await self._control_handler(request)
                return {"ok": True, "result": dict(result)}
            raise CommandDispatchError(
                "INVALID_COMMAND", "request kind is not supported"
            )
        except asyncio.CancelledError:
            raise
        except CommandDispatchError as error:
            response: dict[str, object] = {
                "ok": False,
                "code": error.code,
                "error": error.message,
            }
            if error.draft_saved:
                response["draft_saved"] = True
            if error.next_action is not None:
                response["next_action"] = error.next_action
            return response
        except ValueError as error:
            return {
                "ok": False,
                "code": "INVALID_COMMAND",
                "error": str(error),
            }
        except Exception as error:  # noqa: BLE001
            return {
                "ok": False,
                "code": "COMMAND_FAILED",
                "error": str(error),
            }

    async def _dispatch_command(
        self, request: Mapping[str, object]
    ) -> Mapping[str, object]:
        session_id = request.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise CommandDispatchError(
                "SESSION_REQUIRED", "session_id must be a non-empty string"
            )
        command = request.get("command")
        if not isinstance(command, str) or not command:
            raise CommandDispatchError(
                "COMMAND_REQUIRED", "command must be a non-empty string"
            )

        if command == "check":
            result = await self._service.check(
                session_id,
                timeout=self._timeout_budget.command_seconds,
            )
            return {
                "ok": True,
                "result": {
                    "messages": [
                        serialize_inbound(message) for message in result.messages
                    ],
                    "snapshot_seq": result.snapshot_seq,
                    "delivered_through_seq": result.delivered_through_seq,
                },
            }

        if command == "read":
            target = request.get("target")
            if not isinstance(target, str) or not target:
                raise CommandDispatchError(
                    "TARGET_REQUIRED", "target must be a non-empty string"
                )
            around_message_id = request.get("around_message_id")
            if around_message_id is not None and not isinstance(around_message_id, str):
                raise CommandDispatchError(
                    "INVALID_AROUND_MESSAGE", "around_message_id must be a string"
                )
            limit = request.get("limit", 100)
            if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
                raise CommandDispatchError(
                    "INVALID_LIMIT", "limit must be a positive integer"
                )
            result = await self._service.read(
                session_id,
                target=target,
                around_message_id=around_message_id,
                limit=limit,
                timeout=self._timeout_budget.command_seconds,
            )
            return {
                "ok": True,
                "result": {
                    "messages": [
                        serialize_inbound(message) for message in result.messages
                    ],
                    "snapshot_seq": result.snapshot_seq,
                    "first_seq": result.first_seq,
                    "last_seq": result.last_seq,
                },
            }

        if command == "send":
            target = request.get("target")
            body = request.get("body")
            command_id = request.get("command_id")
            created_at_ms = request.get("created_at_ms", time_ns() // 1_000_000)
            if not isinstance(target, str) or not target:
                raise CommandDispatchError(
                    "TARGET_REQUIRED", "target must be a non-empty string"
                )
            if not isinstance(body, str):
                raise CommandDispatchError("BODY_REQUIRED", "body must be text")
            if not isinstance(command_id, str) or not command_id:
                raise CommandDispatchError(
                    "COMMAND_ID_REQUIRED", "command_id must be a non-empty string"
                )
            if (
                isinstance(created_at_ms, bool)
                or not isinstance(created_at_ms, int)
                or created_at_ms < 0
            ):
                raise CommandDispatchError(
                    "INVALID_CREATED_AT", "created_at_ms must be non-negative"
                )
            result = await self._service.send(
                bcn_session_id=session_id,
                command_id=command_id,
                target=target,
                body=body,
                created_at_ms=created_at_ms,
                timeout=self._timeout_budget.command_seconds,
            )
            return {
                "ok": True,
                "result": {"outbound": serialize_outbound(result)},
            }

        raise CommandDispatchError("UNKNOWN_COMMAND", f"unsupported command: {command}")


def format_message_time(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
