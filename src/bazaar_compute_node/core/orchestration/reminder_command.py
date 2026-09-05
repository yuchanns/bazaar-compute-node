from __future__ import annotations

from collections.abc import Callable

from ..actor import Actor
from ..command import IReminderService
from ..concurrency import IThreadConcurrency
from ..models import Message, MessageDirection, Reminder, ReminderState
from ..reminder import (
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
from ..storage import IStorage
from ..utils.clock import now_ms
from .services import threads_in_reach


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


class ReminderCommandService(IReminderService):
    """Execute session-owned Reminder commands over the durable storage port."""

    def __init__(
        self,
        *,
        storage: IStorage,
        concurrency: IThreadConcurrency,
        poke: Callable[[], None],
        clock: Callable[[], int] | None = None,
    ) -> None:
        self._storage = storage
        self._concurrency = concurrency
        self._poke = poke
        self._clock = clock or now_ms

    async def schedule(
        self,
        actor: Actor,
        request: ReminderScheduleRequest,
    ) -> ReminderScheduleResult:
        owner_id, anchor = await self._resolve_anchor(
            self._storage,
            actor,
            request.message_id,
        )
        async with self._concurrency.for_thread(owner_id):
            now_ms = self._clock()
            reminder = Reminder(
                reminder_id="pending",
                owner_thread_id=owner_id,
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
        return ReminderScheduleResult(reminder)

    async def list(
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

    async def snooze(
        self,
        actor: Actor,
        request: ReminderSnoozeRequest,
    ) -> ReminderSnoozeResult:
        owner_id, reminder = await self._resolve_reminder(
            self._storage,
            actor,
            request.reminder_id,
        )
        async with self._concurrency.for_thread(owner_id):
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
            updated = await self._storage.save_reminder_transition(
                reminder.revision,
                updated,
            )
        self._poke()
        return ReminderSnoozeResult(updated)

    async def update(
        self,
        actor: Actor,
        request: ReminderUpdateRequest,
    ) -> ReminderUpdateResult:
        owner_id, reminder = await self._resolve_reminder(
            self._storage,
            actor,
            request.reminder_id,
        )
        async with self._concurrency.for_thread(owner_id):
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
            updated = await self._storage.save_reminder_transition(
                reminder.revision,
                updated,
            )
        self._poke()
        return ReminderUpdateResult(updated)

    async def cancel(
        self,
        actor: Actor,
        request: ReminderCancelRequest,
    ) -> ReminderCancelResult:
        owner_id, reminder = await self._resolve_reminder(
            self._storage,
            actor,
            request.reminder_id,
        )
        async with self._concurrency.for_thread(owner_id):
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
            updated = await self._storage.save_reminder_transition(
                reminder.revision,
                updated,
            )
        self._poke()
        return ReminderCancelResult(updated)

    @staticmethod
    async def _resolve_anchor(
        storage: IStorage,
        actor: Actor,
        message_id: str,
    ) -> tuple[str, Message]:
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

    @staticmethod
    async def _resolve_reminder(
        storage: IStorage,
        actor: Actor,
        reminder_id: str,
    ) -> tuple[str, Reminder]:
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


__all__ = ["ReminderCommandFailure", "ReminderCommandService"]
