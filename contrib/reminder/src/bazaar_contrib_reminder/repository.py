from __future__ import annotations

from dataclasses import replace
from typing import cast
from uuid import uuid7

from bazaar_contrib_messages import Agent, Messages, Thread
from bazaar_contrib_messages.models import InboundAttachment, Message
from bazaar_contrib_messages.states import MessageDirection
from bazaar_contrib_storage import (
    All,
    Column,
    Compare,
    In,
    Insert,
    IsNull,
    Not,
    Predicate,
    Record,
    Scalar,
    Select,
    Session,
    Sort,
    Update,
    Value,
)

from .models import OwnedReminder, Reminder, ReminderState
from .reminder import canonical_id_reference
from .serialize import serialize_reminder


def equal(name: str, value: Scalar) -> Predicate:
    column = Column(name=name)
    return (
        IsNull(expression=column)
        if value is None
        else Compare(left=column, operator="=", right=Value(value=value))
    )


class Repository:
    """Reminder definitions and transitions inside one caller-owned database scope."""

    def __init__(self, session: Session, messages: Messages, owner: Agent | None):
        self._session = session
        self._messages = messages
        self.owner = owner

    def scope(self, *conditions: Predicate) -> All:
        return All(
            conditions=(
                *(
                    (equal("agent_id", self.owner.id),)
                    if self.owner is not None
                    else ()
                ),
                *conditions,
            )
        )

    async def get_reminder(self, owner: Thread, reminder_id: str) -> Reminder | None:
        reference = canonical_id_reference(reminder_id)
        rows = await self._session.select(
            Select(
                table="reminders",
                where=self.scope(
                    equal("owner_thread_id", owner.id), equal("reminder_id", reference)
                ),
            )
        )
        return reminder_from_row(rows[0]) if rows else None

    async def list_reminders(
        self, owner: Thread, statuses: frozenset[ReminderState]
    ) -> tuple[Reminder, ...]:
        if not isinstance(statuses, frozenset) or not statuses:
            raise ValueError("statuses must be a non-empty frozenset")
        if not all(isinstance(status, ReminderState) for status in statuses):
            raise TypeError("statuses must contain ReminderState values")
        ordered = tuple(sorted(status.value for status in statuses))
        rows = await self._session.select(
            Select(
                table="reminders",
                where=self.scope(
                    equal("owner_thread_id", owner.id),
                    In(expression=Column(name="state"), values=ordered),
                ),
                order_by=(
                    Sort(expression=Column(name="updated_at_ms"), descending=True),
                    Sort(expression=Column(name="reminder_id")),
                ),
            )
        )
        return tuple(reminder_from_row(row) for row in rows)

    async def _update_reminder(self, owner: Agent, reminder: Reminder) -> None:
        values = cast(dict[str, Scalar], serialize_reminder(reminder))
        await self._session.update(
            Update(
                table="reminders",
                values={
                    name: Value(value=values[name])
                    for name in (
                        "title",
                        "state",
                        "next_fire_at_ms",
                        "repeat_rule",
                        "timezone",
                        "revision",
                        "last_occurrence_no",
                        "updated_at_ms",
                        "last_fired_at_ms",
                        "canceled_at_ms",
                    )
                },
                where=All(
                    conditions=(
                        equal("agent_id", owner.id),
                        equal("reminder_id", reminder.reminder_id),
                        equal("owner_thread_id", reminder.owner_thread_id),
                    )
                ),
            )
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
        owner = self.owner
        if owner is None:
            raise RuntimeError("Agent-owned Reminder write requires an Agent scope")
        messages = self._messages.in_session(self._session, owner)
        thread = Thread(reminder.owner_thread_id)
        if await messages.get_thread(thread) is None:
            raise ValueError(f"unknown thread: {reminder.owner_thread_id}")
        anchor_reference = canonical_id_reference(reminder.anchor_message_id)
        anchor = await messages.resolve_message(
            thread, anchor_reference, direction=MessageDirection.INBOUND
        )
        if anchor is None:
            raise ValueError("anchor message does not belong to the reminder owner")
        canonical = replace(reminder, reminder_id=str(uuid7()))
        await self._session.insert(
            Insert(
                table="reminders",
                rows=(
                    {
                        "agent_id": owner.id,
                        **cast(dict[str, Scalar], serialize_reminder(canonical)),
                    },
                ),
            )
        )
        return canonical

    async def save_reminder_transition(
        self, expected_revision: int, reminder: object
    ) -> Reminder:
        if not isinstance(reminder, Reminder):
            raise TypeError("reminder must be a Reminder")
        existing = await self.get_reminder(
            Thread(reminder.owner_thread_id), reminder.reminder_id
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
        elif reminder.state not in {ReminderState.SCHEDULED, ReminderState.CANCELED}:
            raise ValueError("scheduled reminder transition is invalid")
        if self.owner is None:
            raise RuntimeError("Agent-owned Reminder write requires an Agent scope")
        await self._update_reminder(self.owner, reminder)
        return reminder

    async def save_owned_reminder_transition(
        self, expected_revision: int, reminder: OwnedReminder
    ) -> Reminder | None:
        incoming = reminder.reminder
        existing_owned = await self.get_owned_reminder(
            reminder.agent, Thread(incoming.owner_thread_id), incoming.reminder_id
        )
        if existing_owned is None:
            return None
        existing = existing_owned.reminder
        if existing.revision != expected_revision:
            return None
        if existing.state is ReminderState.CANCELED:
            return None
        await self._update_reminder(reminder.agent, incoming)
        return incoming

    async def get_owned_reminder(
        self, owner: Agent, thread: Thread, reminder_id: str
    ) -> OwnedReminder | None:
        if self.owner is not None and self.owner != owner:
            return None
        rows = await self._session.select(
            Select(
                table="reminders",
                where=All(
                    conditions=(
                        equal("agent_id", owner.id),
                        equal("owner_thread_id", thread.id),
                        equal("reminder_id", reminder_id),
                    )
                ),
            )
        )
        return self._owned_reminder_from_row(rows[0]) if rows else None

    async def get_next_scheduled_owned_reminder(self) -> OwnedReminder | None:
        rows = await self._session.select(
            Select(
                table="reminders",
                where=self.scope(
                    equal("state", ReminderState.SCHEDULED.value),
                    Not(condition=IsNull(expression=Column(name="next_fire_at_ms"))),
                ),
                order_by=tuple(
                    Sort(expression=Column(name=name))
                    for name in ("next_fire_at_ms", "agent_id", "reminder_id")
                ),
                limit=1,
            )
        )
        return self._owned_reminder_from_row(rows[0]) if rows else None

    async def list_due_owned_reminders(
        self, now_ms: int, *, limit: int
    ) -> tuple[OwnedReminder, ...]:
        rows = await self._session.select(
            Select(
                table="reminders",
                where=self.scope(
                    equal("state", ReminderState.SCHEDULED.value),
                    Compare(
                        left=Column(name="next_fire_at_ms"),
                        operator="<=",
                        right=Value(value=now_ms),
                    ),
                ),
                order_by=tuple(
                    Sort(expression=Column(name=name))
                    for name in ("next_fire_at_ms", "agent_id", "reminder_id")
                ),
                limit=limit,
            )
        )
        return tuple(self._owned_reminder_from_row(row) for row in rows)

    async def materialize_owned_reminder_message(
        self,
        expected_revision: int,
        reminder: OwnedReminder,
        system_message: Message[InboundAttachment],
    ) -> Message[InboundAttachment] | None:
        incoming = reminder.reminder
        thread = Thread(incoming.owner_thread_id)
        existing_owned = await self.get_owned_reminder(
            reminder.agent, thread, incoming.reminder_id
        )
        if existing_owned is None:
            return None
        existing = existing_owned.reminder
        if existing.revision != expected_revision:
            return None
        if existing.state is not ReminderState.SCHEDULED:
            return None
        messages = self._messages.in_session(self._session, reminder.agent)
        anchor = await messages.get_owned_message(
            reminder.agent,
            thread,
            incoming.anchor_message_id,
            direction=MessageDirection.INBOUND,
        )
        if anchor is None:
            return None
        await self._update_reminder(reminder.agent, incoming)
        return cast(
            Message[InboundAttachment], await messages.save_message(system_message)
        )

    @staticmethod
    def _owned_reminder_from_row(row: Record) -> OwnedReminder:
        return OwnedReminder(
            agent=Agent(_required_text(row["agent_id"], "agent_id")),
            reminder=reminder_from_row(row),
        )

    @staticmethod
    def _validate_expected_revision(existing: Reminder, expected_revision: int) -> None:
        if existing.revision != expected_revision:
            raise ValueError(
                "reminder revision conflict: "
                f"expected {expected_revision}, found {existing.revision}"
            )

    @staticmethod
    def _validate_reminder_identity(existing: Reminder, incoming: Reminder) -> None:
        if (
            existing.reminder_id != incoming.reminder_id
            or existing.owner_thread_id != incoming.owner_thread_id
            or existing.anchor_message_id != incoming.anchor_message_id
            or existing.timezone != incoming.timezone
            or existing.created_at_ms != incoming.created_at_ms
        ):
            raise ValueError("reminder identity cannot change")


def _required_text(value: object, field_name: str) -> str:
    return _string_value(value, field_name, allow_empty=False)


def _optional_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _string_value(value, field_name, allow_empty=False)


def reminder_from_row(row: Record) -> Reminder:
    return Reminder(
        reminder_id=_required_text(row["reminder_id"], "reminder_id"),
        owner_thread_id=_required_text(row["owner_thread_id"], "owner_thread_id"),
        anchor_message_id=_required_text(row["anchor_message_id"], "anchor_message_id"),
        title=_required_text(row["title"], "title"),
        state=ReminderState(_required_text(row["state"], "reminder.state")),
        next_fire_at_ms=cast(int | None, row["next_fire_at_ms"]),
        repeat_rule=_optional_text(row["repeat_rule"], "repeat_rule"),
        timezone=_required_text(row["timezone"], "timezone"),
        revision=cast(int, row["revision"]),
        last_occurrence_no=cast(int, row["last_occurrence_no"]),
        created_at_ms=cast(int, row["created_at_ms"]),
        updated_at_ms=cast(int, row["updated_at_ms"]),
        last_fired_at_ms=cast(int | None, row["last_fired_at_ms"]),
        canceled_at_ms=cast(int | None, row["canceled_at_ms"]),
    )


def _string_value(value: object, field_name: str, *, allow_empty: bool) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        requirement = "a string" if allow_empty else "a non-empty string"
        raise ValueError(f"{field_name} must be {requirement}")
    return value
