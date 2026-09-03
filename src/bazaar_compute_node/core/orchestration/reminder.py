from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from uuid import uuid7

from ..concurrency import ISessionConcurrency
from ..lifecycle import IAsyncLifecycle, TaskFailureSignal
from ..models import (
    InboundAttachment,
    Message,
    MessageDirection,
    OwnedReminder,
    Reminder,
    ReminderState,
    SenderIdentity,
    SenderKind,
    SystemMessageKind,
)
from ..reminder import next_recurrence_ms, render_reminder_fire_body
from ..storage import IStorage, IStorageScope
from ..timerwheel import (
    Timer,
    TimerCancelledError,
    TimerWheel,
    TimerWheelClosedError,
)
from ..utils.clock import now_ms

_WALL_CLOCK_RECHECK_MS = 60_000
_CYCLE_RETRY_MS = 5_000
_DUE_BATCH_SIZE = 100


async def resolve_reminder_anchor(
    storage: IStorageScope,
    agent_id: str,
    message: Message,
) -> Message | None:
    """Return the inbound message a fired Reminder speaks for.

    A Reminder system message carries no provider identity of its own, so
    everything the runtime addresses back to the Channel — approval prompts,
    error feedback — has to resolve the human anchor the Reminder was
    scheduled against. Non-Reminder messages speak for themselves.
    """
    if message.system_message_kind is not SystemMessageKind.REMINDER:
        return message
    reminder_id = message.metadata.get("reminder_id")
    if not isinstance(reminder_id, str):
        return None
    try:
        reminder = await storage.get_reminder(message.session_id, reminder_id)
    except ValueError:
        return None
    if reminder is None:
        return None
    return await storage.get_owned_message(
        agent_id,
        reminder.owner_session_id,
        reminder.anchor_message_id,
        direction=MessageDirection.INBOUND,
    )


class ReminderScheduler(IAsyncLifecycle):
    """Materialize durable Reminder occurrences with one global frontier timer."""

    def __init__(
        self,
        *,
        storage: IStorage,
        timer_wheel: TimerWheel,
        concurrency: ISessionConcurrency,
        publish_wake: Callable[[str, Message[InboundAttachment]], Awaitable[bool]],
        clock: Callable[[], int] | None = None,
    ) -> None:
        self._storage = storage
        self._timer_wheel = timer_wheel
        self._concurrency = concurrency
        self._publish_wake = publish_wake
        self._clock = clock or now_ms
        self._poke = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._failure = TaskFailureSignal()
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
        self._failure.reset()
        self._task = asyncio.create_task(
            self._run(),
            name="bcn-reminder-scheduler",
        )
        self._failure.observe(self._task, component="reminder scheduler")

    async def stop(self, *, timeout: float) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        task = self._task
        if task is None and not self._started:
            return
        self._failure.disable()
        self._stopping = True
        self._poke.set()
        timer = self._active_timer
        if timer is not None and timer.active:
            timer.cancel()
        if task is not None:
            if task.done():
                await asyncio.gather(task, return_exceptions=True)
                self._task = None
                self._active_timer = None
                self._started = False
                return
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

    async def wait_failure(self) -> None:
        await self._failure.wait()

    async def _run(self) -> None:
        while not self._stopping:
            try:
                await self._materialize_due_batches()
                if self._stopping:
                    return
                frontier = await self._storage.get_next_scheduled_owned_reminder()
                if frontier is None:
                    await self._poke.wait()
                    self._poke.clear()
                    continue
                next_fire_at_ms = frontier.reminder.next_fire_at_ms
                if next_fire_at_ms is None:
                    await self._poke.wait()
                    self._poke.clear()
                    continue
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
                self._logger.exception("reminder cycle failed; retrying")
                try:
                    await asyncio.wait_for(
                        self._poke.wait(),
                        _CYCLE_RETRY_MS / 1_000,
                    )
                except TimeoutError:
                    pass
                self._poke.clear()

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
        owners = await self._storage.list_unread_message_owners()
        for owner in owners:
            await self._publish_message(owner.agent_id, owner.trigger_message)

    async def _materialize_due_batches(self) -> None:
        while not self._stopping:
            now_ms = self._clock()
            due = await self._storage.list_due_owned_reminders(
                now_ms,
                limit=_DUE_BATCH_SIZE,
            )
            if not due:
                return
            materialized: list[tuple[str, Message[InboundAttachment]]] = []
            try:
                for reminder in due:
                    result = await self._materialize_due_reminder(reminder)
                    if result is not None:
                        materialized.append(result)
            finally:
                # a reminder that already fired is no longer due, and pending
                # recovery only runs at startup, so anything committed before
                # the failure has to be woken on the way out
                for agent_id, message in sorted(
                    materialized,
                    key=lambda item: (item[0], item[1].session_id, item[1].seq),
                ):
                    await self._publish_message(agent_id, message)
            await asyncio.sleep(0)

    async def _materialize_due_reminder(
        self,
        snapshot: OwnedReminder,
    ) -> tuple[str, Message[InboundAttachment]] | None:
        owner = snapshot.owner
        async with self._concurrency.for_session(owner.owner_session_id):
            current_owned = await self._storage.get_owned_reminder(
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
                return None
            fired_at_ms = self._clock()
            if fired_at_ms < scheduled_for_ms:
                return None
            try:
                next_fire_at_ms = (
                    next_recurrence_ms(
                        scheduled_for_ms=scheduled_for_ms,
                        repeat_rule=current.repeat_rule,
                        timezone=current.timezone,
                    )
                    if current.repeat_rule is not None
                    else None
                )
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001
                await self._cancel_unusable_reminder(
                    owner.agent_id,
                    current,
                    "reminder recurrence is invalid",
                    at_ms=fired_at_ms,
                    error=error,
                )
                return None
            fired = current.record_fire(
                scheduled_for_ms=scheduled_for_ms,
                fired_at_ms=fired_at_ms,
                next_fire_at_ms=next_fire_at_ms,
            )
            anchor = await self._storage.get_owned_message(
                owner.agent_id,
                current.owner_session_id,
                current.anchor_message_id,
                direction=MessageDirection.INBOUND,
            )
            if anchor is None:
                await self._cancel_unusable_reminder(
                    owner.agent_id,
                    current,
                    "reminder anchor is missing",
                    at_ms=fired_at_ms,
                )
                return None
            system_message = Message[InboundAttachment](
                direction=MessageDirection.INBOUND,
                seq=0,
                message_id=str(uuid7()),
                session_id=current.owner_session_id,
                channel_session_id=anchor.channel_session_id,
                channel=anchor.channel,
                provider_thread_id=anchor.provider_thread_id,
                provider_message_id=None,
                provider_time_ms=None,
                received_at_ms=fired_at_ms,
                sender=SenderIdentity(name="system"),
                message_type="text",
                target=anchor.target,
                target_kind=anchor.target_kind,
                body=render_reminder_fire_body(
                    fired,
                    anchor.target,
                    next_fire_at_ms,
                ),
                mentions_agent=False,
                notifies_runtime=True,
                metadata={
                    "sender_kind": SenderKind.SYSTEM.value,
                    "system_message_kind": SystemMessageKind.REMINDER.value,
                    "reminder_id": current.reminder_id,
                },
            )
            materialized = await self._storage.materialize_owned_reminder_message(
                current.revision,
                OwnedReminder(owner.agent_id, fired),
                system_message,
            )
            if materialized is None:
                return None
            return owner.agent_id, materialized

    async def _cancel_unusable_reminder(
        self,
        agent_id: str,
        reminder: Reminder,
        reason: str,
        *,
        at_ms: int | None = None,
        error: BaseException | None = None,
    ) -> None:
        """Retire one Reminder that can never fire, so the frontier keeps moving."""

        canceled = reminder.cancel(at_ms=at_ms if at_ms is not None else self._clock())
        retired = await self._storage.save_owned_reminder_transition(
            reminder.revision,
            OwnedReminder(agent_id, canceled),
        )
        if retired is None:
            return
        self._logger.error(
            "%s; reminder canceled",
            reason,
            extra={
                "agent_id": agent_id,
                "owner_session_id": reminder.owner_session_id,
                "reminder_id": reminder.reminder_id,
                "anchor_message_id": reminder.anchor_message_id,
            },
            exc_info=(
                (type(error), error, error.__traceback__) if error is not None else None
            ),
        )

    async def _publish_message(
        self,
        agent_id: str,
        message: Message[InboundAttachment],
    ) -> None:
        try:
            await self._publish_wake(agent_id, message)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._logger.exception(
                "reminder wake publish failed",
                extra={
                    "agent_id": agent_id,
                    "owner_session_id": message.session_id,
                },
            )


__all__ = ["ReminderScheduler", "resolve_reminder_anchor"]
