from __future__ import annotations

from dataclasses import dataclass

from .reminder import Reminder, ReminderOccurrence


@dataclass(frozen=True, slots=True)
class ReminderOwner:
    """Global Reminder owner identity across configured Agents."""

    agent_id: str
    owner_session_id: str


@dataclass(frozen=True, slots=True)
class OwnedReminder:
    """Reminder definition paired with its durable Agent ownership."""

    agent_id: str
    reminder: Reminder

    @property
    def owner(self) -> ReminderOwner:
        return ReminderOwner(self.agent_id, self.reminder.owner_session_id)


@dataclass(frozen=True, slots=True)
class OwnedReminderOccurrence:
    """Materialized Reminder occurrence paired with its owning Agent."""

    agent_id: str
    occurrence: ReminderOccurrence

    @property
    def owner(self) -> ReminderOwner:
        return ReminderOwner(self.agent_id, self.occurrence.owner_session_id)


__all__ = ["OwnedReminder", "OwnedReminderOccurrence", "ReminderOwner"]
