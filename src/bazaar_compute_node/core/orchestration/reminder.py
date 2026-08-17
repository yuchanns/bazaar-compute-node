from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from time import time_ns
from uuid import uuid7

from ..concurrency import ISessionConcurrency
from ..lifecycle import IAsyncLifecycle
from ..models import (
    OwnedReminder,
    OwnedReminderOccurrence,
    ReminderOccurrence,
    ReminderOwner,
    ReminderState,
)
from ..reminder import next_recurrence_ms
from ..storage import IStorage
from ..timerwheel import (
    Timer,
    TimerCancelledError,
    TimerWheel,
    TimerWheelClosedError,
)

_WALL_CLOCK_RECHECK_MS = 60_000
_DUE_BATCH_SIZE = 100


def _current_time_ms() -> int:
    return time_ns() // 1_000_000


class ReminderScheduler(IAsyncLifecycle):
    """Materialize durable Reminder occurrences with one global frontier timer."""

    def __init__(
        self,
        *,
        storage: IStorage,
        timer_wheel: TimerWheel,
        concurrency: ISessionConcurrency,
        publish_wake: Callable[[str, str], Awaitable[bool]],
        clock: Callable[[], int] | None = None,
    ) -> None:
        self._storage = storage
        self._timer_wheel = timer_wheel
        self._concurrency = concurrency
        self._publish_wake = publish_wake
        self._clock = clock or _current_time_ms
        self._poke = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._active_timer: Timer | None = None
        self._started = False
        self._stopping = False
        self._logger = logging.getLogger("bazaar_compute_node.orchestration.reminder")

    @property
    def active_timer(self) -> Timer | None:
        return self._active_timer

    def poke(self) -> None:
        if not self._stopping:
            self._poke.set()

    async def start(self, *, timeout: float) -> None:
        if self._started:
            return
        if self._stopping:
            raise RuntimeError("reminder scheduler is stopping")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._poke.clear()
        async with asyncio.timeout(timeout):
            await self._publish_pending_recovery()
            await self._materialize_due_batches()
        self._started = True
        self._task = asyncio.create_task(
            self._run(),
            name="bcn-reminder-scheduler",
        )

    async def stop(self, *, timeout: float) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        task = self._task
        if task is None and not self._started:
            return
        self._stopping = True
        self._poke.set()
        timer = self._active_timer
        if timer is not None and timer.active:
            timer.cancel()
        if task is not None:
            try:
                async with asyncio.timeout(timeout):
                    await task
            except TimeoutError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise
            except asyncio.CancelledError:
                raise
        self._task = None
        self._active_timer = None
        self._started = False

    async def _run(self) -> None:
        try:
            while not self._stopping:
                await self._materialize_due_batches()
                if self._stopping:
                    return
                async with self._storage.transaction() as transaction:
                    frontier = await transaction.get_next_scheduled_owned_reminder()
                if frontier is None:
                    await self._poke.wait()
                    self._poke.clear()
                    continue
                next_fire_at_ms = frontier.reminder.next_fire_at_ms
                if next_fire_at_ms is None:
                    raise RuntimeError(
                        "scheduled reminder frontier has no next fire time"
                    )
                remaining_ms = next_fire_at_ms - self._clock()
                if remaining_ms <= 0:
                    await asyncio.sleep(0)
                    continue
                delay_ms = min(
                    remaining_ms,
                    self._timer_wheel.maximum_delay_ms - self._timer_wheel.tick_ms,
                    _WALL_CLOCK_RECHECK_MS,
                )
                await self._wait_for_frontier(delay_ms)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._logger.exception("reminder scheduler failed")
            raise

    async def _wait_for_frontier(self, delay_ms: int) -> None:
        timer = self._timer_wheel.create(delay_ms)
        self._active_timer = timer
        timer_task = asyncio.create_task(timer.wait())
        poke_task = asyncio.create_task(self._poke.wait())
        try:
            done, _ = await asyncio.wait(
                (timer_task, poke_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if poke_task in done:
                poke_task.result()
                self._poke.clear()
            if timer_task in done:
                try:
                    timer_task.result()
                except TimerCancelledError:
                    pass
                except TimerWheelClosedError:
                    if not self._stopping:
                        raise
        finally:
            if not timer_task.done():
                timer_task.cancel()
            if not poke_task.done():
                poke_task.cancel()
            await asyncio.gather(timer_task, poke_task, return_exceptions=True)
            if timer.active:
                timer.cancel()
            if self._active_timer is timer:
                self._active_timer = None

    async def _publish_pending_recovery(self) -> None:
        async with self._storage.transaction() as transaction:
            owners = await transaction.list_pending_reminder_owners()
        for owner in owners:
            await self._publish_owner(owner)

    async def _materialize_due_batches(self) -> None:
        while not self._stopping:
            now_ms = self._clock()
            async with self._storage.transaction() as transaction:
                due = await transaction.list_due_owned_reminders(
                    now_ms,
                    limit=_DUE_BATCH_SIZE,
                )
            if not due:
                return
            owners: set[ReminderOwner] = set()
            for reminder in due:
                owner = await self._materialize_due_reminder(reminder)
                if owner is not None:
                    owners.add(owner)
            for owner in sorted(
                owners,
                key=lambda item: (item.agent_id, item.owner_session_id),
            ):
                await self._publish_owner(owner)
            await asyncio.sleep(0)

    async def _materialize_due_reminder(
        self,
        snapshot: OwnedReminder,
    ) -> ReminderOwner | None:
        owner = snapshot.owner
        async with (
            self._concurrency.for_session(owner.owner_session_id),
            self._storage.transaction() as transaction,
        ):
            current_owned = await transaction.get_owned_reminder(
                owner.agent_id,
                owner.owner_session_id,
                snapshot.reminder.reminder_id,
            )
            if current_owned is None:
                return None
            current = current_owned.reminder
            if (
                current.state is not ReminderState.SCHEDULED
                or current.revision != snapshot.reminder.revision
                or current.next_fire_at_ms != snapshot.reminder.next_fire_at_ms
            ):
                return None
            scheduled_for_ms = current.next_fire_at_ms
            if scheduled_for_ms is None:
                raise RuntimeError("scheduled reminder has no next fire time")
            fired_at_ms = self._clock()
            if fired_at_ms < scheduled_for_ms:
                return None
            next_fire_at_ms = (
                next_recurrence_ms(
                    scheduled_for_ms=scheduled_for_ms,
                    repeat_rule=current.repeat_rule,
                    timezone=current.timezone,
                )
                if current.repeat_rule is not None
                else None
            )
            fired = current.record_fire(
                scheduled_for_ms=scheduled_for_ms,
                fired_at_ms=fired_at_ms,
                next_fire_at_ms=next_fire_at_ms,
            )
            occurrence = ReminderOccurrence(
                occurrence_id=str(uuid7()),
                reminder_id=current.reminder_id,
                owner_session_id=current.owner_session_id,
                occurrence_no=current.last_occurrence_no + 1,
                anchor_message_id=current.anchor_message_id,
                scheduled_for_ms=scheduled_for_ms,
                fired_at_ms=fired_at_ms,
                next_fire_at_ms=next_fire_at_ms,
                overdue=fired_at_ms > scheduled_for_ms,
                read_at_ms=None,
                created_at_ms=fired_at_ms,
            )
            await transaction.save_owned_fired_occurrence(
                current.revision,
                OwnedReminder(owner.agent_id, fired),
                OwnedReminderOccurrence(owner.agent_id, occurrence),
            )
            return owner

    async def _publish_owner(self, owner: ReminderOwner) -> None:
        try:
            await self._publish_wake(owner.agent_id, owner.owner_session_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._logger.exception(
                "reminder wake publish failed",
                extra={
                    "agent_id": owner.agent_id,
                    "owner_session_id": owner.owner_session_id,
                },
            )


__all__ = ["ReminderScheduler"]
