from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from time import time_ns
from typing import Annotated, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError

from ..core.command import (
    ICommandService,
    InboxListResult,
    MessageSendHandoffRequired,
    SessionNotFoundError,
)
from ..core.lifecycle import TimeoutBudget
from ..core.models import InboundMessage, InboxTargetSummary, OutboundMessage


def serialize_inbound(message: InboundMessage) -> dict[str, object]:
    return {
        "seq": message.seq,
        "message_id": message.message_id,
        "session_id": message.session_id,
        "channel_session_id": message.channel_session_id,
        "channel": message.channel,
        "received_at_ms": message.received_at_ms,
        "provider_time_ms": message.provider_time_ms,
        "sender": (
            None
            if message.sender is None
            else {"id": message.sender.id, "name": message.sender.name}
        ),
        "sender_kind": message.sender_kind.value,
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
        "attachments": [
            {
                "name": attachment.name,
                "relative_path": attachment.relative_path,
                "media_type": attachment.media_type,
                "size_bytes": attachment.size_bytes,
                "sha256": attachment.sha256,
            }
            for attachment in message.attachments
        ],
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


def serialize_inbox_target(summary: InboxTargetSummary) -> dict[str, object]:
    sender = summary.latest_sender
    latest_time_ms = (
        summary.latest_provider_time_ms
        if summary.latest_provider_time_ms is not None
        else summary.latest_received_at_ms
    )
    return {
        "target": summary.target,
        "session_id": summary.session_id,
        "target_kind": summary.target_kind.value,
        "current": summary.current,
        "pending_count": summary.pending_count,
        "last_activity_at_ms": summary.last_activity_at_ms,
        "latest_message_id": summary.latest_message_id,
        "latest_sender": (
            None if sender is None else {"id": sender.id, "name": sender.name}
        ),
        "latest_time_ms": latest_time_ms,
    }


def serialize_inbox_list(result: InboxListResult) -> dict[str, object]:
    return {
        "targets": [serialize_inbox_target(target) for target in result.targets],
        "total": result.total,
        "shown": result.shown,
        "offset": result.offset,
        "has_more": result.has_more,
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


NonEmptyText = Annotated[StrictStr, Field(min_length=1)]
PositiveInt = Annotated[StrictInt, Field(gt=0)]
NonNegativeInt = Annotated[StrictInt, Field(ge=0)]


class _CommandRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    kind: Literal["command"]
    resource: StrictStr
    command: StrictStr
    session_id: NonEmptyText


class _MessageCheckRequest(_CommandRequest):
    resource: Literal["message"]
    command: Literal["check"]


class _MessageReadRequest(_CommandRequest):
    resource: Literal["message"]
    command: Literal["read"]
    target: NonEmptyText
    around_message_id: StrictStr | None = None
    limit: PositiveInt = 100


class _MessageSendRequest(_CommandRequest):
    resource: Literal["message"]
    command: Literal["send"]
    target: NonEmptyText
    body: StrictStr
    command_id: NonEmptyText
    attachment_paths: list[NonEmptyText] = Field(default_factory=list)
    reply_to_message_id: NonEmptyText | None = None
    created_at_ms: NonNegativeInt = Field(
        default_factory=lambda: time_ns() // 1_000_000
    )


class _InboxListRequest(_CommandRequest):
    resource: Literal["inbox"]
    command: Literal["list"]
    limit: PositiveInt = 100
    offset: NonNegativeInt = 0


class _ThreadUnfollowRequest(_CommandRequest):
    resource: Literal["thread"]
    command: Literal["unfollow"]
    target: NonEmptyText


_RequestModel = type[_CommandRequest]

_REQUEST_MODELS: dict[tuple[str, str], _RequestModel] = {
    ("message", "check"): _MessageCheckRequest,
    ("message", "read"): _MessageReadRequest,
    ("message", "send"): _MessageSendRequest,
    ("inbox", "list"): _InboxListRequest,
    ("thread", "unfollow"): _ThreadUnfollowRequest,
}

_REQUEST_ERRORS: dict[str, tuple[str, str]] = {
    "session_id": ("SESSION_REQUIRED", "session_id must be a non-empty string"),
    "limit": ("INVALID_LIMIT", "limit must be a positive integer"),
    "offset": ("INVALID_OFFSET", "offset must be a non-negative integer"),
    "target": ("TARGET_REQUIRED", "target must be a non-empty string"),
    "around_message_id": (
        "INVALID_AROUND_MESSAGE",
        "around_message_id must be a string",
    ),
    "body": ("BODY_REQUIRED", "body must be text"),
    "command_id": (
        "COMMAND_ID_REQUIRED",
        "command_id must be a non-empty string",
    ),
    "attachment_paths": (
        "INVALID_ATTACHMENTS",
        "attachment_paths must be a list of non-empty strings",
    ),
    "reply_to_message_id": (
        "INVALID_REPLY_TO",
        "reply_to_message_id must be a non-empty string",
    ),
    "created_at_ms": (
        "INVALID_CREATED_AT",
        "created_at_ms must be non-negative",
    ),
}


def _parse_command_request[RequestT: _CommandRequest](
    request: Mapping[str, object],
    model: type[RequestT],
    *,
    errors: Mapping[str, tuple[str, str]] | None = None,
) -> RequestT:
    try:
        return model.model_validate(request)
    except ValidationError as error:
        field_name = next(
            (
                part
                for part in error.errors()[0].get("loc", ())
                if isinstance(part, str)
            ),
            "request",
        )
        error_map = dict(_REQUEST_ERRORS)
        if errors is not None:
            error_map.update(errors)
        code, message = error_map.get(
            field_name,
            ("INVALID_COMMAND", "command request is invalid"),
        )
        raise CommandDispatchError(code, message) from error


ControlHandler = Callable[[Mapping[str, object]], Awaitable[Mapping[str, object]]]
SessionBindingValidator = Callable[[str, Mapping[str, object]], Awaitable[None]]


class CommandDispatcher:
    """Translate resource-scoped local JSON requests into core command results."""

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
        self, raw_request: Mapping[str, object]
    ) -> Mapping[str, object]:
        resource = raw_request.get("resource")
        if not isinstance(resource, str) or not resource:
            raise CommandDispatchError(
                "RESOURCE_REQUIRED", "resource must be a non-empty string"
            )
        command = raw_request.get("command")
        if not isinstance(command, str) or not command:
            raise CommandDispatchError(
                "COMMAND_REQUIRED", "command must be a non-empty string"
            )
        if resource == "message":
            if command not in {"check", "read", "send"}:
                raise CommandDispatchError(
                    "UNKNOWN_COMMAND", f"unsupported message command: {command}"
                )
        elif resource == "inbox":
            if command != "list":
                raise CommandDispatchError(
                    "UNKNOWN_COMMAND", f"unsupported inbox command: {command}"
                )
        elif resource == "thread":
            if command != "unfollow":
                raise CommandDispatchError(
                    "UNKNOWN_COMMAND", f"unsupported thread command: {command}"
                )
        else:
            raise CommandDispatchError(
                "UNKNOWN_RESOURCE", f"unsupported command resource: {resource}"
            )

        request = _parse_command_request(
            raw_request, _REQUEST_MODELS[(resource, command)]
        )
        session_id = request.session_id
        if self._session_binding_validator is not None:
            await self._session_binding_validator(session_id, raw_request)

        if resource == "message" and command == "check":
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

        if resource == "inbox" and command == "list":
            request = cast(_InboxListRequest, request)
            result = await self._service.list_inbox(
                session_id,
                limit=request.limit,
                offset=request.offset,
            )
            return {"ok": True, "result": serialize_inbox_list(result)}

        if resource == "message" and command == "read":
            request = cast(_MessageReadRequest, request)
            result = await self._service.read(
                session_id,
                target=request.target,
                around_message_id=request.around_message_id,
                limit=request.limit,
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

        if resource == "message" and command == "send":
            request = cast(_MessageSendRequest, request)
            result = await self._service.send(
                session_id=session_id,
                command_id=request.command_id,
                target=request.target,
                body=request.body,
                created_at_ms=request.created_at_ms,
                attachment_paths=tuple(request.attachment_paths),
                reply_to_message_id=request.reply_to_message_id,
            )
            if isinstance(result, MessageSendHandoffRequired):
                return {
                    "ok": True,
                    "result": {
                        "handoff_required": {"target": result.target},
                    },
                }
            return {
                "ok": True,
                "result": {"outbound": serialize_outbound(result)},
            }

        if resource == "thread" and command == "unfollow":
            request = cast(_ThreadUnfollowRequest, request)
            changed = await self._service.unfollow(session_id, target=request.target)
            return {"ok": True, "result": {"changed": changed}}

        raise AssertionError("validated command route has no handler")


def format_message_time(timestamp_ms: int) -> str:
    return (
        datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC)
        .astimezone()
        .strftime("%Y-%m-%d %H:%M:%S")
    )
