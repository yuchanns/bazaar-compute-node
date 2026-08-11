from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from time import time_ns

from ..core.command import (
    ICommandService,
    SessionNotFoundError,
)
from ..core.lifecycle import TimeoutBudget
from ..core.models import InboundMessage, OutboundMessage


def serialize_inbound(message: InboundMessage) -> dict[str, object]:
    return {
        "seq": message.seq,
        "message_id": message.message_id,
        "short_message_id": message.message_id[:8],
        "session_id": message.session_id,
        "channel_session_id": message.channel_session_id,
        "channel": message.channel,
        "received_at_ms": message.received_at_ms,
        "provider_time_ms": message.provider_time_ms,
        "sender": message.sender,
        "message_type": message.message_type,
        "canonical_target": message.canonical_target,
        "target_kind": message.target_kind.value,
        "mentions_agent": message.mentions_agent,
        "notifies_runtime": message.notifies_runtime,
        "attachments": [
            {
                "attachment_id": attachment.attachment_id,
                "name": attachment.name,
                "kind": attachment.kind,
                "state": attachment.state,
                "media_type": attachment.media_type,
                "relative_path": attachment.relative_path,
                "size_bytes": attachment.size_bytes,
                "error": attachment.error,
            }
            for attachment in message.attachments
        ],
        "body": message.body,
        "reply_to_message_id": message.reply_to_message_id,
    }


def serialize_outbound(message: OutboundMessage) -> dict[str, object]:
    return {
        "outbound_message_id": message.outbound_message_id,
        "command_id": message.command_id,
        "session_id": message.session_id,
        "channel_session_id": message.channel_session_id,
        "target": message.target,
        "reply_to_message_id": message.reply_to_message_id,
        "body": message.body,
        "state": message.state.value,
        "fresh_check_state": message.fresh_check_state.value,
        "created_at_ms": message.created_at_ms,
        "snapshot_seq": message.snapshot_seq,
        "current_inbound_seq": message.current_inbound_seq,
        "provider_attempted_at_ms": message.provider_attempted_at_ms,
        "completed_at_ms": message.completed_at_ms,
        "draft_saved_at_ms": message.draft_saved_at_ms,
        "error_kind": message.error_kind,
        "error_message": message.error_message,
        "next_action": message.next_action,
    }


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
SessionBindingValidator = Callable[[str, Mapping[str, object]], Awaitable[None]]


class CommandDispatcher:
    """Translate local JSON requests into core command results."""

    def __init__(
        self,
        service: ICommandService,
        *,
        timeout_budget: TimeoutBudget,
        control_handler: ControlHandler | None = None,
        session_binding_validator: SessionBindingValidator | None = None,
    ) -> None:
        self._service = service
        self._timeout_budget = timeout_budget
        self._control_handler = control_handler
        self._session_binding_validator = session_binding_validator
        self._accepting = False
        self._in_flight: set[asyncio.Task[object]] = set()
        self._drained = asyncio.Event()
        self._drained.set()

    @property
    def accepting(self) -> bool:
        return self._accepting

    def start_accepting(self) -> None:
        self._accepting = True
        self._drained.clear()

    def stop_accepting(self) -> None:
        self._accepting = False
        if not self._in_flight:
            self._drained.set()

    async def drain(self, *, timeout: float) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.stop_accepting()
        if not self._in_flight:
            return
        try:
            await asyncio.wait_for(asyncio.shield(self._drained.wait()), timeout)
            return
        except TimeoutError:
            pass
        current_task = asyncio.current_task()
        pending = tuple(
            task
            for task in self._in_flight
            if task is not current_task and not task.done()
        )
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def __call__(self, request: Mapping[str, object]) -> Mapping[str, object]:
        if not self._accepting:
            return {
                "ok": False,
                "code": "SERVICE_NOT_READY",
                "error": "command service is not accepting requests",
            }
        current_task = asyncio.current_task()
        if current_task is not None:
            self._in_flight.add(current_task)
            self._drained.clear()
        try:
            kind = request.get("kind")
            if kind == "command":
                async with asyncio.timeout(self._timeout_budget.command_seconds):
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
        except SessionNotFoundError as error:
            return {
                "ok": False,
                "code": "SESSION_NOT_FOUND",
                "error": str(error),
            }
        except TimeoutError as error:
            return {
                "ok": False,
                "code": "COMMAND_TIMEOUT",
                "error": str(error) or "command timed out",
            }
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
        finally:
            if current_task is not None:
                self._in_flight.discard(current_task)
                if not self._in_flight:
                    self._drained.set()

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
        if self._session_binding_validator is not None:
            await self._session_binding_validator(session_id, request)

        if command == "check":
            result = await self._service.check(session_id)
            return {
                "ok": True,
                "result": {
                    "messages": [
                        serialize_inbound(message) for message in result.messages
                    ],
                    "referenced_messages": [
                        serialize_inbound(message)
                        for message in result.referenced_messages
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
            )
            return {
                "ok": True,
                "result": {
                    "messages": [
                        serialize_inbound(message) for message in result.messages
                    ],
                    "referenced_messages": [
                        serialize_inbound(message)
                        for message in result.referenced_messages
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
            reply_to_message_id = request.get("reply_to_message_id")
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
            if reply_to_message_id is not None and (
                not isinstance(reply_to_message_id, str) or not reply_to_message_id
            ):
                raise CommandDispatchError(
                    "INVALID_REPLY_TO",
                    "reply_to_message_id must be a non-empty string",
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
                session_id=session_id,
                command_id=command_id,
                target=target,
                body=body,
                created_at_ms=created_at_ms,
                reply_to_message_id=reply_to_message_id,
            )
            return {
                "ok": True,
                "result": {"outbound": serialize_outbound(result)},
            }

        if command == "unfollow":
            target = request.get("target")
            if not isinstance(target, str) or not target:
                raise CommandDispatchError(
                    "TARGET_REQUIRED", "target must be a non-empty string"
                )
            changed = await self._service.unfollow(session_id, target=target)
            return {"ok": True, "result": {"changed": changed}}

        raise CommandDispatchError("UNKNOWN_COMMAND", f"unsupported command: {command}")


def format_message_time(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
