from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from types import TracebackType
from typing import Self
from uuid import uuid7

from bazaar_compute_node.core.models import (
    InboundAttachment,
    Message,
    MessageDirection,
    OwnedReminder,
    Reminder,
    ReminderState,
)
from bazaar_compute_node.core.reminder import (
    canonical_id_reference,
)

from .storage import (
    MemoryStorage as _BaseMemoryStorage,
)
from .storage import (
    _MemoryStorageTransaction as _BaseMemoryStorageTransaction,
)


class MemoryStorage(_BaseMemoryStorage):
    def __init__(self) -> None:
        super().__init__()
        self.reminders: dict[str, Reminder] = {}

    def _operation_for_agent(
        self,
        agent_id: str | None,
        agent_name: str | None = None,
    ) -> _ReminderMemoryStorageTransaction:
        return _ReminderMemoryStorageTransaction(
            self,
            agent_id=agent_id,
            agent_name=agent_name,
        )


class _ReminderMemoryStorageTransaction(_BaseMemoryStorageTransaction):
    def __init__(
        self,
        storage: MemoryStorage,
        *,
        agent_id: str | None = None,
        agent_name: str | None = None,
    ) -> None:
        super().__init__(storage, agent_id=agent_id, agent_name=agent_name)
        self._reminder_storage = storage
        self._reminder_snapshot: dict[str, Reminder] | None = None

    async def __aenter__(self) -> Self:
        await super().__aenter__()
        self._reminder_snapshot = deepcopy(self._reminder_storage.reminders)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        if exc_type is not None and self._reminder_snapshot is not None:
            self._reminder_storage.reminders = self._reminder_snapshot
        return await super().__aexit__(exc_type, exc_value, traceback)

    async def get_reminder(
        self,
        owner_thread_id: str,
        reminder_id: str,
    ) -> Reminder | None:
        reference = canonical_id_reference(reminder_id)
        return next(
            (
                reminder
                for reminder in self._reminder_storage.reminders.values()
                if reminder.owner_thread_id == owner_thread_id
                and reminder.reminder_id == reference
            ),
            None,
        )

    async def list_reminders(
        self,
        owner_thread_id: str,
        statuses: frozenset[ReminderState],
    ) -> tuple[Reminder, ...]:
        if not isinstance(statuses, frozenset) or not statuses:
            raise ValueError("statuses must be a non-empty frozenset")
        if not all(isinstance(status, ReminderState) for status in statuses):
            raise TypeError("statuses must contain ReminderState values")
        reminders = [
            reminder
            for reminder in self._reminder_storage.reminders.values()
            if reminder.owner_thread_id == owner_thread_id
            and reminder.state in statuses
        ]
        reminders.sort(
            key=lambda reminder: (-reminder.updated_at_ms, reminder.reminder_id)
        )
        return tuple(reminders)

    async def save_new_reminder(self, reminder: object) -> Reminder:
        if not isinstance(reminder, Reminder):
            raise TypeError("reminder must be a Reminder")
        if reminder.state is not ReminderState.SCHEDULED:
            raise ValueError("a new reminder must be scheduled")
        if reminder.revision != 1 or reminder.last_occurrence_no != 0:
            raise ValueError("a new reminder must start at revision 1 with no history")
        if reminder.last_fired_at_ms is not None or reminder.canceled_at_ms is not None:
            raise ValueError("a new reminder cannot contain terminal history")
        if reminder.owner_thread_id not in self._reminder_storage.threads:
            raise ValueError(f"unknown thread: {reminder.owner_thread_id}")
        anchor_reference = canonical_id_reference(reminder.anchor_message_id)
        anchor = await self.resolve_message(
            reminder.owner_thread_id,
            anchor_reference,
            direction=MessageDirection.INBOUND,
        )
        if anchor is None:
            raise ValueError("anchor message does not belong to the reminder owner")
        canonical = replace(reminder, reminder_id=str(uuid7()))
        self._reminder_storage.reminders[canonical.reminder_id] = canonical
        return canonical

    async def save_reminder_transition(
        self,
        expected_revision: int,
        reminder: object,
    ) -> Reminder:
        if not isinstance(reminder, Reminder):
            raise TypeError("reminder must be a Reminder")
        existing = await self.get_reminder(
            reminder.owner_thread_id, reminder.reminder_id
        )
        if existing is None:
            raise ValueError("reminder not found")
        _validate_expected_revision(existing, expected_revision)
        _validate_reminder_identity(existing, reminder)
        if reminder.revision != expected_revision + 1:
            raise ValueError("reminder revision must advance by exactly one")
        if reminder.updated_at_ms < existing.updated_at_ms:
            raise ValueError("reminder updated_at_ms cannot move backwards")
        if (
            reminder.last_occurrence_no != existing.last_occurrence_no
            or reminder.last_fired_at_ms != existing.last_fired_at_ms
        ):
            raise ValueError("non-fire transition cannot change occurrence history")
        if existing.state is ReminderState.CANCELED:
            raise ValueError("a canceled reminder cannot transition")
        if reminder.state is ReminderState.FIRED:
            raise ValueError("reminder fire must materialize a system message")
        if existing.state is ReminderState.FIRED:
            if reminder.state is not ReminderState.SCHEDULED:
                raise ValueError("a fired reminder can only be snoozed to scheduled")
        elif reminder.state not in {
            ReminderState.SCHEDULED,
            ReminderState.CANCELED,
        }:
            raise ValueError("scheduled reminder transition is invalid")
        self._reminder_storage.reminders[reminder.reminder_id] = reminder
        return reminder

    async def get_next_scheduled_reminder(self) -> Reminder | None:
        reminders = [
            reminder
            for reminder in self._reminder_storage.reminders.values()
            if reminder.state is ReminderState.SCHEDULED
        ]
        if not reminders:
            return None
        return min(
            reminders,
            key=lambda reminder: (
                reminder.next_fire_at_ms
                if reminder.next_fire_at_ms is not None
                else -1,
                reminder.reminder_id,
            ),
        )

    async def list_due_reminders(
        self,
        now_ms: int,
        *,
        limit: int,
    ) -> tuple[Reminder, ...]:
        reminders = [
            reminder
            for reminder in self._reminder_storage.reminders.values()
            if reminder.state is ReminderState.SCHEDULED
            and reminder.next_fire_at_ms is not None
            and reminder.next_fire_at_ms <= now_ms
        ]
        reminders.sort(
            key=lambda reminder: (
                reminder.next_fire_at_ms
                if reminder.next_fire_at_ms is not None
                else -1,
                reminder.reminder_id,
            )
        )
        return tuple(reminders[:limit])

    async def get_owned_reminder(
        self,
        agent_id: str,
        owner_thread_id: str,
        reminder_id: str,
    ) -> OwnedReminder | None:
        reminder = await self.get_reminder(owner_thread_id, reminder_id)
        if reminder is None or self._agent_id_for_thread(owner_thread_id) != agent_id:
            return None
        return OwnedReminder(agent_id=agent_id, reminder=reminder)

    async def save_owned_reminder_transition(
        self,
        expected_revision: int,
        reminder: OwnedReminder,
    ) -> Reminder | None:
        owned = await self.get_owned_reminder(
            reminder.agent_id,
            reminder.reminder.owner_thread_id,
            reminder.reminder.reminder_id,
        )
        if owned is None:
            return None
        if owned.reminder.revision != expected_revision:
            return None
        if owned.reminder.state is ReminderState.CANCELED:
            return None
        self._reminder_storage.reminders[reminder.reminder.reminder_id] = (
            reminder.reminder
        )
        return reminder.reminder

    async def get_next_scheduled_owned_reminder(self) -> OwnedReminder | None:
        reminders = [
            owned
            for reminder in self._reminder_storage.reminders.values()
            if reminder.state is ReminderState.SCHEDULED
            and reminder.next_fire_at_ms is not None
            for owned in [self._owned_reminder(reminder)]
            if owned is not None
        ]
        if not reminders:
            return None
        return min(
            reminders,
            key=lambda owned: (
                owned.reminder.next_fire_at_ms,
                owned.agent_id,
                owned.reminder.reminder_id,
            ),
        )

    async def list_due_owned_reminders(
        self,
        now_ms: int,
        *,
        limit: int,
    ) -> tuple[OwnedReminder, ...]:
        reminders = [
            owned
            for reminder in self._reminder_storage.reminders.values()
            if reminder.state is ReminderState.SCHEDULED
            and reminder.next_fire_at_ms is not None
            and reminder.next_fire_at_ms <= now_ms
            for owned in [self._owned_reminder(reminder)]
            if owned is not None
        ]
        reminders.sort(
            key=lambda owned: (
                owned.reminder.next_fire_at_ms,
                owned.agent_id,
                owned.reminder.reminder_id,
            )
        )
        return tuple(reminders[:limit])

    async def materialize_owned_reminder_message(
        self,
        expected_revision: int,
        reminder: OwnedReminder,
        system_message: Message[InboundAttachment],
    ) -> Message[InboundAttachment] | None:
        incoming = reminder.reminder
        existing_owned = await self.get_owned_reminder(
            reminder.agent_id,
            incoming.owner_thread_id,
            incoming.reminder_id,
        )
        if existing_owned is None:
            return None
        existing = existing_owned.reminder
        if existing.revision != expected_revision:
            return None
        if existing.state is not ReminderState.SCHEDULED:
            return None
        if existing.next_fire_at_ms is None:
            return None
        anchor = await self.resolve_message(
            incoming.owner_thread_id,
            incoming.anchor_message_id,
            direction=MessageDirection.INBOUND,
        )
        if anchor is None:
            return None
        self._reminder_storage.reminders[incoming.reminder_id] = incoming
        return await self._save_inbound_message(system_message)

    def _owned_reminder(self, reminder: Reminder) -> OwnedReminder | None:
        agent_id = self._agent_id_for_thread(reminder.owner_thread_id)
        if agent_id is None:
            return None
        return OwnedReminder(agent_id=agent_id, reminder=reminder)

    def _agent_id_for_thread(self, session_id: str) -> str | None:
        session = self._reminder_storage.threads.get(session_id)
        return session.workspace_id if session is not None else None


def _validate_expected_revision(
    existing: Reminder,
    expected_revision: int,
) -> None:
    if existing.revision != expected_revision:
        raise ValueError(
            "reminder revision conflict: "
            f"expected {expected_revision}, found {existing.revision}"
        )


def _validate_reminder_identity(
    existing: Reminder,
    incoming: Reminder,
) -> None:
    if (
        existing.reminder_id != incoming.reminder_id
        or existing.owner_thread_id != incoming.owner_thread_id
        or existing.anchor_message_id != incoming.anchor_message_id
        or existing.timezone != incoming.timezone
        or existing.created_at_ms != incoming.created_at_ms
    ):
        raise ValueError("reminder identity cannot change")


__all__ = ["MemoryStorage"]
