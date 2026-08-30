from __future__ import annotations

import logging
from collections.abc import Mapping
from time import time_ns
from typing import Annotated, Literal, cast

from pydantic import Field, StrictBool, StrictInt, StrictStr

from ..core.command import ICommandService, IReminderService
from ..core.lifecycle import TimeoutBudget
from ..core.models import Reminder, ReminderState
from ..core.orchestration.reminder_command import ReminderCommandFailure
from ..core.reminder import (
    ReminderCancelRequest,
    ReminderListRequest,
    ReminderScheduleRequest,
    ReminderSnoozeRequest,
    ReminderUpdateRequest,
)
from .command import CommandDispatcher as _MessageCommandDispatcher
from .command import (
    CommandDispatchError,
    SessionBindingValidator,
    _CommandRequest,
    _parse_command_request,
)
from .upgrade import UpgradeError, UpgradeService, UpgradeUnavailable


def serialize_reminder(reminder: Reminder) -> dict[str, object]:
    return {
        "reminder_id": reminder.reminder_id,
        "owner_session_id": reminder.owner_session_id,
        "anchor_message_id": reminder.anchor_message_id,
        "title": reminder.title,
        "state": reminder.state.value,
        "next_fire_at_ms": reminder.next_fire_at_ms,
        "repeat_rule": reminder.repeat_rule,
        "timezone": reminder.timezone,
        "revision": reminder.revision,
        "last_occurrence_no": reminder.last_occurrence_no,
        "created_at_ms": reminder.created_at_ms,
        "updated_at_ms": reminder.updated_at_ms,
        "last_fired_at_ms": reminder.last_fired_at_ms,
        "canceled_at_ms": reminder.canceled_at_ms,
    }


NonEmptyText = Annotated[StrictStr, Field(min_length=1)]
# long enough for the node to be back before the Agent is asked to report
_UPGRADE_FOLLOW_UP_SECONDS = 60
PositiveInt = Annotated[StrictInt, Field(gt=0)]


class _NodeUpgradeRequest(_CommandRequest):
    resource: Literal["node"]
    command: Literal["upgrade"]
    message_id: NonEmptyText


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


class CommandDispatcher(_MessageCommandDispatcher):
    """Resource-aware dispatcher for all local Agent commands."""

    def __init__(
        self,
        service: ICommandService,
        *,
        reminder_service: IReminderService,
        timeout_budget: TimeoutBudget,
        session_binding_validator: SessionBindingValidator | None = None,
        upgrade_service: UpgradeService | None,
    ) -> None:
        super().__init__(
            service,
            timeout_budget=timeout_budget,
            session_binding_validator=session_binding_validator,
        )
        self._reminder_service = reminder_service
        self._upgrade_service = upgrade_service
        self._logger = logging.getLogger("bazaar_compute_node.application.upgrade")

    def _command_timeout(self, request: Mapping[str, object]) -> float | None:
        # installing a release takes as long as it takes; nothing is served by
        # giving up on a command whose work would carry on regardless
        if request.get("resource") == "node":
            return None
        return super()._command_timeout(request)

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

        if resource == "message":
            if command not in {"check", "read", "send"}:
                raise CommandDispatchError(
                    "UNKNOWN_COMMAND",
                    f"unsupported message command: {command}",
                )
            return await super()._dispatch_command(raw_request)
        if resource == "thread":
            if command != "unfollow":
                raise CommandDispatchError(
                    "UNKNOWN_COMMAND",
                    f"unsupported thread command: {command}",
                )
            return await super()._dispatch_command(raw_request)
        if resource == "inbox":
            return await super()._dispatch_command(raw_request)
        if resource == "node":
            upgrade_service = self._upgrade_service
            if upgrade_service is None:
                # nothing on this platform would bring the node back after an
                # upgrade exits it, so the Agent is given no upgrade to run
                raise CommandDispatchError(
                    "UNKNOWN_RESOURCE",
                    f"unsupported command resource: {resource}",
                )
            if command not in {"upgrade", "version"}:
                raise CommandDispatchError(
                    "UNKNOWN_COMMAND",
                    f"unsupported node command: {command}",
                )
            if command == "version":
                return {
                    "ok": True,
                    "result": {
                        "version": upgrade_service.installed_version,
                    },
                }
            request = _parse_command_request(
                raw_request,
                _NodeUpgradeRequest,
                errors={
                    "message_id": (
                        "UPGRADE_ANCHOR_REQUIRED",
                        "Upgrade requires --message-id.",
                    )
                },
            )
            session_id = request.session_id
            if self._session_binding_validator is not None:
                await self._session_binding_validator(session_id, raw_request)
            return await self._dispatch_upgrade(
                session_id,
                cast(_NodeUpgradeRequest, request),
                upgrade_service,
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
        session_id = request.session_id
        if self._session_binding_validator is not None:
            await self._session_binding_validator(session_id, raw_request)

        try:
            return await self._dispatch_reminder(session_id, command, request)
        except ReminderCommandFailure as error:
            raise CommandDispatchError(
                error.code,
                error.message,
                next_action=error.next_action,
            ) from error

    async def _dispatch_upgrade(
        self,
        session_id: str,
        request: _NodeUpgradeRequest,
        upgrade_service: UpgradeService,
    ) -> Mapping[str, object]:
        async def wake_after(upgrade_version: str) -> str | None:
            # the reminder is how the Agent is prompted to check the node over
            # once it is back; the release is already installed by now, so
            # failing to schedule one is a worse reason to leave the node
            # un-restarted than to go on without it
            try:
                result = await self._reminder_service.schedule(
                    session_id,
                    ReminderScheduleRequest.from_options(
                        title=(f"Report the outcome of upgrading to {upgrade_version}"),
                        message_id=request.message_id,
                        evaluated_at_ms=time_ns() // 1_000_000,
                        delay_seconds=_UPGRADE_FOLLOW_UP_SECONDS,
                    ),
                )
            except ReminderCommandFailure:
                self._logger.warning(
                    "the upgrade follow-up could not be scheduled", exc_info=True
                )
                return None
            return result.reminder.reminder_id

        try:
            upgrade_version, reminder_id = await upgrade_service.upgrade(
                wake_after=wake_after,
            )
        except UpgradeUnavailable as error:
            raise CommandDispatchError(
                "UPGRADE_NOT_AVAILABLE",
                str(error),
                next_action="Wait for the inbox notice to name one.",
            ) from error
        except UpgradeError as error:
            raise CommandDispatchError(
                "UPGRADE_INSTALL_FAILED",
                str(error),
                next_action=(
                    "Tell the user the node keeps running on the installed version."
                ),
            ) from error
        return {
            "ok": True,
            "result": {
                "installed_version": upgrade_service.installed_version,
                "upgrade_version": upgrade_version,
                "reminder_id": reminder_id,
            },
        }

    async def _dispatch_reminder(
        self,
        session_id: str,
        command: str,
        parsed_request: _CommandRequest,
    ) -> Mapping[str, object]:
        now_ms = time_ns() // 1_000_000
        if command == "schedule":
            request_values = cast(_ReminderScheduleRequest, parsed_request)
            title = request_values.title
            message_id = request_values.message_id
            delay_seconds = request_values.delay_seconds
            fire_at = request_values.fire_at
            repeat_rule = request_values.repeat_rule
            timezone = request_values.timezone
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
                request = ReminderScheduleRequest.from_options(
                    title=title,
                    message_id=message_id,
                    evaluated_at_ms=now_ms,
                    delay_seconds=delay_seconds,
                    fire_at=fire_at,
                    repeat_rule=repeat_rule,
                    timezone=timezone,
                )
            except ValueError as error:
                message = str(error)
                lowered = message.casefold()
                if "title" in lowered:
                    code = "REMINDER_TITLE_REQUIRED"
                elif "timezone" in lowered:
                    code = "REMINDER_TIMEZONE_INVALID"
                elif "repeat" in lowered or "weekday" in lowered:
                    code = "REMINDER_REPEAT_INVALID"
                elif "id reference" in lowered or "uuid" in lowered:
                    code = "REMINDER_ANCHOR_NOT_FOUND"
                else:
                    code = "REMINDER_TIME_REQUIRED"
                raise CommandDispatchError(code, message) from error
            result = await self._reminder_service.schedule(session_id, request)
            return {
                "ok": True,
                "result": {"reminder": serialize_reminder(result.reminder)},
            }

        if command == "list":
            request_values = cast(_ReminderListRequest, parsed_request)
            all_statuses = request_values.all
            status_text = request_values.status
            if all_statuses and status_text is not None:
                raise CommandDispatchError(
                    "INVALID_COMMAND",
                    "--all and --status cannot be used together",
                )
            if all_statuses:
                statuses = frozenset(ReminderState)
            elif status_text is None:
                statuses = ReminderListRequest().statuses
            else:
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
            result = await self._reminder_service.list(
                session_id,
                ReminderListRequest(statuses=statuses),
            )
            return {
                "ok": True,
                "result": {
                    "reminders": [
                        serialize_reminder(reminder) for reminder in result.reminders
                    ]
                },
            }

        if command == "snooze":
            request_values = cast(_ReminderSnoozeRequest, parsed_request)
            try:
                request = ReminderSnoozeRequest.from_options(
                    reminder_id=request_values.reminder_id,
                    duration=request_values.by,
                    evaluated_at_ms=now_ms,
                )
            except ValueError as error:
                code = (
                    "REMINDER_NOT_FOUND"
                    if self._is_id_error(error)
                    else "INVALID_COMMAND"
                )
                raise CommandDispatchError(code, str(error)) from error
            result = await self._reminder_service.snooze(session_id, request)
            return {
                "ok": True,
                "result": {"reminder": serialize_reminder(result.reminder)},
            }

        if command == "update":
            request_values = cast(_ReminderUpdateRequest, parsed_request)
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
            result = await self._reminder_service.update(session_id, request)
            return {
                "ok": True,
                "result": {"reminder": serialize_reminder(result.reminder)},
            }

        if command == "cancel":
            request_values = cast(_ReminderCancelRequest, parsed_request)
            try:
                request = ReminderCancelRequest(
                    reminder_id=request_values.reminder_id,
                    evaluated_at_ms=now_ms,
                )
            except ValueError as error:
                raise CommandDispatchError("REMINDER_NOT_FOUND", str(error)) from error
            result = await self._reminder_service.cancel(session_id, request)
            return {
                "ok": True,
                "result": {"reminder": serialize_reminder(result.reminder)},
            }

        raise CommandDispatchError(
            "UNKNOWN_COMMAND",
            f"unsupported reminder command: {command}",
        )

    @staticmethod
    def _is_id_error(error: ValueError) -> bool:
        lowered = str(error).casefold()
        return "id reference" in lowered or "uuid" in lowered


__all__ = [
    "CommandDispatcher",
    "serialize_reminder",
]
