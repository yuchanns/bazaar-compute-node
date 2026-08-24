from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from types import TracebackType
from typing import Self, cast
from uuid import uuid7

from bazaar_compute_node.core.models import (
    Message,
    OwnedReminder,
    OwnedReminderOccurrence,
    Reminder,
    ReminderOccurrence,
    ReminderOwner,
    ReminderState,
)
from bazaar_compute_node.core.reminder import canonical_id_reference

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
        self.reminder_occurrences: dict[str, ReminderOccurrence] = {}

    def _operation_for_agent(
        self, agent_id: str | None
    ) -> _ReminderMemoryStorageTransaction:
        return _ReminderMemoryStorageTransaction(self, agent_id=agent_id)


class _ReminderMemoryStorageTransaction(_BaseMemoryStorageTransaction):
    def __init__(self, storage: MemoryStorage, *, agent_id: str | None = None) -> None:
        super().__init__(storage, agent_id=agent_id)
        self._reminder_storage = storage
        self._reminder_snapshot: (
            tuple[
                dict[str, Reminder],
                dict[str, ReminderOccurrence],
            ]
            | None
        ) = None

    async def __aenter__(self) -> Self:
        await super().__aenter__()
        self._reminder_snapshot = (
            deepcopy(self._reminder_storage.reminders),
            deepcopy(self._reminder_storage.reminder_occurrences),
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        if exc_type is not None and self._reminder_snapshot is not None:
            (
                self._reminder_storage.reminders,
                self._reminder_storage.reminder_occurrences,
            ) = self._reminder_snapshot
        return await super().__aexit__(exc_type, exc_value, traceback)

    async def resolve_inbound_message(
        self,
        session_id: str,
        message_id: str,
    ) -> Message | None:
        reference = canonical_id_reference(message_id)
        return next(
            (
                message
                for message in self._reminder_storage.inbound_messages.get(
                    session_id, []
                )
                if message.message_id == reference
            ),
            None,
        )

    async def get_reminder(
        self,
        owner_session_id: str,
        reminder_id: str,
    ) -> Reminder | None:
        reference = canonical_id_reference(reminder_id)
        return next(
            (
                reminder
                for reminder in self._reminder_storage.reminders.values()
                if reminder.owner_session_id == owner_session_id
                and reminder.reminder_id == reference
            ),
            None,
        )

    async def list_reminders(
        self,
        owner_session_id: str,
        statuses: frozenset[ReminderState],
    ) -> tuple[Reminder, ...]:
        if not isinstance(statuses, frozenset) or not statuses:
            raise ValueError("statuses must be a non-empty frozenset")
        if not all(isinstance(status, ReminderState) for status in statuses):
            raise TypeError("statuses must contain ReminderState values")
        reminders = [
            reminder
            for reminder in self._reminder_storage.reminders.values()
            if reminder.owner_session_id == owner_session_id
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
        if reminder.owner_session_id not in self._reminder_storage.bcn_sessions:
            raise ValueError(f"unknown bcn session: {reminder.owner_session_id}")
        anchor_reference = canonical_id_reference(reminder.anchor_message_id)
        anchor = await self.resolve_inbound_message(
            reminder.owner_session_id, anchor_reference
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
            reminder.owner_session_id, reminder.reminder_id
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
            raise ValueError("reminder fire must use save_fired_occurrence")
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
        owner_session_id: str,
        reminder_id: str,
    ) -> OwnedReminder | None:
        reminder = await self.get_reminder(owner_session_id, reminder_id)
        if reminder is None or self._agent_id_for_session(owner_session_id) != agent_id:
            return None
        return OwnedReminder(agent_id=agent_id, reminder=reminder)

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

    async def save_fired_occurrence(
        self,
        expected_revision: int,
        reminder: object,
        occurrence: object,
    ) -> ReminderOccurrence:
        if not isinstance(reminder, Reminder):
            raise TypeError("reminder must be a Reminder")
        if not isinstance(occurrence, ReminderOccurrence):
            raise TypeError("occurrence must be a ReminderOccurrence")
        existing = await self.get_reminder(
            reminder.owner_session_id, reminder.reminder_id
        )
        if existing is None:
            raise ValueError("reminder not found")
        _validate_expected_revision(existing, expected_revision)
        _validate_reminder_identity(existing, reminder)
        if existing.state is not ReminderState.SCHEDULED:
            raise ValueError("only a scheduled reminder can fire")
        if reminder.revision != expected_revision + 1:
            raise ValueError("reminder revision must advance by exactly one")
        if reminder.last_occurrence_no != existing.last_occurrence_no + 1:
            raise ValueError("reminder occurrence number must advance by exactly one")
        if reminder.last_fired_at_ms != occurrence.fired_at_ms:
            raise ValueError("reminder fire time does not match occurrence")
        if reminder.updated_at_ms != occurrence.fired_at_ms:
            raise ValueError("reminder update time does not match occurrence fire")
        if reminder.title != existing.title:
            raise ValueError("fire cannot change reminder title")
        if reminder.repeat_rule != existing.repeat_rule:
            raise ValueError("fire cannot change reminder cadence")
        if occurrence.reminder_id != existing.reminder_id:
            raise ValueError("occurrence reminder binding does not match")
        if occurrence.owner_session_id != existing.owner_session_id:
            raise ValueError("occurrence owner binding does not match")
        if occurrence.anchor_message_id != existing.anchor_message_id:
            raise ValueError("occurrence anchor binding does not match")
        if occurrence.occurrence_no != reminder.last_occurrence_no:
            raise ValueError("occurrence number does not match reminder history")
        if occurrence.scheduled_for_ms != existing.next_fire_at_ms:
            raise ValueError("occurrence scheduled slot does not match reminder")
        if occurrence.next_fire_at_ms != reminder.next_fire_at_ms:
            raise ValueError("occurrence next fire does not match reminder")
        if occurrence.overdue != (occurrence.fired_at_ms > occurrence.scheduled_for_ms):
            raise ValueError("occurrence overdue flag does not match fire time")
        if occurrence.read_at_ms is not None:
            raise ValueError("a new occurrence must be pending")
        if any(
            persisted.reminder_id == existing.reminder_id
            and persisted.occurrence_no == occurrence.occurrence_no
            for persisted in self._reminder_storage.reminder_occurrences.values()
        ):
            raise ValueError("reminder occurrence number is already persisted")
        canonical = replace(occurrence, occurrence_id=str(uuid7()))
        self._reminder_storage.reminder_occurrences[canonical.occurrence_id] = canonical
        self._reminder_storage.reminders[reminder.reminder_id] = reminder
        return canonical

    async def save_owned_fired_occurrence(
        self,
        expected_revision: int,
        reminder: object,
        occurrence: object,
    ) -> OwnedReminderOccurrence:
        if not isinstance(reminder, OwnedReminder):
            raise TypeError("reminder must be an OwnedReminder")
        if not isinstance(occurrence, OwnedReminderOccurrence):
            raise TypeError("occurrence must be an OwnedReminderOccurrence")
        if reminder.agent_id != occurrence.agent_id:
            raise ValueError("Reminder and occurrence Agent ownership does not match")
        if (
            await self.get_owned_reminder(
                reminder.agent_id,
                reminder.reminder.owner_session_id,
                reminder.reminder.reminder_id,
            )
            is None
        ):
            raise ValueError("reminder not found")
        canonical = await self.save_fired_occurrence(
            expected_revision,
            reminder.reminder,
            occurrence.occurrence,
        )
        return OwnedReminderOccurrence(reminder.agent_id, canonical)

    async def list_pending_reminder_occurrences(
        self,
        owner_session_id: str,
        *,
        limit: int,
    ) -> tuple[ReminderOccurrence, ...]:
        occurrences = [
            occurrence
            for occurrence in self._reminder_storage.reminder_occurrences.values()
            if occurrence.owner_session_id == owner_session_id and occurrence.pending
        ]
        occurrences.sort(
            key=lambda occurrence: (occurrence.fired_at_ms, occurrence.occurrence_id)
        )
        return tuple(occurrences[:limit])

    async def count_pending_reminder_occurrences(self, owner_session_id: str) -> int:
        return sum(
            occurrence.owner_session_id == owner_session_id and occurrence.pending
            for occurrence in self._reminder_storage.reminder_occurrences.values()
        )

    async def mark_reminder_occurrences_read(
        self,
        owner_session_id: str,
        occurrence_ids: object,
        *,
        read_at_ms: int,
    ) -> tuple[ReminderOccurrence, ...]:
        if not isinstance(occurrence_ids, tuple):
            raise TypeError("occurrence_ids must be a tuple")
        occurrence_ids = cast(tuple[str, ...], occurrence_ids)
        if not occurrence_ids:
            return ()
        if len(set(occurrence_ids)) != len(occurrence_ids):
            raise ValueError("occurrence_ids cannot contain duplicates")
        marked: list[ReminderOccurrence] = []
        for occurrence_id in occurrence_ids:
            reference = canonical_id_reference(occurrence_id)
            occurrence = self._reminder_storage.reminder_occurrences.get(reference)
            if occurrence is None or occurrence.owner_session_id != owner_session_id:
                raise ValueError("reminder occurrence does not belong to owner")
            updated = occurrence.mark_read(at_ms=read_at_ms)
            self._reminder_storage.reminder_occurrences[reference] = updated
            marked.append(updated)
        return tuple(marked)

    async def list_sessions_with_pending_reminders(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    occurrence.owner_session_id
                    for occurrence in self._reminder_storage.reminder_occurrences.values()
                    if occurrence.pending
                }
            )
        )

    async def list_pending_reminder_owners(self) -> tuple[ReminderOwner, ...]:
        owners: set[ReminderOwner] = set()
        for occurrence in self._reminder_storage.reminder_occurrences.values():
            if not occurrence.pending:
                continue
            agent_id = self._agent_id_for_session(occurrence.owner_session_id)
            if agent_id is not None:
                owners.add(
                    ReminderOwner(
                        agent_id=agent_id,
                        owner_session_id=occurrence.owner_session_id,
                    )
                )
        return tuple(
            sorted(owners, key=lambda owner: (owner.agent_id, owner.owner_session_id))
        )

    def _owned_reminder(self, reminder: Reminder) -> OwnedReminder | None:
        agent_id = self._agent_id_for_session(reminder.owner_session_id)
        if agent_id is None:
            return None
        return OwnedReminder(agent_id=agent_id, reminder=reminder)

    def _agent_id_for_session(self, session_id: str) -> str | None:
        session = self._reminder_storage.bcn_sessions.get(session_id)
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
        or existing.owner_session_id != incoming.owner_session_id
        or existing.anchor_message_id != incoming.anchor_message_id
        or existing.timezone != incoming.timezone
        or existing.created_at_ms != incoming.created_at_ms
    ):
        raise ValueError("reminder identity cannot change")


__all__ = ["MemoryStorage"]
