from __future__ import annotations

from collections.abc import Callable
from time import time_ns

from ..command import IReminderService, SessionNotFoundError
from ..concurrency import ISessionConcurrency
from ..models import InboundMessage, Reminder, ReminderOccurrence, ReminderState
from ..reminder import (
    ReminderCancelRequest,
    ReminderCancelResult,
    ReminderCheckItem,
    ReminderCheckRequest,
    ReminderCheckResult,
    ReminderListRequest,
    ReminderListResult,
    ReminderScheduleRequest,
    ReminderScheduleResult,
    ReminderSnoozeRequest,
    ReminderSnoozeResult,
    ReminderUpdateRequest,
    ReminderUpdateResult,
)
from ..storage import IStorage, IStorageTransaction


def _current_time_ms() -> int:
    return time_ns() // 1_000_000


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
        concurrency: ISessionConcurrency,
        poke: Callable[[], None],
        clock: Callable[[], int] | None = None,
    ) -> None:
        self._storage = storage
        self._concurrency = concurrency
        self._poke = poke
        self._clock = clock or _current_time_ms

    async def schedule(
        self,
        session_id: str,
        request: ReminderScheduleRequest,
    ) -> ReminderScheduleResult:
        async with (
            self._concurrency.for_session(session_id),
            self._storage.transaction() as transaction,
        ):
            await self._require_session(transaction, session_id)
            anchor = await self._resolve_anchor(
                transaction,
                session_id,
                request.message_id,
            )
            now_ms = self._clock()
            reminder = Reminder(
                reminder_id="pending",
                owner_session_id=session_id,
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
            reminder = await transaction.save_new_reminder(reminder)
        self._poke()
        return ReminderScheduleResult(reminder)

    async def check(
        self,
        session_id: str,
        request: ReminderCheckRequest,
    ) -> ReminderCheckResult:
        try:
            async with (
                self._concurrency.for_session(session_id),
                self._storage.transaction() as transaction,
            ):
                await self._require_session(transaction, session_id)
                occurrences = await transaction.list_pending_reminder_occurrences(
                    session_id,
                    limit=request.limit,
                )
                if not occurrences:
                    return ReminderCheckResult(items=(), has_more=False)

                snapshots: list[tuple[ReminderOccurrence, str, str]] = []
                for occurrence in occurrences:
                    reminder = await transaction.get_reminder(
                        session_id,
                        occurrence.reminder_id,
                    )
                    if reminder is None:
                        raise ReminderCommandFailure(
                            "REMINDER_CHECK_FAILED",
                            f"Reminder definition is missing: {occurrence.reminder_id}",
                        )
                    anchor = await transaction.resolve_inbound_message(
                        session_id,
                        occurrence.anchor_message_id,
                    )
                    if anchor is None:
                        raise ReminderCommandFailure(
                            "REMINDER_CHECK_FAILED",
                            f"Reminder anchor is missing: {occurrence.anchor_message_id}",
                        )
                    snapshots.append(
                        (occurrence, reminder.title, anchor.canonical_target)
                    )

                marked = await transaction.mark_reminder_occurrences_read(
                    session_id,
                    tuple(occurrence.occurrence_id for occurrence in occurrences),
                    read_at_ms=self._clock(),
                )
                marked_by_id = {
                    occurrence.occurrence_id: occurrence for occurrence in marked
                }
                items = tuple(
                    ReminderCheckItem(
                        occurrence=marked_by_id[occurrence.occurrence_id],
                        title=title,
                        canonical_target=canonical_target,
                    )
                    for occurrence, title, canonical_target in snapshots
                )
                has_more = (
                    await transaction.count_pending_reminder_occurrences(session_id) > 0
                )
            return ReminderCheckResult(items=items, has_more=has_more)
        except SessionNotFoundError:
            raise
        except ReminderCommandFailure:
            raise
        except (TypeError, ValueError) as error:
            raise ReminderCommandFailure(
                "REMINDER_CHECK_FAILED",
                str(error),
            ) from error

    async def list(
        self,
        session_id: str,
        request: ReminderListRequest,
    ) -> ReminderListResult:
        async with self._storage.transaction() as transaction:
            await self._require_session(transaction, session_id)
            reminders = await transaction.list_reminders(
                session_id,
                request.statuses,
            )
        return ReminderListResult(reminders)

    async def snooze(
        self,
        session_id: str,
        request: ReminderSnoozeRequest,
    ) -> ReminderSnoozeResult:
        async with (
            self._concurrency.for_session(session_id),
            self._storage.transaction() as transaction,
        ):
            await self._require_session(transaction, session_id)
            reminder = await self._resolve_reminder(
                transaction,
                session_id,
                request.reminder_id,
            )
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
            updated = await transaction.save_reminder_transition(
                reminder.revision,
                updated,
            )
        self._poke()
        return ReminderSnoozeResult(updated)

    async def update(
        self,
        session_id: str,
        request: ReminderUpdateRequest,
    ) -> ReminderUpdateResult:
        async with (
            self._concurrency.for_session(session_id),
            self._storage.transaction() as transaction,
        ):
            await self._require_session(transaction, session_id)
            reminder = await self._resolve_reminder(
                transaction,
                session_id,
                request.reminder_id,
            )
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
            updated = await transaction.save_reminder_transition(
                reminder.revision,
                updated,
            )
        self._poke()
        return ReminderUpdateResult(updated)

    async def cancel(
        self,
        session_id: str,
        request: ReminderCancelRequest,
    ) -> ReminderCancelResult:
        async with (
            self._concurrency.for_session(session_id),
            self._storage.transaction() as transaction,
        ):
            await self._require_session(transaction, session_id)
            reminder = await self._resolve_reminder(
                transaction,
                session_id,
                request.reminder_id,
            )
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
            updated = await transaction.save_reminder_transition(
                reminder.revision,
                updated,
            )
        self._poke()
        return ReminderCancelResult(updated)

    @staticmethod
    async def _require_session(
        transaction: IStorageTransaction,
        session_id: str,
    ) -> None:
        if await transaction.get_bcn_session(session_id) is None:
            raise SessionNotFoundError(f"unknown bcn session: {session_id}")

    @staticmethod
    async def _resolve_anchor(
        transaction: IStorageTransaction,
        session_id: str,
        message_id: str,
    ) -> InboundMessage:
        try:
            anchor = await transaction.resolve_inbound_message(session_id, message_id)
        except ValueError as error:
            raise ReminderCommandFailure(
                "REMINDER_ANCHOR_NOT_FOUND",
                str(error),
            ) from error
        if anchor is None:
            raise ReminderCommandFailure(
                "REMINDER_ANCHOR_NOT_FOUND",
                f"Reminder anchor was not found in the current session: {message_id}",
            )
        return anchor

    @staticmethod
    async def _resolve_reminder(
        transaction: IStorageTransaction,
        session_id: str,
        reminder_id: str,
    ) -> Reminder:
        try:
            reminder = await transaction.get_reminder(session_id, reminder_id)
        except ValueError as error:
            raise ReminderCommandFailure(
                "REMINDER_NOT_FOUND",
                str(error),
            ) from error
        if reminder is None:
            raise ReminderCommandFailure(
                "REMINDER_NOT_FOUND",
                f"Reminder was not found in the current session: {reminder_id}",
            )
        return reminder


__all__ = ["ReminderCommandFailure", "ReminderCommandService"]
