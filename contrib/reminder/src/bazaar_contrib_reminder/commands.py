from __future__ import annotations

from collections.abc import Callable

from bazaar_contrib_messages import Actor, Actors, Agent, Thread, ThreadConcurrency
from bazaar_contrib_messages.models import Message
from bazaar_contrib_messages.states import MessageDirection, RuntimeEventState

from .audit import ReminderAudit
from .clock import now_ms
from .models import (
    Reminder,
    ReminderState,
)
from .reminder import (
    ReminderCancelRequest,
    ReminderCancelResult,
    ReminderListRequest,
    ReminderListResult,
    ReminderScheduleRequest,
    ReminderScheduleResult,
    ReminderSnoozeRequest,
    ReminderSnoozeResult,
    ReminderUpdateRequest,
    ReminderUpdateResult,
)
from .scheduler import reminder_audit_metadata, reminder_correlation
from .store import ReminderStore


async def threads_in_reach(storage: ReminderStore, actor: Actor) -> tuple[Thread, ...]:
    match actor:
        case Thread():
            return (actor,)
        case Agent():
            return await storage.list_thread_ids()


class ReminderCommandFailure(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        next_action: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.next_action = next_action


class ReminderCommands:
    """The commands about reminders: what wakes the agent later, and when.
    Their locks are the scheduler's, so a reminder is never fired and
    changed at once."""

    def __init__(
        self,
        *,
        actors: Actors,
        storage: ReminderStore,
        audit: ReminderAudit,
        poke: Callable[[], None],
        reminder_concurrency: ThreadConcurrency,
        clock: Callable[[], int] = now_ms,
    ) -> None:
        self._actors = actors
        self._storage = storage
        self._audit = audit
        self._clock = clock
        self._poke = poke
        self._reminder_concurrency = reminder_concurrency

    async def schedule_reminder(
        self,
        actor: Actor,
        request: ReminderScheduleRequest,
    ) -> ReminderScheduleResult:
        owner_id, anchor = await self._resolve_anchor(
            self._storage,
            actor,
            request.message_id,
        )
        async with self._reminder_concurrency.for_thread(owner_id):
            now_ms = self._clock()
            reminder = Reminder(
                reminder_id="pending",
                owner_thread_id=owner_id.id,
                anchor_message_id=anchor.message_id,
                title=request.title,
                state=ReminderState.SCHEDULED,
                next_fire_at_ms=request.next_fire_at_ms,
                repeat_rule=request.repeat_rule,
                timezone=request.timezone,
                revision=1,
                last_occurrence_no=0,
                created_at_ms=now_ms,
                updated_at_ms=now_ms,
            )
            reminder = await self._storage.save_new_reminder(reminder)
        self._poke()
        await self._append_audit(
            event_name="reminder.scheduled", reminder=reminder, anchor=anchor
        )
        return ReminderScheduleResult(reminder)

    async def list_reminders(
        self,
        actor: Actor,
        request: ReminderListRequest,
    ) -> ReminderListResult:
        listed: list[Reminder] = []
        for thread_id in await threads_in_reach(self._storage, actor):
            listed.extend(
                await self._storage.list_reminders(thread_id, request.statuses)
            )
        return ReminderListResult(tuple(listed))

    async def snooze_reminder(
        self,
        actor: Actor,
        request: ReminderSnoozeRequest,
    ) -> ReminderSnoozeResult:
        owner_id, _ = await self._resolve_reminder(
            self._storage,
            actor,
            request.reminder_id,
        )
        async with self._reminder_concurrency.for_thread(owner_id):
            reminder = await self._held_reminder(owner_id, request.reminder_id)
            try:
                updated = reminder.snooze(
                    duration_ms=request.duration_ms,
                    at_ms=request.evaluated_at_ms,
                )
            except ValueError as error:
                raise ReminderCommandFailure(
                    "REMINDER_NOT_SCHEDULED",
                    str(error),
                    next_action="Create a new Reminder if this Reminder is no longer reusable.",
                ) from error
            # what the audit will say about it is gathered before the change
            # is committed, so a read that fails cannot fail a change that stood
            anchor = await self._anchor_of(reminder)
            updated = await self._storage.save_reminder_transition(
                reminder.revision,
                updated,
            )
        self._poke()
        await self._append_audit(
            event_name="reminder.snoozed", reminder=updated, anchor=anchor
        )
        return ReminderSnoozeResult(updated)

    async def update_reminder(
        self,
        actor: Actor,
        request: ReminderUpdateRequest,
    ) -> ReminderUpdateResult:
        owner_id, _ = await self._resolve_reminder(
            self._storage,
            actor,
            request.reminder_id,
        )
        async with self._reminder_concurrency.for_thread(owner_id):
            reminder = await self._held_reminder(owner_id, request.reminder_id)
            if reminder.state is not ReminderState.SCHEDULED:
                next_action = (
                    "Run `bcc reminder snooze` first, or create a new Reminder."
                    if reminder.state is ReminderState.FIRED
                    else "Create a new Reminder if follow-up is still needed."
                )
                raise ReminderCommandFailure(
                    "REMINDER_UPDATE_FAILED",
                    f"Only a scheduled Reminder can be updated; current state is {reminder.state.value}.",
                    next_action=next_action,
                )
            try:
                if request.title is not None:
                    updated = reminder.update_title(
                        request.title,
                        at_ms=request.evaluated_at_ms,
                    )
                elif request.next_fire_at_ms is not None:
                    updated = reminder.update_next_fire(
                        request.next_fire_at_ms,
                        at_ms=request.evaluated_at_ms,
                    )
                elif request.repeat_rule is not None:
                    updated = reminder.update_cadence(
                        request.repeat_rule,
                        at_ms=request.evaluated_at_ms,
                    )
                else:
                    raise AssertionError("validated Reminder update has no field")
            except ValueError as error:
                raise ReminderCommandFailure(
                    "REMINDER_UPDATE_FAILED",
                    str(error),
                ) from error
            # what the audit will say about it is gathered before the change
            # is committed, so a read that fails cannot fail a change that stood
            anchor = await self._anchor_of(reminder)
            updated = await self._storage.save_reminder_transition(
                reminder.revision,
                updated,
            )
        self._poke()
        await self._append_audit(
            event_name="reminder.updated", reminder=updated, anchor=anchor
        )
        return ReminderUpdateResult(updated)

    async def cancel_reminder(
        self,
        actor: Actor,
        request: ReminderCancelRequest,
    ) -> ReminderCancelResult:
        owner_id, _ = await self._resolve_reminder(
            self._storage,
            actor,
            request.reminder_id,
        )
        async with self._reminder_concurrency.for_thread(owner_id):
            reminder = await self._held_reminder(owner_id, request.reminder_id)
            if reminder.state is not ReminderState.SCHEDULED:
                raise ReminderCommandFailure(
                    "REMINDER_NOT_SCHEDULED",
                    f"Only a scheduled Reminder can be canceled; current state is {reminder.state.value}.",
                )
            try:
                updated = reminder.cancel(at_ms=request.evaluated_at_ms)
            except ValueError as error:
                raise ReminderCommandFailure(
                    "REMINDER_NOT_SCHEDULED",
                    str(error),
                ) from error
            # what the audit will say about it is gathered before the change
            # is committed, so a read that fails cannot fail a change that stood
            anchor = await self._anchor_of(reminder)
            updated = await self._storage.save_reminder_transition(
                reminder.revision,
                updated,
            )
        self._poke()
        await self._append_audit(
            event_name="reminder.canceled", reminder=updated, anchor=anchor
        )
        return ReminderCancelResult(updated)

    async def _anchor_of(self, reminder: Reminder) -> Message | None:
        return await self._storage.resolve_message(
            Thread(reminder.owner_thread_id),
            reminder.anchor_message_id,
            direction=MessageDirection.INBOUND,
        )

    async def _append_audit(
        self,
        *,
        event_name: str,
        reminder: Reminder,
        anchor: Message | None,
    ) -> None:
        await self._audit.append(
            event_name=event_name,
            state=RuntimeEventState.COMPLETED,
            correlation=reminder_correlation(
                Agent(self._actors.agent_id), reminder, anchor
            ),
            metadata=reminder_audit_metadata(reminder, anchor),
        )

    @staticmethod
    async def _resolve_anchor(
        storage: ReminderStore,
        actor: Actor,
        message_id: str,
    ) -> tuple[Thread, Message]:
        """Return the conversation a Reminder will belong to, and its anchor."""

        for thread_id in await threads_in_reach(storage, actor):
            try:
                anchor = await storage.resolve_message(
                    thread_id,
                    message_id,
                    direction=MessageDirection.INBOUND,
                )
            except ValueError as error:
                raise ReminderCommandFailure(
                    "REMINDER_ANCHOR_NOT_FOUND",
                    str(error),
                ) from error
            if anchor is not None:
                return thread_id, anchor
        raise ReminderCommandFailure(
            "REMINDER_ANCHOR_NOT_FOUND",
            f"Reminder anchor was not found in reach: {message_id}",
        )

    async def _held_reminder(self, owner_id: Thread, reminder_id: str) -> Reminder:
        """Read the Reminder again now that its conversation is held."""

        reminder = await self._storage.get_reminder(owner_id, reminder_id)
        if reminder is None:
            raise ReminderCommandFailure(
                "REMINDER_NOT_FOUND",
                f"Reminder was not found in reach: {reminder_id}",
            )
        return reminder

    @staticmethod
    async def _resolve_reminder(
        storage: ReminderStore,
        actor: Actor,
        reminder_id: str,
    ) -> tuple[Thread, Reminder]:
        for thread_id in await threads_in_reach(storage, actor):
            try:
                reminder = await storage.get_reminder(thread_id, reminder_id)
            except ValueError as error:
                raise ReminderCommandFailure(
                    "REMINDER_NOT_FOUND",
                    str(error),
                ) from error
            if reminder is not None:
                return thread_id, reminder
        raise ReminderCommandFailure(
            "REMINDER_NOT_FOUND",
            f"Reminder was not found in reach: {reminder_id}",
        )


__all__ = ["ReminderCommandFailure", "ReminderCommands"]
