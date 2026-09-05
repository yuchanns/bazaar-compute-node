from __future__ import annotations

from dataclasses import replace
from uuid import uuid7

from ....core.models import (
    InboundAttachment,
    Message,
    MessageDirection,
    OwnedReminder,
    Reminder,
    ReminderState,
)
from ....core.reminder import canonical_id_reference
from ..codec import (
    _required_text,
)
from ..reminder_codec import reminder_from_row
from .base import RepositoryBase

_REMINDER_COLUMNS = (
    "reminder_id, owner_thread_id, anchor_message_id, title, state, "
    "next_fire_at_ms, repeat_rule, timezone, revision, last_occurrence_no, "
    "created_at_ms, updated_at_ms, last_fired_at_ms, canceled_at_ms"
)


_OWNED_REMINDER_COLUMNS = f"agent_id, {_REMINDER_COLUMNS}"


class ReminderOperations(RepositoryBase):
    async def get_reminder(
        self,
        owner_thread_id: str,
        reminder_id: str,
    ) -> Reminder | None:
        reference = canonical_id_reference(reminder_id)
        row = await self.fetchone(
            f"SELECT {_REMINDER_COLUMNS} FROM reminders "
            "WHERE agent_id = /*agent_id*/? AND owner_thread_id = ? "
            "AND reminder_id = ?",
            (owner_thread_id, reference),
        )
        return reminder_from_row(row) if row is not None else None

    async def list_reminders(
        self,
        owner_thread_id: str,
        statuses: frozenset[ReminderState],
    ) -> tuple[Reminder, ...]:
        if not isinstance(statuses, frozenset) or not statuses:
            raise ValueError("statuses must be a non-empty frozenset")
        if not all(isinstance(status, ReminderState) for status in statuses):
            raise TypeError("statuses must contain ReminderState values")
        ordered = tuple(sorted(status.value for status in statuses))
        placeholders = ", ".join("?" for _ in ordered)
        rows = await self.fetchall(
            f"SELECT {_REMINDER_COLUMNS} FROM reminders "
            f"WHERE agent_id = /*agent_id*/? AND owner_thread_id = ? "
            f"AND state IN ({placeholders}) ORDER BY updated_at_ms DESC, reminder_id",
            (owner_thread_id, *ordered),
        )
        return tuple(reminder_from_row(row) for row in rows)

    async def get_next_scheduled_reminder(self) -> Reminder | None:
        row = await self.fetchone(
            f"SELECT {_REMINDER_COLUMNS} FROM reminders "
            "WHERE agent_id = /*agent_id*/? AND state = ? "
            "ORDER BY next_fire_at_ms, reminder_id LIMIT 1",
            (ReminderState.SCHEDULED.value,),
        )
        return reminder_from_row(row) if row is not None else None

    async def list_due_reminders(
        self,
        now_ms: int,
        *,
        limit: int,
    ) -> tuple[Reminder, ...]:
        rows = await self.fetchall(
            f"SELECT {_REMINDER_COLUMNS} FROM reminders "
            "WHERE agent_id = /*agent_id*/? AND state = ? AND next_fire_at_ms <= ? "
            "ORDER BY next_fire_at_ms, reminder_id LIMIT ?",
            (ReminderState.SCHEDULED.value, now_ms, limit),
        )
        return tuple(reminder_from_row(row) for row in rows)

    async def _update_reminder(self, reminder: Reminder) -> None:
        await self.execute(
            "UPDATE reminders SET title = ?, state = ?, next_fire_at_ms = ?, "
            "repeat_rule = ?, timezone = ?, revision = ?, last_occurrence_no = ?, "
            "updated_at_ms = ?, last_fired_at_ms = ?, canceled_at_ms = ? "
            "WHERE agent_id = /*agent_id*/? AND reminder_id = ? AND owner_thread_id = ?",
            (
                reminder.title,
                reminder.state.value,
                reminder.next_fire_at_ms,
                reminder.repeat_rule,
                reminder.timezone,
                reminder.revision,
                reminder.last_occurrence_no,
                reminder.updated_at_ms,
                reminder.last_fired_at_ms,
                reminder.canceled_at_ms,
                reminder.reminder_id,
                reminder.owner_thread_id,
            ),
        )

    async def save_new_reminder(self, reminder: object) -> Reminder:
        if not isinstance(reminder, Reminder):
            raise TypeError("reminder must be a Reminder")
        if reminder.state is not ReminderState.SCHEDULED:
            raise ValueError("a new reminder must be scheduled")
        if reminder.revision != 1 or reminder.last_occurrence_no != 0:
            raise ValueError("a new reminder must start at revision 1 with no history")
        if reminder.last_fired_at_ms is not None or reminder.canceled_at_ms is not None:
            raise ValueError("a new reminder cannot contain terminal history")
        if await self.get_thread(reminder.owner_thread_id) is None:
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
        agent_id = self._bound_agent_id()
        if agent_id is None:
            raise RuntimeError("Agent-owned Reminder write requires an Agent scope")
        await self.execute(
            "INSERT INTO reminders ("
            "agent_id, reminder_id, owner_thread_id, anchor_message_id, title, state, "
            "next_fire_at_ms, repeat_rule, timezone, revision, last_occurrence_no, "
            "created_at_ms, updated_at_ms, last_fired_at_ms, canceled_at_ms"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                agent_id,
                canonical.reminder_id,
                canonical.owner_thread_id,
                canonical.anchor_message_id,
                canonical.title,
                canonical.state.value,
                canonical.next_fire_at_ms,
                canonical.repeat_rule,
                canonical.timezone,
                canonical.revision,
                canonical.last_occurrence_no,
                canonical.created_at_ms,
                canonical.updated_at_ms,
                canonical.last_fired_at_ms,
                canonical.canceled_at_ms,
            ),
        )
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
        self._validate_expected_revision(existing, expected_revision)
        self._validate_reminder_identity(existing, reminder)
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
        await self._update_reminder(reminder)
        return reminder

    async def save_owned_reminder_transition(
        self,
        expected_revision: int,
        reminder: OwnedReminder,
    ) -> Reminder | None:
        """Retire one Reminder, or report that its state moved before the write."""

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
        if existing.state is ReminderState.CANCELED:
            return None
        await self.execute(
            "UPDATE reminders SET title = ?, state = ?, next_fire_at_ms = ?, "
            "repeat_rule = ?, timezone = ?, revision = ?, last_occurrence_no = ?, "
            "updated_at_ms = ?, last_fired_at_ms = ?, canceled_at_ms = ? "
            "WHERE agent_id = ? AND reminder_id = ? AND owner_thread_id = ?",
            (
                incoming.title,
                incoming.state.value,
                incoming.next_fire_at_ms,
                incoming.repeat_rule,
                incoming.timezone,
                incoming.revision,
                incoming.last_occurrence_no,
                incoming.updated_at_ms,
                incoming.last_fired_at_ms,
                incoming.canceled_at_ms,
                reminder.agent_id,
                incoming.reminder_id,
                incoming.owner_thread_id,
            ),
        )
        return incoming

    async def get_owned_reminder(
        self,
        agent_id: str,
        owner_thread_id: str,
        reminder_id: str,
    ) -> OwnedReminder | None:
        bound_agent_id = self._bound_agent_id()
        if bound_agent_id is not None and bound_agent_id != agent_id:
            return None
        effective_agent_id = bound_agent_id or agent_id
        row = await self.fetchone(
            f"SELECT {_OWNED_REMINDER_COLUMNS} FROM reminders "
            "WHERE agent_id = ? AND owner_thread_id = ? AND reminder_id = ?",
            (effective_agent_id, owner_thread_id, reminder_id),
        )
        return self._owned_reminder_from_row(row) if row is not None else None

    async def get_next_scheduled_owned_reminder(self) -> OwnedReminder | None:
        predicates = ["state = ?", "next_fire_at_ms IS NOT NULL"]
        parameters: list[object] = [ReminderState.SCHEDULED.value]
        bound_agent_id = self._bound_agent_id()
        if bound_agent_id is not None:
            predicates.append("agent_id = ?")
            parameters.append(bound_agent_id)
        row = await self.fetchone(
            f"SELECT {_OWNED_REMINDER_COLUMNS} FROM reminders "
            f"WHERE {' AND '.join(predicates)} "
            "ORDER BY next_fire_at_ms, agent_id, reminder_id LIMIT 1",
            parameters,
        )
        return self._owned_reminder_from_row(row) if row is not None else None

    async def list_due_owned_reminders(
        self,
        now_ms: int,
        *,
        limit: int,
    ) -> tuple[OwnedReminder, ...]:
        predicates = ["state = ?", "next_fire_at_ms <= ?"]
        parameters: list[object] = [ReminderState.SCHEDULED.value, now_ms]
        bound_agent_id = self._bound_agent_id()
        if bound_agent_id is not None:
            predicates.append("agent_id = ?")
            parameters.append(bound_agent_id)
        rows = await self.fetchall(
            f"SELECT {_OWNED_REMINDER_COLUMNS} FROM reminders "
            f"WHERE {' AND '.join(predicates)} "
            "ORDER BY next_fire_at_ms, agent_id, reminder_id LIMIT ?",
            (*parameters, limit),
        )
        return tuple(self._owned_reminder_from_row(row) for row in rows)

    async def materialize_owned_reminder_message(
        self,
        expected_revision: int,
        reminder: OwnedReminder,
        system_message: Message[InboundAttachment],
    ) -> Message[InboundAttachment] | None:
        """Fire one Reminder, or report that its state moved before the write."""

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
        anchor = await self.fetchone(
            "SELECT channel_session_id, channel, provider_thread_id, target, "
            "target_kind FROM messages WHERE agent_id = ? AND thread_id = ? "
            "AND message_id = ? AND direction = 'inbound'",
            (
                reminder.agent_id,
                incoming.owner_thread_id,
                incoming.anchor_message_id,
            ),
        )
        if anchor is None:
            return None

        await self.execute(
            "UPDATE reminders SET title = ?, state = ?, next_fire_at_ms = ?, "
            "repeat_rule = ?, timezone = ?, revision = ?, last_occurrence_no = ?, "
            "updated_at_ms = ?, last_fired_at_ms = ?, canceled_at_ms = ? "
            "WHERE agent_id = ? AND reminder_id = ? AND owner_thread_id = ?",
            (
                incoming.title,
                incoming.state.value,
                incoming.next_fire_at_ms,
                incoming.repeat_rule,
                incoming.timezone,
                incoming.revision,
                incoming.last_occurrence_no,
                incoming.updated_at_ms,
                incoming.last_fired_at_ms,
                incoming.canceled_at_ms,
                reminder.agent_id,
                incoming.reminder_id,
                incoming.owner_thread_id,
            ),
        )
        return await self._save_system_message_for_agent(
            system_message,
            reminder.agent_id,
        )

    @staticmethod
    def _owned_reminder_from_row(row) -> OwnedReminder:
        return OwnedReminder(
            agent_id=_required_text(row["agent_id"], "agent_id"),
            reminder=reminder_from_row(row),
        )

    @staticmethod
    def _validate_expected_revision(
        existing: Reminder,
        expected_revision: int,
    ) -> None:
        if existing.revision != expected_revision:
            raise ValueError(
                "reminder revision conflict: "
                f"expected {expected_revision}, found {existing.revision}"
            )

    @staticmethod
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
