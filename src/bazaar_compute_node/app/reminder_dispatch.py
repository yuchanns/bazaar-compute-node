from __future__ import annotations

from collections.abc import Mapping
from time import time_ns

from ..core.command import ICommandService, IReminderService
from ..core.lifecycle import TimeoutBudget
from ..core.models import Reminder, ReminderOccurrence, ReminderState
from ..core.orchestration.reminder_command import ReminderCommandFailure
from ..core.reminder import (
    ReminderCancelRequest,
    ReminderCheckRequest,
    ReminderListRequest,
    ReminderScheduleRequest,
    ReminderSnoozeRequest,
    ReminderUpdateRequest,
)
from .command import CommandDispatcher as _MessageCommandDispatcher
from .command import (
    CommandDispatchError,
    ControlHandler,
    SessionBindingValidator,
)


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


def serialize_reminder_occurrence(
    occurrence: ReminderOccurrence,
) -> dict[str, object]:
    return {
        "occurrence_id": occurrence.occurrence_id,
        "reminder_id": occurrence.reminder_id,
        "owner_session_id": occurrence.owner_session_id,
        "occurrence_no": occurrence.occurrence_no,
        "anchor_message_id": occurrence.anchor_message_id,
        "scheduled_for_ms": occurrence.scheduled_for_ms,
        "fired_at_ms": occurrence.fired_at_ms,
        "next_fire_at_ms": occurrence.next_fire_at_ms,
        "overdue": occurrence.overdue,
        "read_at_ms": occurrence.read_at_ms,
        "created_at_ms": occurrence.created_at_ms,
    }


class CommandDispatcher(_MessageCommandDispatcher):
    """Resource-aware dispatcher for message, inbox, thread, and Reminder commands."""

    def __init__(
        self,
        service: ICommandService,
        *,
        reminder_service: IReminderService,
        timeout_budget: TimeoutBudget,
        control_handler: ControlHandler | None = None,
        session_binding_validator: SessionBindingValidator | None = None,
    ) -> None:
        super().__init__(
            service,
            timeout_budget=timeout_budget,
            control_handler=control_handler,
            session_binding_validator=session_binding_validator,
        )
        self._reminder_service = reminder_service

    async def _dispatch_command(
        self,
        request: Mapping[str, object],
    ) -> Mapping[str, object]:
        resource = request.get("resource")
        if not isinstance(resource, str) or not resource:
            raise CommandDispatchError(
                "RESOURCE_REQUIRED",
                "resource must be a non-empty string",
            )
        command = request.get("command")
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
            return await super()._dispatch_command(request)
        if resource == "thread":
            if command != "unfollow":
                raise CommandDispatchError(
                    "UNKNOWN_COMMAND",
                    f"unsupported thread command: {command}",
                )
            return await super()._dispatch_command(request)
        if resource == "inbox":
            return await super()._dispatch_command(request)
        if resource != "reminder":
            raise CommandDispatchError(
                "UNKNOWN_RESOURCE",
                f"unsupported command resource: {resource}",
            )

        session_id = request.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise CommandDispatchError(
                "SESSION_REQUIRED",
                "session_id must be a non-empty string",
            )
        if self._session_binding_validator is not None:
            await self._session_binding_validator(session_id, request)

        try:
            return await self._dispatch_reminder(session_id, command, request)
        except ReminderCommandFailure as error:
            raise CommandDispatchError(
                error.code,
                error.message,
                next_action=error.next_action,
            ) from error

    async def _dispatch_reminder(
        self,
        session_id: str,
        command: str,
        request: Mapping[str, object],
    ) -> Mapping[str, object]:
        now_ms = time_ns() // 1_000_000
        if command == "schedule":
            title = request.get("title")
            message_id = request.get("message_id")
            delay_seconds = request.get("delay_seconds")
            fire_at = request.get("fire_at")
            repeat_rule = request.get("repeat_rule")
            timezone = request.get("timezone")
            if not isinstance(title, str) or not title:
                raise CommandDispatchError(
                    "REMINDER_TITLE_REQUIRED",
                    "Reminder title must be a non-empty string.",
                )
            if not isinstance(message_id, str) or not message_id:
                raise CommandDispatchError(
                    "REMINDER_ANCHOR_REQUIRED",
                    "Reminder --message-id is required.",
                )
            if delay_seconds is not None and (
                isinstance(delay_seconds, bool)
                or not isinstance(delay_seconds, int)
                or delay_seconds <= 0
            ):
                raise CommandDispatchError(
                    "REMINDER_TIME_REQUIRED",
                    "--delay-seconds must be a positive integer.",
                )
            if fire_at is not None and (not isinstance(fire_at, str) or not fire_at):
                raise CommandDispatchError(
                    "REMINDER_TIME_REQUIRED",
                    "--fire-at must be a non-empty ISO-8601 timestamp.",
                )
            if repeat_rule is not None and (
                not isinstance(repeat_rule, str) or not repeat_rule
            ):
                raise CommandDispatchError(
                    "REMINDER_REPEAT_INVALID",
                    "--repeat must be a non-empty recurrence rule.",
                )
            if timezone is not None and (not isinstance(timezone, str) or not timezone):
                raise CommandDispatchError(
                    "REMINDER_TIMEZONE_INVALID",
                    "--tz must be a non-empty IANA timezone.",
                )
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
                typed = ReminderScheduleRequest.from_options(
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
            result = await self._reminder_service.schedule(session_id, typed)
            return {
                "ok": True,
                "result": {"reminder": serialize_reminder(result.reminder)},
            }

        if command == "check":
            result = await self._reminder_service.check(
                session_id,
                ReminderCheckRequest(),
            )
            return {
                "ok": True,
                "result": {
                    "items": [
                        {
                            "occurrence": serialize_reminder_occurrence(
                                item.occurrence
                            ),
                            "title": item.title,
                            "canonical_target": item.canonical_target,
                        }
                        for item in result.items
                    ],
                    "has_more": result.has_more,
                },
            }

        if command == "list":
            all_statuses = request.get("all", False)
            status_text = request.get("status")
            if not isinstance(all_statuses, bool):
                raise CommandDispatchError(
                    "INVALID_COMMAND",
                    "reminder list all flag must be boolean",
                )
            if status_text is not None and (
                not isinstance(status_text, str) or not status_text
            ):
                raise CommandDispatchError(
                    "INVALID_COMMAND",
                    "--status must be a non-empty comma-separated status list",
                )
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
            reminder_id = self._required_reminder_id(request)
            duration = request.get("by")
            if not isinstance(duration, str) or not duration:
                raise CommandDispatchError(
                    "INVALID_COMMAND",
                    "Reminder snooze requires --by <duration>.",
                )
            try:
                typed = ReminderSnoozeRequest.from_options(
                    reminder_id=reminder_id,
                    duration=duration,
                    evaluated_at_ms=now_ms,
                )
            except ValueError as error:
                code = (
                    "REMINDER_NOT_FOUND"
                    if self._is_id_error(error)
                    else "INVALID_COMMAND"
                )
                raise CommandDispatchError(code, str(error)) from error
            result = await self._reminder_service.snooze(session_id, typed)
            return {
                "ok": True,
                "result": {"reminder": serialize_reminder(result.reminder)},
            }

        if command == "update":
            reminder_id = self._required_reminder_id(request)
            fire_at = request.get("fire_at")
            in_duration = request.get("in")
            cadence = request.get("cadence")
            title = request.get("title")
            for value, field_name in (
                (fire_at, "fire_at"),
                (in_duration, "in"),
                (cadence, "cadence"),
                (title, "title"),
            ):
                if value is not None and (not isinstance(value, str) or not value):
                    raise CommandDispatchError(
                        "REMINDER_UPDATE_FAILED",
                        f"Reminder update {field_name} must be non-empty text.",
                    )
            try:
                typed = ReminderUpdateRequest.from_options(
                    reminder_id=reminder_id,
                    evaluated_at_ms=now_ms,
                    fire_at=fire_at if isinstance(fire_at, str) else None,
                    in_duration=(in_duration if isinstance(in_duration, str) else None),
                    cadence=cadence if isinstance(cadence, str) else None,
                    title=title if isinstance(title, str) else None,
                )
            except ValueError as error:
                if self._is_id_error(error):
                    code = "REMINDER_NOT_FOUND"
                elif cadence is not None:
                    code = "REMINDER_REPEAT_INVALID"
                else:
                    code = "REMINDER_UPDATE_FAILED"
                raise CommandDispatchError(code, str(error)) from error
            result = await self._reminder_service.update(session_id, typed)
            return {
                "ok": True,
                "result": {"reminder": serialize_reminder(result.reminder)},
            }

        if command == "cancel":
            reminder_id = self._required_reminder_id(request)
            try:
                typed = ReminderCancelRequest(
                    reminder_id=reminder_id,
                    evaluated_at_ms=now_ms,
                )
            except ValueError as error:
                raise CommandDispatchError("REMINDER_NOT_FOUND", str(error)) from error
            result = await self._reminder_service.cancel(session_id, typed)
            return {
                "ok": True,
                "result": {"reminder": serialize_reminder(result.reminder)},
            }

        raise CommandDispatchError(
            "UNKNOWN_COMMAND",
            f"unsupported reminder command: {command}",
        )

    @staticmethod
    def _required_reminder_id(request: Mapping[str, object]) -> str:
        reminder_id = request.get("reminder_id")
        if not isinstance(reminder_id, str) or not reminder_id:
            raise CommandDispatchError(
                "REMINDER_NOT_FOUND",
                "Reminder --id is required.",
            )
        return reminder_id

    @staticmethod
    def _is_id_error(error: ValueError) -> bool:
        lowered = str(error).casefold()
        return "id reference" in lowered or "uuid" in lowered


__all__ = [
    "CommandDispatcher",
    "serialize_reminder",
    "serialize_reminder_occurrence",
]
