from __future__ import annotations

from dataclasses import dataclass

from bazaar_contrib_messages import Agent, Thread

from .reminder import Reminder


@dataclass(frozen=True, slots=True)
class ReminderOwner:
    """Global Reminder owner identity across configured Agents."""

    agent: Agent
    thread: Thread


@dataclass(frozen=True, slots=True)
class OwnedReminder:
    """Reminder definition paired with its durable Agent ownership."""

    agent: Agent
    reminder: Reminder

    @property
    def owner(self) -> ReminderOwner:
        return ReminderOwner(self.agent, Thread(self.reminder.owner_thread_id))


__all__ = ["OwnedReminder", "ReminderOwner"]
