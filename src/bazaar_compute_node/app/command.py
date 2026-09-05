from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from time import time_ns
from typing import Annotated, Literal, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    ValidationError,
)

from ..core.actor import Actor, Actors
from ..core.command import (
    ICommandService,
    MessageSendFreshnessHold,
    TargetProjection,
    ThreadNotFoundError,
)
from ..core.lifecycle import TimeoutBudget
from ..core.models import (
    InboundAttachment,
    InboxTargetSummary,
    Message,
    MessageDirection,
    OutboundAttachment,
    OutboundDeliveryState,
)
from ..rendering import TextTemplate


def _serialize_attachment(
    attachment: InboundAttachment | OutboundAttachment,
) -> dict[str, object]:
    if isinstance(attachment, InboundAttachment):
        return {
            "attachment_id": attachment.attachment_id,
            "name": attachment.name,
            "kind": attachment.kind,
            "state": attachment.state,
            "media_type": attachment.media_type,
            "relative_path": attachment.relative_path,
            "size_bytes": attachment.size_bytes,
            "error": attachment.error,
            "sha256": None,
        }
    return {
        "attachment_id": None,
        "name": attachment.name,
        "kind": "file",
        "state": "ready",
        "media_type": attachment.media_type,
        "relative_path": attachment.relative_path,
        "size_bytes": attachment.size_bytes,
        "error": None,
        "sha256": attachment.sha256,
    }


def _in_arrival_order(messages: Iterable[Message]) -> tuple[Message, ...]:
    """Order what several threads brought by when each message arrived."""

    return tuple(
        sorted(
            messages,
            key=lambda message: (
                message.received_at_ms
                if message.received_at_ms is not None
                else message.created_at_ms or 0,
                message.thread_id,
                message.seq,
            ),
        )
    )


def _serialized(
    messages: Sequence[Message],
    target_projections: tuple[TargetProjection, ...],
) -> list[dict[str, object]]:
    return [serialize_message(message, target_projections) for message in messages]


def serialize_message(
    message: Message,
    target_projections: tuple[TargetProjection, ...] = (),
) -> dict[str, object]:
    targets = {
        projection.canonical_target: projection.display_target
        for projection in target_projections
    }
    return {
        "seq": message.seq,
        "message_id": message.message_id,
        "direction": message.direction.value,
        "thread_id": message.thread_id,
        "channel_session_id": message.channel_session_id,
        "channel": message.channel,
        "received_at_ms": message.received_at_ms,
        "created_at_ms": message.created_at_ms,
        "provider_time_ms": message.provider_time_ms,
        "sender": (
            None
            if message.sender is None
            else {
                "id": message.sender.id,
                "name": message.sender.name,
                "display_name": message.sender.display_name,
            }
        ),
        "sender_kind": message.sender_kind.value,
        "system_message_kind": (
            None
            if message.system_message_kind is None
            else message.system_message_kind.value
        ),
        "system_message_source_target": (
            targets.get(source_target, source_target)
            if isinstance(
                source_target := message.metadata.get("system_message_source_target"),
                str,
            )
            else None
        ),
        "system_message_source_message_id": message.metadata.get(
            "system_message_source_message_id"
        ),
        "message_type": message.message_type,
        "target": targets.get(message.target, message.target),
        "canonical_target": message.target,
        "target_kind": message.target_kind.value,
        "mentions_agent": message.mentions_agent,
        "notifies_runtime": message.notifies_runtime,
        "attachments": [
            _serialize_attachment(attachment) for attachment in message.attachments
        ],
        "body": message.body,
        "reply_to_message_id": message.reply_to_message_id,
        "delivery_state": (
            message.delivery_state.value
            if message.direction is MessageDirection.OUTBOUND
            and message.delivery_state is not None
            else None
        ),
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
        "thread_id": summary.thread_id,
        "target_kind": summary.target_kind.value,
        "pending_count": summary.pending_count,
        "last_activity_at_ms": summary.last_activity_at_ms,
        "latest_message_id": summary.latest_message_id,
        "latest_sender": (
            None
            if sender is None
            else {
                "id": sender.id,
                "name": sender.name,
                "display_name": sender.display_name,
            }
        ),
        "latest_time_ms": latest_time_ms,
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
    actor_id: NonEmptyText


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
    send_draft: StrictBool = False
    created_at_ms: NonNegativeInt = Field(
        default_factory=lambda: time_ns() // 1_000_000
    )


class _InboxCheckRequest(_CommandRequest):
    resource: Literal["inbox"]
    command: Literal["check"]


class _ThreadUnfollowRequest(_CommandRequest):
    resource: Literal["thread"]
    command: Literal["unfollow"]
    target: NonEmptyText


_RequestModel = type[_CommandRequest]

_REQUEST_MODELS: dict[tuple[str, str], _RequestModel] = {
    ("message", "check"): _MessageCheckRequest,
    ("message", "read"): _MessageReadRequest,
    ("message", "send"): _MessageSendRequest,
    ("inbox", "check"): _InboxCheckRequest,
    ("thread", "unfollow"): _ThreadUnfollowRequest,
}

_SEND_FAILURES: dict[OutboundDeliveryState, tuple[str, str, str]] = {
    OutboundDeliveryState.PARTIAL: (
        "SEND_PARTIAL",
        "Message delivery was only partially confirmed.",
        (
            "Do not retry the complete message automatically; reconcile confirmed "
            "delivery first."
        ),
    ),
    OutboundDeliveryState.UNKNOWN: (
        "SEND_UNKNOWN",
        "Message delivery outcome is unknown.",
        "Reconcile channel delivery before retrying.",
    ),
    OutboundDeliveryState.FAILED: (
        "SEND_FAILED",
        "Message delivery failed.",
        "Fix the provider error before retrying.",
    ),
}

_REQUEST_ERRORS: dict[str, tuple[str, str]] = {
    "actor_id": ("SESSION_REQUIRED", "actor_id must be a non-empty string"),
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


SessionBindingValidator = Callable[[Actor, Mapping[str, object]], Awaitable[None]]


class CommandDispatcher:
    """Translate resource-scoped local JSON requests into core command results."""

    def __init__(
        self,
        service: ICommandService,
        *,
        actors: Actors,
        timeout_budget: TimeoutBudget,
        session_binding_validator: SessionBindingValidator | None = None,
    ) -> None:
        self._actors = actors
        self._service = service
        self._timeout_budget = timeout_budget
        self._session_binding_validator = session_binding_validator
        self._accepting = False
        self._in_flight: set[asyncio.Task[object]] = set()
        self._drained = asyncio.Event()
        self._drained.set()

    def _resolve_actor(self, actor_id: str) -> Actor:
        try:
            return self._actors.resolve(actor_id)
        except ValueError as error:
            raise CommandDispatchError("SESSION_NOT_FOUND", str(error)) from error

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

    def _command_timeout(self, request: Mapping[str, object]) -> float | None:
        del request
        return self._timeout_budget.command_seconds

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
                async with asyncio.timeout(self._command_timeout(request)):
                    return await self._dispatch_command(request)
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
        except ThreadNotFoundError as error:
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
        if resource not in {known for known, _ in _REQUEST_MODELS}:
            raise CommandDispatchError(
                "UNKNOWN_RESOURCE", f"unsupported command resource: {resource}"
            )
        route = (resource, command)
        if route not in _REQUEST_MODELS:
            raise CommandDispatchError(
                "UNKNOWN_COMMAND", f"unsupported {resource} command: {command}"
            )

        request = _parse_command_request(raw_request, _REQUEST_MODELS[route])
        actor = self._resolve_actor(request.actor_id)
        if self._session_binding_validator is not None:
            await self._session_binding_validator(actor, raw_request)

        match route:
            case ("message", "check"):
                return await self._check_messages(actor)
            case ("message", "read"):
                return await self._read_messages(
                    actor, cast(_MessageReadRequest, request)
                )
            case ("message", "send"):
                return await self._send_message(
                    actor, cast(_MessageSendRequest, request)
                )
            case ("inbox", "check"):
                return await self._check_inbox(actor)
            case ("thread", "unfollow"):
                return await self._unfollow_thread(
                    actor, cast(_ThreadUnfollowRequest, request)
                )
            case _:
                raise AssertionError("validated command route has no handler")

    async def _check_messages(self, actor: Actor) -> Mapping[str, object]:
        drained = await self._service.check(actor)
        projections = tuple(
            projection for result in drained for projection in result.target_projections
        )
        return {
            "ok": True,
            "result": {
                "messages": _serialized(
                    _in_arrival_order(
                        message for result in drained for message in result.messages
                    ),
                    projections,
                ),
                "referenced_messages": _serialized(
                    tuple(
                        message
                        for result in drained
                        for message in result.referenced_messages
                    ),
                    projections,
                ),
            },
        }

    async def _check_inbox(self, actor: Actor) -> Mapping[str, object]:
        result = await self._service.pending_targets(actor)
        return {
            "ok": True,
            "result": {
                "targets": [serialize_inbox_target(target) for target in result.targets]
            },
        }

    async def _read_messages(
        self, actor: Actor, request: _MessageReadRequest
    ) -> Mapping[str, object]:
        result = await self._service.read(
            actor,
            raw_target=request.target,
            around_message_id=request.around_message_id,
            limit=request.limit,
        )
        return {
            "ok": True,
            "result": {
                "messages": _serialized(result.messages, result.target_projections),
                "referenced_messages": _serialized(
                    result.referenced_messages, result.target_projections
                ),
                "snapshot_seq": result.snapshot_seq,
                "first_seq": result.first_seq,
                "last_seq": result.last_seq,
            },
        }

    async def _send_message(
        self, actor: Actor, request: _MessageSendRequest
    ) -> Mapping[str, object]:
        result = await self._service.send(
            actor=actor,
            command_id=request.command_id,
            raw_target=request.target,
            body=request.body,
            created_at_ms=request.created_at_ms,
            attachment_paths=tuple(request.attachment_paths),
            reply_to_message_id=request.reply_to_message_id,
            send_draft=request.send_draft,
        )
        if isinstance(result, MessageSendFreshnessHold):
            return {"ok": True, "result": {"text": format_freshness_hold(result)}}
        message = result.message
        delivery_state = message.delivery_state
        if delivery_state is None:
            raise RuntimeError("outbound message has no delivery state")
        if delivery_state not in {
            OutboundDeliveryState.SENT,
            OutboundDeliveryState.QUEUED,
        }:
            failure = _SEND_FAILURES.get(delivery_state)
            if failure is None:
                raise AssertionError(
                    f"message send returned unsupported state: {delivery_state.value}"
                )
            code, fallback, next_action = failure
            raise CommandDispatchError(
                code,
                message.error_message or fallback,
                next_action=next_action,
            )
        text = _SEND_RESULT.render(
            {
                "delivery_state": delivery_state.value,
                "target": result.target,
                "message_id": message.message_id,
            }
        )
        return {"ok": True, "result": {"text": text}}

    async def _unfollow_thread(
        self, actor: Actor, request: _ThreadUnfollowRequest
    ) -> Mapping[str, object]:
        result = await self._service.unfollow(actor, raw_target=request.target)
        return {
            "ok": True,
            "result": {"target": result.target, "changed": result.changed},
        }


def format_message_time(timestamp_ms: int) -> str:
    return (
        datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC)
        .astimezone()
        .strftime("%Y-%m-%d %H:%M:%S")
    )


_ATTACHMENT_SUFFIX = TextTemplate.from_resource("command/attachment_suffix.tpl")
_SENDER = TextTemplate.from_resource("command/sender.tpl")
_CHECK_MESSAGE = TextTemplate.from_resource("command/check_message.tpl")
_READ_MESSAGE = TextTemplate.from_resource("command/read_message.tpl")
_SEND_RESULT = TextTemplate.from_resource("command/send_result.tpl")
_FRESHNESS_HOLD = TextTemplate.from_resource("command/freshness_hold.tpl")


def _message_timestamp(message: Mapping[str, object]) -> int:
    for field_name in ("provider_time_ms", "received_at_ms", "created_at_ms"):
        timestamp = message.get(field_name)
        if timestamp is not None:
            return cast(int, timestamp)
    raise ValueError("message has no display timestamp")


def _message_header_fields(
    message: Mapping[str, object],
) -> tuple[str, str, str, str, str | None, str]:
    target = cast(str, message["target"])
    message_id = cast(str, message["message_id"])
    sender_kind = cast(str, message["sender_kind"])
    sender_value = message["sender"]
    sender: str | None = None
    if sender_value is not None:
        sender_mapping = cast(Mapping[str, object], sender_value)
        sender = (
            _SENDER.render(
                {
                    "sender_id": cast(str | None, sender_mapping.get("id")),
                    "sender_name": cast(str | None, sender_mapping.get("name")),
                    "sender_display_name": cast(
                        str | None, sender_mapping.get("display_name")
                    ),
                }
            )
            or None
        )
    return (
        target,
        message_id,
        format_message_time(_message_timestamp(message)),
        sender_kind,
        sender,
        cast(str, message["body"]),
    )


def _attachment_suffix(message: Mapping[str, object]) -> str:
    attachments = cast(list[Mapping[str, object]], message["attachments"])
    return _ATTACHMENT_SUFFIX.render(
        {
            "attachments": [
                {
                    "name": cast(str, attachment["name"]),
                    "attachment_id": cast(str | None, attachment.get("attachment_id")),
                    "state": cast(str, attachment["state"]),
                    "path": attachment.get("relative_path"),
                    "error": attachment.get("error"),
                }
                for attachment in attachments
            ]
        }
    )


def format_check_message(message: Mapping[str, object]) -> str:
    target, message_id, timestamp, sender_kind, sender, body = _message_header_fields(
        message
    )
    source_target = message.get("system_message_source_target")
    source_message_id = message.get("system_message_source_message_id")
    return _CHECK_MESSAGE.render(
        {
            "target": target,
            "message_id": message_id,
            "timestamp": timestamp,
            "sender_kind": sender_kind,
            "reply_to_message_id": message["reply_to_message_id"],
            "sender": sender,
            "body": body,
            "attachment_suffix": _attachment_suffix(message),
            "system_message_kind": message.get("system_message_kind"),
            # a repeating reminder is the only one that can still be updated
            "repeats": "\nNext iteration: " in body,
            "source_target": (
                json.dumps(cast(str, source_target))
                if source_target is not None
                else None
            ),
            "source_message_id": (
                json.dumps(cast(str, source_message_id))
                if source_message_id is not None
                else None
            ),
        }
    )


def format_read_message(
    message: Mapping[str, object],
    *,
    index: int,
    count: int,
) -> str:
    target, message_id, timestamp, sender_kind, sender, body = _message_header_fields(
        message
    )
    return _READ_MESSAGE.render(
        {
            "index": index,
            "count": count,
            "seq": cast(int, message["seq"]),
            "message_id": message_id,
            "timestamp": timestamp,
            "sender_kind": sender_kind,
            "target": target,
            "reply_to_message_id": message["reply_to_message_id"],
            "sender": sender,
            "body": body,
            "attachment_suffix": _attachment_suffix(message),
        }
    )


def format_freshness_hold(result: MessageSendFreshnessHold) -> str:
    messages = [
        serialize_message(message, result.target_projections)
        for message in result.messages
    ]
    referenced_messages = [
        serialize_message(message, result.target_projections)
        for message in result.referenced_messages
    ]
    shown = len(messages)
    return _FRESHNESS_HOLD.render(
        {
            "total": result.newer_message_total,
            "shown": shown,
            "first_seq": messages[0]["seq"] if messages else None,
            "last_seq": messages[-1]["seq"] if messages else None,
            "target": json.dumps(result.target),
            "referenced_lines": [
                format_read_message(
                    message, index=index, count=len(referenced_messages)
                )
                for index, message in enumerate(referenced_messages, start=1)
            ],
            "message_lines": [
                format_read_message(message, index=index, count=shown)
                for index, message in enumerate(messages, start=1)
            ],
        }
    )
