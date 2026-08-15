from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Self
from unicodedata import category

from .states import REMINDER_TRANSITIONS, ReminderState, ensure_transition


def _validate_text(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")


def _validate_non_negative(value: int, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")


def _validate_positive(value: int, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")


def _validate_title(value: str) -> None:
    _validate_text(value, "title")
    if any(category(character) == "Cc" for character in value):
        raise ValueError("title cannot contain control characters")


def _validate_transition_time(current_ms: int, incoming_ms: int) -> None:
    _validate_non_negative(incoming_ms, "at_ms")
    if incoming_ms < current_ms:
        raise ValueError("reminder transition time cannot move backwards")


@dataclass(frozen=True, slots=True)
class Reminder:
    reminder_id: str
    owner_session_id: str
    anchor_message_id: str
    title: str
    state: ReminderState
    next_fire_at_ms: int | None
    repeat_rule: str | None
    timezone: str
    revision: int
    last_occurrence_no: int
    created_at_ms: int
    updated_at_ms: int
    last_fired_at_ms: int | None = None
    canceled_at_ms: int | None = None

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.reminder_id, "reminder_id"),
            (self.owner_session_id, "owner_session_id"),
            (self.anchor_message_id, "anchor_message_id"),
            (self.timezone, "timezone"),
        ):
            _validate_text(value, field_name)
        _validate_title(self.title)
        if not isinstance(self.state, ReminderState):
            raise TypeError("state must be a ReminderState")
        if self.repeat_rule is not None:
            _validate_text(self.repeat_rule, "repeat_rule")
        _validate_positive(self.revision, "revision")
        _validate_non_negative(self.last_occurrence_no, "last_occurrence_no")
        _validate_non_negative(self.created_at_ms, "created_at_ms")
        _validate_non_negative(self.updated_at_ms, "updated_at_ms")
        if self.updated_at_ms < self.created_at_ms:
            raise ValueError("updated_at_ms cannot precede created_at_ms")
        for value, field_name in (
            (self.next_fire_at_ms, "next_fire_at_ms"),
            (self.last_fired_at_ms, "last_fired_at_ms"),
            (self.canceled_at_ms, "canceled_at_ms"),
        ):
            if value is not None:
                _validate_non_negative(value, field_name)
                if value > self.updated_at_ms and field_name != "next_fire_at_ms":
                    raise ValueError(f"{field_name} cannot exceed updated_at_ms")
        if self.state is ReminderState.SCHEDULED:
            if self.next_fire_at_ms is None:
                raise ValueError("a scheduled reminder requires next_fire_at_ms")
            if self.canceled_at_ms is not None:
                raise ValueError("a scheduled reminder cannot have canceled_at_ms")
        elif self.state is ReminderState.FIRED:
            if self.next_fire_at_ms is not None:
                raise ValueError("a fired reminder cannot have next_fire_at_ms")
            if self.repeat_rule is not None:
                raise ValueError("a recurring reminder cannot remain fired")
            if self.last_fired_at_ms is None:
                raise ValueError("a fired reminder requires last_fired_at_ms")
            if self.canceled_at_ms is not None:
                raise ValueError("a fired reminder cannot have canceled_at_ms")
        else:
            if self.next_fire_at_ms is not None:
                raise ValueError("a canceled reminder cannot have next_fire_at_ms")
            if self.canceled_at_ms is None:
                raise ValueError("a canceled reminder requires canceled_at_ms")
        if (self.last_occurrence_no == 0) != (self.last_fired_at_ms is None):
            raise ValueError(
                "last_occurrence_no and last_fired_at_ms must describe the same history"
            )
        if self.revision < self.last_occurrence_no + 1:
            raise ValueError("revision cannot precede the occurrence history")

    def snooze(self, *, duration_ms: int, at_ms: int) -> Self:
        _validate_positive(duration_ms, "duration_ms")
        _validate_transition_time(self.updated_at_ms, at_ms)
        if self.state is ReminderState.CANCELED:
            raise ValueError("a canceled reminder cannot be snoozed")
        base_ms = (
            self.next_fire_at_ms if self.state is ReminderState.SCHEDULED else at_ms
        )
        if base_ms is None:
            raise AssertionError("a scheduled reminder has no next fire time")
        ensure_transition(
            "reminder", self.state, ReminderState.SCHEDULED, REMINDER_TRANSITIONS
        )
        return replace(
            self,
            state=ReminderState.SCHEDULED,
            next_fire_at_ms=base_ms + duration_ms,
            revision=self.revision + 1,
            updated_at_ms=at_ms,
            canceled_at_ms=None,
        )

    def update_title(self, title: str, *, at_ms: int) -> Self:
        self._require_scheduled("updated")
        _validate_title(title)
        _validate_transition_time(self.updated_at_ms, at_ms)
        return replace(
            self,
            title=title,
            revision=self.revision + 1,
            updated_at_ms=at_ms,
        )

    def update_next_fire(self, next_fire_at_ms: int, *, at_ms: int) -> Self:
        self._require_scheduled("updated")
        _validate_non_negative(next_fire_at_ms, "next_fire_at_ms")
        _validate_transition_time(self.updated_at_ms, at_ms)
        if next_fire_at_ms <= at_ms:
            raise ValueError("next_fire_at_ms must be in the future")
        return replace(
            self,
            next_fire_at_ms=next_fire_at_ms,
            revision=self.revision + 1,
            updated_at_ms=at_ms,
        )

    def update_cadence(self, repeat_rule: str, *, at_ms: int) -> Self:
        self._require_scheduled("updated")
        _validate_text(repeat_rule, "repeat_rule")
        _validate_transition_time(self.updated_at_ms, at_ms)
        return replace(
            self,
            repeat_rule=repeat_rule,
            revision=self.revision + 1,
            updated_at_ms=at_ms,
        )

    def cancel(self, *, at_ms: int) -> Self:
        self._require_scheduled("canceled")
        _validate_transition_time(self.updated_at_ms, at_ms)
        ensure_transition(
            "reminder", self.state, ReminderState.CANCELED, REMINDER_TRANSITIONS
        )
        return replace(
            self,
            state=ReminderState.CANCELED,
            next_fire_at_ms=None,
            revision=self.revision + 1,
            updated_at_ms=at_ms,
            canceled_at_ms=at_ms,
        )

    def record_fire(
        self,
        *,
        scheduled_for_ms: int,
        fired_at_ms: int,
        next_fire_at_ms: int | None,
    ) -> Self:
        self._require_scheduled("fired")
        _validate_non_negative(scheduled_for_ms, "scheduled_for_ms")
        _validate_non_negative(fired_at_ms, "fired_at_ms")
        _validate_transition_time(self.updated_at_ms, fired_at_ms)
        if scheduled_for_ms != self.next_fire_at_ms:
            raise ValueError(
                "scheduled_for_ms does not match the current reminder slot"
            )
        if fired_at_ms < scheduled_for_ms:
            raise ValueError("fired_at_ms cannot precede scheduled_for_ms")
        if self.repeat_rule is None:
            if next_fire_at_ms is not None:
                raise ValueError("a one-time reminder cannot retain a next fire time")
            target_state = ReminderState.FIRED
        else:
            if next_fire_at_ms is None:
                raise ValueError("a recurring reminder requires its next fire time")
            _validate_non_negative(next_fire_at_ms, "next_fire_at_ms")
            if next_fire_at_ms <= scheduled_for_ms:
                raise ValueError(
                    "a recurring next fire time must follow the current slot"
                )
            target_state = ReminderState.SCHEDULED
        ensure_transition("reminder", self.state, target_state, REMINDER_TRANSITIONS)
        return replace(
            self,
            state=target_state,
            next_fire_at_ms=next_fire_at_ms,
            revision=self.revision + 1,
            last_occurrence_no=self.last_occurrence_no + 1,
            updated_at_ms=fired_at_ms,
            last_fired_at_ms=fired_at_ms,
        )

    def _require_scheduled(self, operation: str) -> None:
        if self.state is not ReminderState.SCHEDULED:
            raise ValueError(
                f"only a scheduled reminder can be {operation}; current state is "
                f"{self.state.value}"
            )


@dataclass(frozen=True, slots=True)
class ReminderOccurrence:
    occurrence_id: str
    reminder_id: str
    owner_session_id: str
    occurrence_no: int
    anchor_message_id: str
    scheduled_for_ms: int
    fired_at_ms: int
    next_fire_at_ms: int | None
    overdue: bool
    read_at_ms: int | None
    created_at_ms: int

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.occurrence_id, "occurrence_id"),
            (self.reminder_id, "reminder_id"),
            (self.owner_session_id, "owner_session_id"),
            (self.anchor_message_id, "anchor_message_id"),
        ):
            _validate_text(value, field_name)
        _validate_positive(self.occurrence_no, "occurrence_no")
        _validate_non_negative(self.scheduled_for_ms, "scheduled_for_ms")
        _validate_non_negative(self.fired_at_ms, "fired_at_ms")
        _validate_non_negative(self.created_at_ms, "created_at_ms")
        if self.fired_at_ms < self.scheduled_for_ms:
            raise ValueError("fired_at_ms cannot precede scheduled_for_ms")
        if self.created_at_ms < self.fired_at_ms:
            raise ValueError("created_at_ms cannot precede fired_at_ms")
        if self.next_fire_at_ms is not None:
            _validate_non_negative(self.next_fire_at_ms, "next_fire_at_ms")
            if self.next_fire_at_ms <= self.scheduled_for_ms:
                raise ValueError("next_fire_at_ms must follow scheduled_for_ms")
        if not isinstance(self.overdue, bool):
            raise TypeError("overdue must be a boolean")
        if self.overdue and self.fired_at_ms == self.scheduled_for_ms:
            raise ValueError("an overdue occurrence must fire after its scheduled time")
        if self.read_at_ms is not None:
            _validate_non_negative(self.read_at_ms, "read_at_ms")
            if self.read_at_ms < self.created_at_ms:
                raise ValueError("read_at_ms cannot precede created_at_ms")

    @property
    def pending(self) -> bool:
        return self.read_at_ms is None

    def mark_read(self, *, at_ms: int) -> Self:
        if self.read_at_ms is not None:
            raise ValueError("reminder occurrence was already read")
        _validate_non_negative(at_ms, "at_ms")
        if at_ms < self.created_at_ms:
            raise ValueError("read time cannot precede occurrence creation")
        return replace(self, read_at_ms=at_ms)
