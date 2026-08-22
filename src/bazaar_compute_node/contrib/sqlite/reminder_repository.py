from __future__ import annotations

from dataclasses import replace
from typing import cast
from uuid import uuid7

from ...core.models import (
    InboundMessage,
    OwnedReminder,
    OwnedReminderOccurrence,
    Reminder,
    ReminderOccurrence,
    ReminderOwner,
    ReminderState,
)
from ...core.reminder import canonical_id_reference
from .codec import (
    _required_non_negative_int,
    _required_text,
    inbound_message_from_row,
    validate_non_empty_text,
    validate_non_negative_int,
    validate_positive_int,
)
from .reminder_codec import reminder_from_row, reminder_occurrence_from_row
from .repository import SqliteTransaction

_REMINDER_COLUMNS = (
    "reminder_id, owner_session_id, anchor_message_id, title, state, "
    "next_fire_at_ms, repeat_rule, timezone, revision, last_occurrence_no, "
    "created_at_ms, updated_at_ms, last_fired_at_ms, canceled_at_ms"
)
_OCCURRENCE_COLUMNS = (
    "occurrence_id, reminder_id, owner_session_id, occurrence_no, "
    "anchor_message_id, scheduled_for_ms, fired_at_ms, next_fire_at_ms, "
    "overdue, read_at_ms, created_at_ms"
)
_INBOUND_COLUMNS = (
    "seq, message_id, session_id, channel_session_id, channel, "
    "provider_thread_id, provider_message_id, provider_time_ms, "
    "received_at_ms, sender, message_type, canonical_target, target_kind, "
    "reply_to_message_id, body, mentions_agent, notifies_runtime, "
    "provider_payload_ref, metadata_json"
)
_OWNED_REMINDER_COLUMNS = f"agent_id, {_REMINDER_COLUMNS}"


class ReminderTransaction(SqliteTransaction):
    async def resolve_inbound_message(
        self,
        session_id: str,
        message_id: str,
    ) -> InboundMessage | None:
        validate_non_empty_text(session_id, "session_id")
        reference = canonical_id_reference(message_id)
        row = await self.fetchone(
            f"SELECT {_INBOUND_COLUMNS} FROM inbound_messages "
            "WHERE session_id = ? AND message_id = ?",
            (session_id, reference),
        )
        if row is None:
            return None
        return inbound_message_from_row(row, await self._attachments(row["message_id"]))

    async def get_reminder(
        self,
        owner_session_id: str,
        reminder_id: str,
    ) -> Reminder | None:
        validate_non_empty_text(owner_session_id, "owner_session_id")
        reference = canonical_id_reference(reminder_id)
        row = await self.fetchone(
            f"SELECT {_REMINDER_COLUMNS} FROM reminders "
            "WHERE owner_session_id = ? AND reminder_id = ?",
            (owner_session_id, reference),
        )
        return reminder_from_row(row) if row is not None else None

    async def list_reminders(
        self,
        owner_session_id: str,
        statuses: frozenset[ReminderState],
    ) -> tuple[Reminder, ...]:
        validate_non_empty_text(owner_session_id, "owner_session_id")
        if not isinstance(statuses, frozenset) or not statuses:
            raise ValueError("statuses must be a non-empty frozenset")
        if not all(isinstance(status, ReminderState) for status in statuses):
            raise TypeError("statuses must contain ReminderState values")
        ordered = tuple(sorted(status.value for status in statuses))
        placeholders = ", ".join("?" for _ in ordered)
        rows = await self.fetchall(
            f"SELECT {_REMINDER_COLUMNS} FROM reminders "
            f"WHERE owner_session_id = ? AND state IN ({placeholders}) "
            "ORDER BY updated_at_ms DESC, reminder_id",
            (owner_session_id, *ordered),
        )
        return tuple(reminder_from_row(row) for row in rows)

    async def save_new_reminder(self, reminder: object) -> Reminder:
        if not isinstance(reminder, Reminder):
            raise TypeError("reminder must be a Reminder")
        if reminder.state is not ReminderState.SCHEDULED:
            raise ValueError("a new reminder must be scheduled")
        if reminder.revision != 1 or reminder.last_occurrence_no != 0:
            raise ValueError("a new reminder must start at revision 1 with no history")
        if reminder.last_fired_at_ms is not None or reminder.canceled_at_ms is not None:
            raise ValueError("a new reminder cannot contain terminal history")
        if await self.get_bcn_session(reminder.owner_session_id) is None:
            raise ValueError(f"unknown bcn session: {reminder.owner_session_id}")
        anchor_reference = canonical_id_reference(reminder.anchor_message_id)
        anchor = await self.resolve_inbound_message(
            reminder.owner_session_id, anchor_reference
        )
        if anchor is None:
            raise ValueError("anchor message does not belong to the reminder owner")
        canonical = replace(reminder, reminder_id=str(uuid7()))
        await self.execute(
            "INSERT INTO reminders ("
            "reminder_id, owner_session_id, anchor_message_id, title, state, "
            "next_fire_at_ms, repeat_rule, timezone, revision, last_occurrence_no, "
            "created_at_ms, updated_at_ms, last_fired_at_ms, canceled_at_ms"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                canonical.reminder_id,
                canonical.owner_session_id,
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
        validate_positive_int(expected_revision, "expected_revision")
        if not isinstance(reminder, Reminder):
            raise TypeError("reminder must be a Reminder")
        existing = await self.get_reminder(
            reminder.owner_session_id, reminder.reminder_id
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
            raise ValueError("reminder fire must use save_fired_occurrence")
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

    async def get_next_scheduled_reminder(self) -> Reminder | None:
        row = await self.fetchone(
            f"SELECT {_REMINDER_COLUMNS} FROM reminders "
            "WHERE state = ? ORDER BY next_fire_at_ms, reminder_id LIMIT 1",
            (ReminderState.SCHEDULED.value,),
        )
        return reminder_from_row(row) if row is not None else None

    async def list_due_reminders(
        self,
        now_ms: int,
        *,
        limit: int,
    ) -> tuple[Reminder, ...]:
        validate_non_negative_int(now_ms, "now_ms")
        validate_positive_int(limit, "limit")
        rows = await self.fetchall(
            f"SELECT {_REMINDER_COLUMNS} FROM reminders "
            "WHERE state = ? AND next_fire_at_ms <= ? "
            "ORDER BY next_fire_at_ms, reminder_id LIMIT ?",
            (ReminderState.SCHEDULED.value, now_ms, limit),
        )
        return tuple(reminder_from_row(row) for row in rows)

    async def get_owned_reminder(
        self,
        agent_id: str,
        owner_session_id: str,
        reminder_id: str,
    ) -> OwnedReminder | None:
        validate_non_empty_text(agent_id, "agent_id")
        validate_non_empty_text(owner_session_id, "owner_session_id")
        validate_non_empty_text(reminder_id, "reminder_id")
        bound_agent_id = self._bound_agent_id()
        if bound_agent_id is not None and bound_agent_id != agent_id:
            return None
        effective_agent_id = bound_agent_id or agent_id
        row = await self.fetchone(
            f"SELECT {_OWNED_REMINDER_COLUMNS} FROM reminders "
            "WHERE agent_id = ? AND owner_session_id = ? AND reminder_id = ?",
            (effective_agent_id, owner_session_id, reminder_id),
        )
        return self._owned_reminder_from_row(row) if row is not None else None

    async def get_next_scheduled_owned_reminder(self) -> OwnedReminder | None:
        predicates = ["state = ?"]
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
        validate_non_negative_int(now_ms, "now_ms")
        validate_positive_int(limit, "limit")
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

    async def save_owned_fired_occurrence(
        self,
        expected_revision: int,
        reminder: object,
        occurrence: object,
    ) -> OwnedReminderOccurrence:
        validate_positive_int(expected_revision, "expected_revision")
        if not isinstance(reminder, OwnedReminder):
            raise TypeError("reminder must be an OwnedReminder")
        if not isinstance(occurrence, OwnedReminderOccurrence):
            raise TypeError("occurrence must be an OwnedReminderOccurrence")
        if reminder.agent_id != occurrence.agent_id:
            raise ValueError("Reminder and occurrence Agent ownership does not match")

        incoming = reminder.reminder
        incoming_occurrence = occurrence.occurrence
        existing_owned = await self.get_owned_reminder(
            reminder.agent_id,
            incoming.owner_session_id,
            incoming.reminder_id,
        )
        if existing_owned is None:
            raise ValueError("reminder not found")
        existing = existing_owned.reminder
        self._validate_expected_revision(existing, expected_revision)
        self._validate_reminder_identity(existing, incoming)
        if existing.state is not ReminderState.SCHEDULED:
            raise ValueError("only a scheduled reminder can fire")
        if incoming.revision != expected_revision + 1:
            raise ValueError("reminder revision must advance by exactly one")
        if incoming.last_occurrence_no != existing.last_occurrence_no + 1:
            raise ValueError("reminder occurrence number must advance by exactly one")
        if incoming.last_fired_at_ms != incoming_occurrence.fired_at_ms:
            raise ValueError("reminder fire time does not match occurrence")
        if incoming.updated_at_ms != incoming_occurrence.fired_at_ms:
            raise ValueError("reminder update time does not match occurrence fire")
        if incoming.title != existing.title:
            raise ValueError("fire cannot change reminder title")
        if incoming.repeat_rule != existing.repeat_rule:
            raise ValueError("fire cannot change reminder cadence")
        if incoming_occurrence.reminder_id != existing.reminder_id:
            raise ValueError("occurrence reminder binding does not match")
        if incoming_occurrence.owner_session_id != existing.owner_session_id:
            raise ValueError("occurrence owner binding does not match")
        if incoming_occurrence.anchor_message_id != existing.anchor_message_id:
            raise ValueError("occurrence anchor binding does not match")
        if incoming_occurrence.occurrence_no != incoming.last_occurrence_no:
            raise ValueError("occurrence number does not match reminder history")
        if incoming_occurrence.scheduled_for_ms != existing.next_fire_at_ms:
            raise ValueError("occurrence scheduled slot does not match reminder")
        if incoming_occurrence.next_fire_at_ms != incoming.next_fire_at_ms:
            raise ValueError("occurrence next fire does not match reminder")
        if incoming_occurrence.overdue != (
            incoming_occurrence.fired_at_ms > incoming_occurrence.scheduled_for_ms
        ):
            raise ValueError("occurrence overdue flag does not match fire time")
        if incoming_occurrence.read_at_ms is not None:
            raise ValueError("a new occurrence must be pending")

        duplicate = await self.fetchone(
            "SELECT 1 FROM reminder_occurrences "
            "WHERE agent_id = ? AND reminder_id = ? AND occurrence_no = ?",
            (
                reminder.agent_id,
                existing.reminder_id,
                incoming_occurrence.occurrence_no,
            ),
        )
        if duplicate is not None:
            raise ValueError("reminder occurrence number is already persisted")

        canonical = replace(incoming_occurrence, occurrence_id=str(uuid7()))
        await self.execute(
            "INSERT INTO reminder_occurrences ("
            "agent_id, occurrence_id, reminder_id, owner_session_id, occurrence_no, "
            "anchor_message_id, scheduled_for_ms, fired_at_ms, next_fire_at_ms, "
            "overdue, read_at_ms, created_at_ms) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                reminder.agent_id,
                canonical.occurrence_id,
                canonical.reminder_id,
                canonical.owner_session_id,
                canonical.occurrence_no,
                canonical.anchor_message_id,
                canonical.scheduled_for_ms,
                canonical.fired_at_ms,
                canonical.next_fire_at_ms,
                int(canonical.overdue),
                canonical.read_at_ms,
                canonical.created_at_ms,
            ),
        )
        await self.execute(
            "UPDATE reminders SET title = ?, state = ?, next_fire_at_ms = ?, "
            "repeat_rule = ?, timezone = ?, revision = ?, last_occurrence_no = ?, "
            "updated_at_ms = ?, last_fired_at_ms = ?, canceled_at_ms = ? "
            "WHERE agent_id = ? AND reminder_id = ? AND owner_session_id = ?",
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
                incoming.owner_session_id,
            ),
        )
        return OwnedReminderOccurrence(reminder.agent_id, canonical)

    async def list_pending_reminder_owners(self) -> tuple[ReminderOwner, ...]:
        parameters: tuple[object, ...] = ()
        predicate = "read_at_ms IS NULL"
        bound_agent_id = self._bound_agent_id()
        if bound_agent_id is not None:
            predicate += " AND agent_id = ?"
            parameters = (bound_agent_id,)
        rows = await self.fetchall(
            "SELECT DISTINCT agent_id, owner_session_id FROM reminder_occurrences "
            f"WHERE {predicate} ORDER BY agent_id, owner_session_id",
            parameters,
        )
        return tuple(
            ReminderOwner(
                agent_id=_required_text(row["agent_id"], "agent_id"),
                owner_session_id=_required_text(
                    row["owner_session_id"], "owner_session_id"
                ),
            )
            for row in rows
        )

    def _bound_agent_id(self) -> str | None:
        return getattr(self, "agent_id", None)

    @staticmethod
    def _owned_reminder_from_row(row) -> OwnedReminder:
        return OwnedReminder(
            agent_id=_required_text(row["agent_id"], "agent_id"),
            reminder=reminder_from_row(row),
        )

    async def save_fired_occurrence(
        self,
        expected_revision: int,
        reminder: object,
        occurrence: object,
    ) -> ReminderOccurrence:
        validate_positive_int(expected_revision, "expected_revision")
        if not isinstance(reminder, Reminder):
            raise TypeError("reminder must be a Reminder")
        if not isinstance(occurrence, ReminderOccurrence):
            raise TypeError("occurrence must be a ReminderOccurrence")
        existing = await self.get_reminder(
            reminder.owner_session_id, reminder.reminder_id
        )
        if existing is None:
            raise ValueError("reminder not found")
        self._validate_expected_revision(existing, expected_revision)
        self._validate_reminder_identity(existing, reminder)
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
        duplicate = await self.fetchone(
            "SELECT 1 FROM reminder_occurrences "
            "WHERE reminder_id = ? AND occurrence_no = ?",
            (existing.reminder_id, occurrence.occurrence_no),
        )
        if duplicate is not None:
            raise ValueError("reminder occurrence number is already persisted")
        canonical = replace(occurrence, occurrence_id=str(uuid7()))
        await self.execute(
            "INSERT INTO reminder_occurrences ("
            "occurrence_id, reminder_id, owner_session_id, occurrence_no, "
            "anchor_message_id, scheduled_for_ms, fired_at_ms, next_fire_at_ms, "
            "overdue, read_at_ms, created_at_ms"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                canonical.occurrence_id,
                canonical.reminder_id,
                canonical.owner_session_id,
                canonical.occurrence_no,
                canonical.anchor_message_id,
                canonical.scheduled_for_ms,
                canonical.fired_at_ms,
                canonical.next_fire_at_ms,
                int(canonical.overdue),
                canonical.read_at_ms,
                canonical.created_at_ms,
            ),
        )
        await self._update_reminder(reminder)
        return canonical

    async def list_pending_reminder_occurrences(
        self,
        owner_session_id: str,
        *,
        limit: int,
    ) -> tuple[ReminderOccurrence, ...]:
        validate_non_empty_text(owner_session_id, "owner_session_id")
        validate_positive_int(limit, "limit")
        rows = await self.fetchall(
            f"SELECT {_OCCURRENCE_COLUMNS} FROM reminder_occurrences "
            "WHERE owner_session_id = ? AND read_at_ms IS NULL "
            "ORDER BY fired_at_ms, occurrence_id LIMIT ?",
            (owner_session_id, limit),
        )
        return tuple(reminder_occurrence_from_row(row) for row in rows)

    async def count_pending_reminder_occurrences(self, owner_session_id: str) -> int:
        validate_non_empty_text(owner_session_id, "owner_session_id")
        row = await self.fetchone(
            "SELECT COUNT(*) AS pending_count FROM reminder_occurrences "
            "WHERE owner_session_id = ? AND read_at_ms IS NULL",
            (owner_session_id,),
        )
        if row is None:
            raise RuntimeError("SQLite reminder pending count returned no row")
        return _required_non_negative_int(row["pending_count"], "pending_count")

    async def mark_reminder_occurrences_read(
        self,
        owner_session_id: str,
        occurrence_ids: object,
        *,
        read_at_ms: int,
    ) -> tuple[ReminderOccurrence, ...]:
        validate_non_empty_text(owner_session_id, "owner_session_id")
        validate_non_negative_int(read_at_ms, "read_at_ms")
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
            row = await self.fetchone(
                f"SELECT {_OCCURRENCE_COLUMNS} FROM reminder_occurrences "
                "WHERE occurrence_id = ? AND owner_session_id = ?",
                (reference, owner_session_id),
            )
            if row is None:
                raise ValueError("reminder occurrence does not belong to owner")
            occurrence = reminder_occurrence_from_row(row)
            if not occurrence.pending:
                raise ValueError("reminder occurrence was already read")
            updated = occurrence.mark_read(at_ms=read_at_ms)
            await self.execute(
                "UPDATE reminder_occurrences SET read_at_ms = ? "
                "WHERE occurrence_id = ? AND owner_session_id = ? "
                "AND read_at_ms IS NULL",
                (read_at_ms, reference, owner_session_id),
            )
            marked.append(updated)
        return tuple(marked)

    async def list_sessions_with_pending_reminders(self) -> tuple[str, ...]:
        rows = await self.fetchall(
            "SELECT DISTINCT owner_session_id FROM reminder_occurrences "
            "WHERE read_at_ms IS NULL ORDER BY owner_session_id"
        )
        return tuple(str(row["owner_session_id"]) for row in rows)

    async def _update_reminder(self, reminder: Reminder) -> None:
        await self.execute(
            "UPDATE reminders SET title = ?, state = ?, next_fire_at_ms = ?, "
            "repeat_rule = ?, timezone = ?, revision = ?, last_occurrence_no = ?, "
            "updated_at_ms = ?, last_fired_at_ms = ?, canceled_at_ms = ? "
            "WHERE reminder_id = ? AND owner_session_id = ?",
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
                reminder.owner_session_id,
            ),
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
            or existing.owner_session_id != incoming.owner_session_id
            or existing.anchor_message_id != incoming.anchor_message_id
            or existing.timezone != incoming.timezone
            or existing.created_at_ms != incoming.created_at_ms
        ):
            raise ValueError("reminder identity cannot change")


__all__ = ["ReminderTransaction"]
