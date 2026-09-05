from __future__ import annotations

from dataclasses import dataclass

from .reminder import Reminder


@dataclass(frozen=True, slots=True)
class ReminderOwner:
    """Global Reminder owner identity across configured Agents."""

    agent_id: str
    owner_thread_id: str


@dataclass(frozen=True, slots=True)
class OwnedReminder:
    """Reminder definition paired with its durable Agent ownership."""

    agent_id: str
    reminder: Reminder

    @property
    def owner(self) -> ReminderOwner:
        return ReminderOwner(self.agent_id, self.reminder.owner_thread_id)


__all__ = ["OwnedReminder", "ReminderOwner"]
