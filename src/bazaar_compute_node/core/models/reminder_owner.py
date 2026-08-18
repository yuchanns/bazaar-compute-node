from __future__ import annotations

from dataclasses import dataclass

from .reminder import Reminder, ReminderOccurrence


def _validate_text(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")


@dataclass(frozen=True, slots=True)
class ReminderOwner:
    """Global Reminder owner identity across configured Agents."""

    agent_id: str
    owner_session_id: str

    def __post_init__(self) -> None:
        _validate_text(self.agent_id, "agent_id")
        _validate_text(self.owner_session_id, "owner_session_id")


@dataclass(frozen=True, slots=True)
class OwnedReminder:
    """Reminder definition paired with its durable Agent ownership."""

    agent_id: str
    reminder: Reminder

    def __post_init__(self) -> None:
        _validate_text(self.agent_id, "agent_id")
        if not isinstance(self.reminder, Reminder):
            raise TypeError("reminder must be a Reminder")

    @property
    def owner(self) -> ReminderOwner:
        return ReminderOwner(self.agent_id, self.reminder.owner_session_id)


@dataclass(frozen=True, slots=True)
class OwnedReminderOccurrence:
    """Materialized Reminder occurrence paired with its owning Agent."""

    agent_id: str
    occurrence: ReminderOccurrence

    def __post_init__(self) -> None:
        _validate_text(self.agent_id, "agent_id")
        if not isinstance(self.occurrence, ReminderOccurrence):
            raise TypeError("occurrence must be a ReminderOccurrence")

    @property
    def owner(self) -> ReminderOwner:
        return ReminderOwner(self.agent_id, self.occurrence.owner_session_id)


__all__ = ["OwnedReminder", "OwnedReminderOccurrence", "ReminderOwner"]
