from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Self
from unicodedata import category
from uuid import UUID

from .models import Reminder, ReminderOccurrence, ReminderState
from .reminder_schedule import (
    RecurrenceKind,
    ReminderDuration,
    ReminderRecurrence,
    ReminderSchedule,
    canonical_timezone,
    format_utc_timestamp,
    next_recurrence_ms,
    parse_duration,
    parse_fire_at,
    parse_repeat_rule,
    resolve_schedule,
)

_ID_PREFIX_PATTERN = re.compile(r"[0-9a-fA-F]{8}\Z")

__all__ = [
    "RecurrenceKind",
    "ReminderCancelRequest",
    "ReminderCancelResult",
    "ReminderCheckItem",
    "ReminderCheckRequest",
    "ReminderCheckResult",
    "ReminderDuration",
    "ReminderListRequest",
    "ReminderListResult",
    "ReminderRecurrence",
    "ReminderSchedule",
    "ReminderScheduleRequest",
    "ReminderScheduleResult",
    "ReminderSnoozeRequest",
    "ReminderSnoozeResult",
    "ReminderUpdateRequest",
    "ReminderUpdateResult",
    "canonical_id_reference",
    "canonical_timezone",
    "format_utc_timestamp",
    "next_recurrence_ms",
    "parse_duration",
    "parse_fire_at",
    "parse_repeat_rule",
    "resolve_schedule",
    "short_id",
]


@dataclass(frozen=True, slots=True)
class ReminderScheduleRequest:
    title: str
    message_id: str
    next_fire_at_ms: int
    repeat_rule: str | None
    timezone: str

    def __post_init__(self) -> None:
        _validate_title(self.title)
        object.__setattr__(self, "message_id", canonical_id_reference(self.message_id))
        ReminderSchedule(
            next_fire_at_ms=self.next_fire_at_ms,
            repeat_rule=self.repeat_rule,
            timezone=self.timezone,
        )

    @classmethod
    def from_options(
        cls,
        *,
        title: str,
        message_id: str,
        evaluated_at_ms: int,
        delay_seconds: int | None = None,
        fire_at: str | None = None,
        repeat_rule: str | None = None,
        timezone: str | None = None,
    ) -> Self:
        schedule = resolve_schedule(
            evaluated_at_ms=evaluated_at_ms,
            delay_seconds=delay_seconds,
            fire_at=fire_at,
            repeat_rule=repeat_rule,
            timezone=timezone,
        )
        return cls(
            title=title,
            message_id=message_id,
            next_fire_at_ms=schedule.next_fire_at_ms,
            repeat_rule=schedule.repeat_rule,
            timezone=schedule.timezone,
        )


@dataclass(frozen=True, slots=True)
class ReminderCheckRequest:
    limit: int = 100

    def __post_init__(self) -> None:
        _require_positive_int(self.limit, "limit")
        if self.limit > 100:
            raise ValueError("limit cannot exceed 100")


@dataclass(frozen=True, slots=True)
class ReminderListRequest:
    statuses: frozenset[ReminderState] = frozenset(
        {ReminderState.SCHEDULED, ReminderState.FIRED}
    )

    def __post_init__(self) -> None:
        if not isinstance(self.statuses, frozenset) or not self.statuses:
            raise ValueError("statuses must be a non-empty frozenset")
        if not all(isinstance(status, ReminderState) for status in self.statuses):
            raise TypeError("statuses must contain ReminderState values")


@dataclass(frozen=True, slots=True)
class ReminderSnoozeRequest:
    reminder_id: str
    duration_ms: int
    evaluated_at_ms: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "reminder_id", canonical_id_reference(self.reminder_id)
        )
        _require_positive_int(self.duration_ms, "duration_ms")
        _require_non_negative_int(self.evaluated_at_ms, "evaluated_at_ms")

    @classmethod
    def from_options(
        cls,
        *,
        reminder_id: str,
        duration: str,
        evaluated_at_ms: int,
    ) -> Self:
        return cls(
            reminder_id=reminder_id,
            duration_ms=parse_duration(duration).milliseconds,
            evaluated_at_ms=evaluated_at_ms,
        )


@dataclass(frozen=True, slots=True)
class ReminderUpdateRequest:
    reminder_id: str
    evaluated_at_ms: int
    title: str | None = None
    next_fire_at_ms: int | None = None
    repeat_rule: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "reminder_id", canonical_id_reference(self.reminder_id)
        )
        _require_non_negative_int(self.evaluated_at_ms, "evaluated_at_ms")
        provided = sum(
            value is not None
            for value in (self.title, self.next_fire_at_ms, self.repeat_rule)
        )
        if provided != 1:
            raise ValueError("exactly one reminder update field must be provided")
        if self.title is not None:
            _validate_title(self.title)
        if self.next_fire_at_ms is not None:
            _require_non_negative_int(self.next_fire_at_ms, "next_fire_at_ms")
            if self.next_fire_at_ms <= self.evaluated_at_ms:
                raise ValueError("next_fire_at_ms must be in the future")
        if self.repeat_rule is not None:
            recurrence = parse_repeat_rule(self.repeat_rule)
            if recurrence.canonical != self.repeat_rule:
                raise ValueError("repeat_rule must use its canonical form")

    @classmethod
    def from_options(
        cls,
        *,
        reminder_id: str,
        evaluated_at_ms: int,
        fire_at: str | None = None,
        in_duration: str | None = None,
        cadence: str | None = None,
        title: str | None = None,
    ) -> Self:
        _require_non_negative_int(evaluated_at_ms, "evaluated_at_ms")
        provided = sum(
            value is not None for value in (fire_at, in_duration, cadence, title)
        )
        if provided != 1:
            raise ValueError("exactly one reminder update option must be provided")
        next_fire_at_ms = None
        repeat_rule = None
        if fire_at is not None:
            next_fire_at_ms = parse_fire_at(fire_at)
        elif in_duration is not None:
            next_fire_at_ms = evaluated_at_ms + parse_duration(in_duration).milliseconds
        elif cadence is not None:
            repeat_rule = parse_repeat_rule(cadence).canonical
        return cls(
            reminder_id=reminder_id,
            evaluated_at_ms=evaluated_at_ms,
            title=title,
            next_fire_at_ms=next_fire_at_ms,
            repeat_rule=repeat_rule,
        )


@dataclass(frozen=True, slots=True)
class ReminderCancelRequest:
    reminder_id: str
    evaluated_at_ms: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "reminder_id", canonical_id_reference(self.reminder_id)
        )
        _require_non_negative_int(self.evaluated_at_ms, "evaluated_at_ms")


@dataclass(frozen=True, slots=True)
class ReminderScheduleResult:
    reminder: Reminder

    def __post_init__(self) -> None:
        _require_reminder(self.reminder)


@dataclass(frozen=True, slots=True)
class ReminderCheckItem:
    occurrence: ReminderOccurrence
    title: str
    canonical_target: str

    def __post_init__(self) -> None:
        if not isinstance(self.occurrence, ReminderOccurrence):
            raise TypeError("occurrence must be a ReminderOccurrence")
        _validate_title(self.title)
        if not isinstance(self.canonical_target, str) or not self.canonical_target:
            raise ValueError("canonical_target must be a non-empty string")


@dataclass(frozen=True, slots=True)
class ReminderCheckResult:
    items: tuple[ReminderCheckItem, ...]
    has_more: bool

    def __post_init__(self) -> None:
        if not isinstance(self.items, tuple) or not all(
            isinstance(item, ReminderCheckItem) for item in self.items
        ):
            raise TypeError("items must be a tuple of ReminderCheckItem values")
        if not isinstance(self.has_more, bool):
            raise TypeError("has_more must be a boolean")


@dataclass(frozen=True, slots=True)
class ReminderListResult:
    reminders: tuple[Reminder, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.reminders, tuple) or not all(
            isinstance(reminder, Reminder) for reminder in self.reminders
        ):
            raise TypeError("reminders must be a tuple of Reminder values")


@dataclass(frozen=True, slots=True)
class ReminderSnoozeResult:
    reminder: Reminder

    def __post_init__(self) -> None:
        _require_reminder(self.reminder)


@dataclass(frozen=True, slots=True)
class ReminderUpdateResult:
    reminder: Reminder

    def __post_init__(self) -> None:
        _require_reminder(self.reminder)


@dataclass(frozen=True, slots=True)
class ReminderCancelResult:
    reminder: Reminder

    def __post_init__(self) -> None:
        _require_reminder(self.reminder)


def canonical_id_reference(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("id reference must be a non-empty string")
    if _ID_PREFIX_PATTERN.fullmatch(value) is not None:
        return value.lower()
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise ValueError(
            "id reference must be a UUID or 8-character hexadecimal prefix"
        ) from error
    if str(parsed) != value.lower():
        raise ValueError("full UUID references must use canonical hyphenated form")
    return str(parsed)


def short_id(value: str) -> str:
    try:
        parsed = UUID(value)
    except (TypeError, ValueError) as error:
        raise ValueError("value must be a full UUID") from error
    return parsed.hex[:8]


def _require_non_negative_int(value: int, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")


def _require_positive_int(value: int, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")


def _require_reminder(value: object) -> None:
    if not isinstance(value, Reminder):
        raise TypeError("reminder must be a Reminder")


def _validate_title(value: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError("title must be a non-empty string")
    if any(category(character) == "Cc" for character in value):
        raise ValueError("title cannot contain control characters")
