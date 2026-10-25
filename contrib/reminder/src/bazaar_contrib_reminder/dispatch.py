from __future__ import annotations

import asyncio
from collections.abc import Mapping
from enum import Enum, auto
from math import ceil
from time import time_ns
from typing import Annotated, Literal, cast

from bazaar_contrib_messages import Actor, Actors, SessionBindingValidator
from bazaar_contrib_messages.results import (
    MessageSearchTimeoutError,
    ThreadNotFoundError,
)
from bazaar_contrib_timer import Timer, wait_for
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    ValidationError,
)

from .commands import ReminderCommandFailure, ReminderCommands
from .models import Reminder, ReminderState
from .reminder import (
    ReminderCancelRequest,
    ReminderListRequest,
    ReminderScheduleRequest,
    ReminderSnoozeRequest,
    ReminderUpdateRequest,
)
from .serialize import serialize_reminder


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


class _CommandRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    kind: Literal["command"]
    resource: StrictStr
    command: StrictStr
    actor_id: NonEmptyText


_SCHEDULE_ERROR_CODES = (
    ("title", "REMINDER_TITLE_REQUIRED"),
    ("timezone", "REMINDER_TIMEZONE_INVALID"),
    ("repeat", "REMINDER_REPEAT_INVALID"),
    ("weekday", "REMINDER_REPEAT_INVALID"),
    ("id reference", "REMINDER_ANCHOR_NOT_FOUND"),
    ("uuid", "REMINDER_ANCHOR_NOT_FOUND"),
)


def _listed_statuses(
    all_statuses: bool, status_text: str | None
) -> frozenset[ReminderState]:
    """Read which Reminder states a list request asked for."""

    if all_statuses and status_text is not None:
        raise CommandDispatchError(
            "INVALID_COMMAND",
            "--all and --status cannot be used together",
        )
    if all_statuses:
        return frozenset(ReminderState)
    if status_text is None:
        return ReminderListRequest().statuses
    try:
        statuses = frozenset(
            ReminderState(value.strip())
            for value in status_text.split(",")
            if value.strip()
        )
    except ValueError as error:
        raise CommandDispatchError(
            "INVALID_COMMAND",
            f"invalid Reminder status list: {status_text}",
        ) from error
    if not statuses:
        raise CommandDispatchError(
            "INVALID_COMMAND",
            "--status must contain at least one Reminder status",
        )
    return statuses


def _reminder_payload(reminder: Reminder) -> Mapping[str, object]:
    return {"ok": True, "result": {"reminder": serialize_reminder(reminder)}}


class _ReminderScheduleRequest(_CommandRequest):
    resource: Literal["reminder"]
    command: Literal["schedule"]
    title: NonEmptyText
    message_id: NonEmptyText
    delay_seconds: PositiveInt | None = None
    fire_at: NonEmptyText | None = None
    repeat_rule: NonEmptyText | None = None
    timezone: NonEmptyText | None = None


class _ReminderListRequest(_CommandRequest):
    resource: Literal["reminder"]
    command: Literal["list"]
    all: StrictBool = False
    status: NonEmptyText | None = None


class _ReminderSnoozeRequest(_CommandRequest):
    resource: Literal["reminder"]
    command: Literal["snooze"]
    reminder_id: NonEmptyText
    by: NonEmptyText


class _ReminderUpdateRequest(_CommandRequest):
    resource: Literal["reminder"]
    command: Literal["update"]
    reminder_id: NonEmptyText
    fire_at: NonEmptyText | None = None
    in_duration: NonEmptyText | None = Field(default=None, alias="in")
    cadence: NonEmptyText | None = None
    title: NonEmptyText | None = None


class _ReminderCancelRequest(_CommandRequest):
    resource: Literal["reminder"]
    command: Literal["cancel"]
    reminder_id: NonEmptyText


_REMINDER_REQUESTS: dict[
    str, tuple[type[_CommandRequest], Mapping[str, tuple[str, str]]]
] = {
    "schedule": (
        _ReminderScheduleRequest,
        {
            "title": (
                "REMINDER_TITLE_REQUIRED",
                "Reminder title must be a non-empty string.",
            ),
            "message_id": (
                "REMINDER_ANCHOR_REQUIRED",
                "Reminder --message-id is required.",
            ),
            "delay_seconds": (
                "REMINDER_TIME_REQUIRED",
                "--delay-seconds must be a positive integer.",
            ),
            "fire_at": (
                "REMINDER_TIME_REQUIRED",
                "--fire-at must be a non-empty ISO-8601 timestamp.",
            ),
            "repeat_rule": (
                "REMINDER_REPEAT_INVALID",
                "--repeat must be a non-empty recurrence rule.",
            ),
            "timezone": (
                "REMINDER_TIMEZONE_INVALID",
                "--tz must be a non-empty IANA timezone.",
            ),
        },
    ),
    "list": (
        _ReminderListRequest,
        {
            "all": ("INVALID_COMMAND", "reminder list all flag must be boolean"),
            "status": (
                "INVALID_COMMAND",
                "--status must be a non-empty comma-separated status list",
            ),
        },
    ),
    "snooze": (
        _ReminderSnoozeRequest,
        {
            "reminder_id": ("REMINDER_NOT_FOUND", "Reminder --id is required."),
            "by": (
                "INVALID_COMMAND",
                "Reminder snooze requires --by <duration>.",
            ),
        },
    ),
    "update": (
        _ReminderUpdateRequest,
        {
            "reminder_id": ("REMINDER_NOT_FOUND", "Reminder --id is required."),
            "fire_at": (
                "REMINDER_UPDATE_FAILED",
                "Reminder update fire_at must be non-empty text.",
            ),
            "in": (
                "REMINDER_UPDATE_FAILED",
                "Reminder update in must be non-empty text.",
            ),
            "in_duration": (
                "REMINDER_UPDATE_FAILED",
                "Reminder update in must be non-empty text.",
            ),
            "cadence": (
                "REMINDER_UPDATE_FAILED",
                "Reminder update cadence must be non-empty text.",
            ),
            "title": (
                "REMINDER_UPDATE_FAILED",
                "Reminder update title must be non-empty text.",
            ),
        },
    ),
    "cancel": (
        _ReminderCancelRequest,
        {"reminder_id": ("REMINDER_NOT_FOUND", "Reminder --id is required.")},
    ),
}

_REQUEST_ERRORS = {
    "actor_id": ("SESSION_REQUIRED", "actor_id must be a non-empty string")
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


class _State(Enum):
    STOPPED = auto()
    ACCEPTING = auto()


class CommandDispatcher:
    """Translate Reminder command requests within the active agent binding."""

    def __init__(
        self,
        service: ReminderCommands,
        *,
        actors: Actors,
        timer: Timer,
        session_binding_validator: SessionBindingValidator | None = None,
    ) -> None:
        self._actors = actors
        self._timer = timer
        self._service = service
        self._session_binding_validator = session_binding_validator
        self._state = _State.STOPPED
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
        return self._state is _State.ACCEPTING

    def start_accepting(self) -> None:
        self._state = _State.ACCEPTING
        self._drained.clear()

    def stop_accepting(self) -> None:
        self._state = _State.STOPPED
        if not self._in_flight:
            self._drained.set()

    async def drain(self, *, timeout: float) -> None:  # noqa: ASYNC109
        if timeout < 0:
            raise ValueError("timeout must be non-negative")
        self.stop_accepting()
        if not self._in_flight:
            return
        try:
            await wait_for(
                self._timer,
                asyncio.shield(self._drained.wait()),
                ceil(timeout * 1000),
            )
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
        if self._state is not _State.ACCEPTING:
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
            # a command takes as long as its work takes: giving up on one
            # whose work carries on regardless only hides what became of it
            if kind == "command":
                return await self._dispatch_command(request)
            raise CommandDispatchError(
                "INVALID_COMMAND", "request kind is not supported"
            )
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
        except MessageSearchTimeoutError as error:
            return {"ok": False, "code": error.code, "error": str(error)}
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
        self,
        raw_request: Mapping[str, object],
    ) -> Mapping[str, object]:
        resource = raw_request.get("resource")
        if not isinstance(resource, str) or not resource:
            raise CommandDispatchError(
                "RESOURCE_REQUIRED",
                "resource must be a non-empty string",
            )
        command = raw_request.get("command")
        if not isinstance(command, str) or not command:
            raise CommandDispatchError(
                "COMMAND_REQUIRED",
                "command must be a non-empty string",
            )

        if resource != "reminder":
            raise CommandDispatchError(
                "UNKNOWN_RESOURCE",
                f"unsupported command resource: {resource}",
            )

        request_spec = _REMINDER_REQUESTS.get(command)
        if request_spec is None:
            raise CommandDispatchError(
                "UNKNOWN_COMMAND",
                f"unsupported reminder command: {command}",
            )
        request_model, request_errors = request_spec
        request = _parse_command_request(
            raw_request,
            request_model,
            errors=request_errors,
        )
        actor = self._resolve_actor(request.actor_id)
        if self._session_binding_validator is not None:
            await self._session_binding_validator(actor, raw_request)

        try:
            return await self._dispatch_reminder(actor, command, request)
        except ReminderCommandFailure as error:
            raise CommandDispatchError(
                error.code,
                error.message,
                next_action=error.next_action,
            ) from error

    async def _dispatch_reminder(
        self,
        actor: Actor,
        command: str,
        parsed_request: _CommandRequest,
    ) -> Mapping[str, object]:
        now_ms = time_ns() // 1_000_000
        match command:
            case "schedule":
                return await self._schedule_reminder(
                    actor, cast(_ReminderScheduleRequest, parsed_request), now_ms
                )
            case "list":
                return await self._list_reminders(
                    actor, cast(_ReminderListRequest, parsed_request)
                )
            case "snooze":
                return await self._snooze_reminder(
                    actor, cast(_ReminderSnoozeRequest, parsed_request), now_ms
                )
            case "update":
                return await self._update_reminder(
                    actor, cast(_ReminderUpdateRequest, parsed_request), now_ms
                )
            case "cancel":
                return await self._cancel_reminder(
                    actor, cast(_ReminderCancelRequest, parsed_request), now_ms
                )
            case _:
                raise CommandDispatchError(
                    "UNKNOWN_COMMAND",
                    f"unsupported reminder command: {command}",
                )

    async def _schedule_reminder(
        self,
        actor: Actor,
        request_values: _ReminderScheduleRequest,
        now_ms: int,
    ) -> Mapping[str, object]:
        delay_seconds = request_values.delay_seconds
        fire_at = request_values.fire_at
        repeat_rule = request_values.repeat_rule
        if delay_seconds is not None and fire_at is not None:
            raise CommandDispatchError(
                "REMINDER_TIME_CONFLICT",
                "--delay-seconds and --fire-at are mutually exclusive.",
            )
        if delay_seconds is None and fire_at is None and repeat_rule is None:
            raise CommandDispatchError(
                "REMINDER_TIME_REQUIRED",
                "A Reminder requires --delay-seconds, --fire-at, or --repeat.",
            )
        try:
            request = await asyncio.to_thread(
                ReminderScheduleRequest.from_options,
                title=request_values.title,
                message_id=request_values.message_id,
                evaluated_at_ms=now_ms,
                delay_seconds=delay_seconds,
                fire_at=fire_at,
                repeat_rule=repeat_rule,
                timezone=request_values.timezone,
            )
        except ValueError as error:
            lowered = str(error).casefold()
            raise CommandDispatchError(
                next(
                    (code for token, code in _SCHEDULE_ERROR_CODES if token in lowered),
                    "REMINDER_TIME_REQUIRED",
                ),
                str(error),
            ) from error
        result = await self._service.schedule_reminder(actor, request)
        return _reminder_payload(result.reminder)

    async def _list_reminders(
        self,
        actor: Actor,
        request_values: _ReminderListRequest,
    ) -> Mapping[str, object]:
        result = await self._service.list_reminders(
            actor,
            ReminderListRequest(
                statuses=_listed_statuses(request_values.all, request_values.status)
            ),
        )
        return {
            "ok": True,
            "result": {
                "reminders": [
                    serialize_reminder(reminder) for reminder in result.reminders
                ]
            },
        }

    async def _snooze_reminder(
        self,
        actor: Actor,
        request_values: _ReminderSnoozeRequest,
        now_ms: int,
    ) -> Mapping[str, object]:
        try:
            request = ReminderSnoozeRequest.from_options(
                reminder_id=request_values.reminder_id,
                duration=request_values.by,
                evaluated_at_ms=now_ms,
            )
        except ValueError as error:
            code = (
                "REMINDER_NOT_FOUND" if self._is_id_error(error) else "INVALID_COMMAND"
            )
            raise CommandDispatchError(code, str(error)) from error
        result = await self._service.snooze_reminder(actor, request)
        return _reminder_payload(result.reminder)

    async def _update_reminder(
        self,
        actor: Actor,
        request_values: _ReminderUpdateRequest,
        now_ms: int,
    ) -> Mapping[str, object]:
        try:
            request = ReminderUpdateRequest.from_options(
                reminder_id=request_values.reminder_id,
                evaluated_at_ms=now_ms,
                fire_at=request_values.fire_at,
                in_duration=request_values.in_duration,
                cadence=request_values.cadence,
                title=request_values.title,
            )
        except ValueError as error:
            if self._is_id_error(error):
                code = "REMINDER_NOT_FOUND"
            elif request_values.cadence is not None:
                code = "REMINDER_REPEAT_INVALID"
            else:
                code = "REMINDER_UPDATE_FAILED"
            raise CommandDispatchError(code, str(error)) from error
        result = await self._service.update_reminder(actor, request)
        return _reminder_payload(result.reminder)

    async def _cancel_reminder(
        self,
        actor: Actor,
        request_values: _ReminderCancelRequest,
        now_ms: int,
    ) -> Mapping[str, object]:
        try:
            request = ReminderCancelRequest(
                reminder_id=request_values.reminder_id,
                evaluated_at_ms=now_ms,
            )
        except ValueError as error:
            raise CommandDispatchError("REMINDER_NOT_FOUND", str(error)) from error
        result = await self._service.cancel_reminder(actor, request)
        return _reminder_payload(result.reminder)

    @staticmethod
    def _is_id_error(error: ValueError) -> bool:
        lowered = str(error).casefold()
        return "id reference" in lowered or "uuid" in lowered
