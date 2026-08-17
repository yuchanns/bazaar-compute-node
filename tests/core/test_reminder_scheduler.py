from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

import pytest
from bcn_test_support import MemoryStorage

from bazaar_compute_node.app.application import NodeApplication
from bazaar_compute_node.app.config import (
    AgentConfiguration,
    ChannelConfiguration,
    NodeConfiguration,
    RuntimeConfiguration,
)
from bazaar_compute_node.app.registry import AdapterRegistry
from bazaar_compute_node.core.concurrency import SessionLockRegistry
from bazaar_compute_node.core.models import (
    BcnSession,
    Reminder,
    ReminderOccurrence,
    ReminderState,
)
from bazaar_compute_node.core.orchestration.reminder import ReminderScheduler
from bazaar_compute_node.core.timerwheel import TimerWheel

_AGENT_A = "agent-a"
_AGENT_B = "agent-b"
_SESSION_A = "session-a"
_SESSION_B = "session-b"
_SESSION_C = "session-c"
_REMINDER_A = "018f0000-0000-7000-8000-000000000001"
_REMINDER_B = "018f0000-0000-7000-8000-000000000002"
_REMINDER_C = "018f0000-0000-7000-8000-000000000003"


def add_session(storage: MemoryStorage, *, agent_id: str, session_id: str) -> None:
    storage.bcn_sessions[session_id] = BcnSession(
        id=session_id,
        channel_session_id=f"channel-{session_id}",
        workspace_id=agent_id,
        created_at_ms=0,
        updated_at_ms=0,
    )


def make_scheduled_reminder(
    reminder_id: str,
    *,
    session_id: str,
    next_fire_at_ms: int = 100,
) -> Reminder:
    return Reminder(
        reminder_id=reminder_id,
        owner_session_id=session_id,
        anchor_message_id=f"anchor-{session_id}",
        title=f"Reminder {reminder_id}",
        state=ReminderState.SCHEDULED,
        next_fire_at_ms=next_fire_at_ms,
        repeat_rule=None,
        timezone="UTC",
        revision=1,
        last_occurrence_no=0,
        created_at_ms=0,
        updated_at_ms=0,
    )


def make_fired_reminder(reminder_id: str, *, session_id: str) -> Reminder:
    return Reminder(
        reminder_id=reminder_id,
        owner_session_id=session_id,
        anchor_message_id=f"anchor-{session_id}",
        title=f"Reminder {reminder_id}",
        state=ReminderState.FIRED,
        next_fire_at_ms=None,
        repeat_rule=None,
        timezone="UTC",
        revision=2,
        last_occurrence_no=1,
        created_at_ms=0,
        updated_at_ms=100,
        last_fired_at_ms=100,
    )


def make_pending_occurrence(
    reminder_id: str,
    *,
    session_id: str,
    occurrence_id: str,
) -> ReminderOccurrence:
    return ReminderOccurrence(
        occurrence_id=occurrence_id,
        reminder_id=reminder_id,
        owner_session_id=session_id,
        occurrence_no=1,
        anchor_message_id=f"anchor-{session_id}",
        scheduled_for_ms=100,
        fired_at_ms=100,
        next_fire_at_ms=None,
        overdue=False,
        read_at_ms=None,
        created_at_ms=100,
    )


async def start_scheduler(
    storage: MemoryStorage,
    *,
    publish_wake: Callable[[str, str], bool],
    clock: Callable[[], int] = lambda: 100,
) -> tuple[ReminderScheduler, TimerWheel]:
    async def publish(agent_id: str, session_id: str) -> bool:
        return publish_wake(agent_id, session_id)

    timer_wheel = TimerWheel()
    await timer_wheel.start()
    scheduler = ReminderScheduler(
        storage=storage,
        timer_wheel=timer_wheel,
        concurrency=SessionLockRegistry(),
        publish_wake=publish,
        clock=clock,
    )
    await scheduler.start(timeout=1)
    return scheduler, timer_wheel


async def stop_scheduler(
    scheduler: ReminderScheduler,
    timer_wheel: TimerWheel,
) -> None:
    await scheduler.stop(timeout=1)
    await timer_wheel.close()


@pytest.mark.asyncio
async def test_global_scheduler_routes_due_wakes_by_agent_and_session() -> None:
    storage = MemoryStorage()
    add_session(storage, agent_id=_AGENT_A, session_id=_SESSION_A)
    add_session(storage, agent_id=_AGENT_A, session_id=_SESSION_C)
    add_session(storage, agent_id=_AGENT_B, session_id=_SESSION_B)
    storage.reminders.update(
        {
            _REMINDER_A: make_scheduled_reminder(_REMINDER_A, session_id=_SESSION_A),
            _REMINDER_B: make_scheduled_reminder(_REMINDER_B, session_id=_SESSION_B),
            _REMINDER_C: make_scheduled_reminder(_REMINDER_C, session_id=_SESSION_C),
        }
    )
    wakes: list[tuple[str, str]] = []

    def publish(agent_id: str, session_id: str) -> bool:
        wakes.append((agent_id, session_id))
        return True

    scheduler, timer_wheel = await start_scheduler(storage, publish_wake=publish)
    try:
        assert wakes == [
            (_AGENT_A, _SESSION_A),
            (_AGENT_A, _SESSION_C),
            (_AGENT_B, _SESSION_B),
        ]
        assert {reminder.state for reminder in storage.reminders.values()} == {
            ReminderState.FIRED
        }
        assert len(storage.reminder_occurrences) == 3
    finally:
        await stop_scheduler(scheduler, timer_wheel)


@pytest.mark.asyncio
async def test_failed_wake_keeps_due_occurrence_unread() -> None:
    storage = MemoryStorage()
    add_session(storage, agent_id=_AGENT_A, session_id=_SESSION_A)
    storage.reminders[_REMINDER_A] = make_scheduled_reminder(
        _REMINDER_A, session_id=_SESSION_A
    )
    wakes: list[tuple[str, str]] = []

    def publish(agent_id: str, session_id: str) -> bool:
        wakes.append((agent_id, session_id))
        return False

    scheduler, timer_wheel = await start_scheduler(storage, publish_wake=publish)
    try:
        assert wakes == [(_AGENT_A, _SESSION_A)]
        async with storage.transaction() as transaction:
            owners = await transaction.list_pending_reminder_owners()
        assert [(owner.agent_id, owner.owner_session_id) for owner in owners] == [
            (_AGENT_A, _SESSION_A)
        ]
    finally:
        await stop_scheduler(scheduler, timer_wheel)


@pytest.mark.asyncio
async def test_pending_recovery_is_agent_scoped_and_failure_isolated() -> None:
    storage = MemoryStorage()
    add_session(storage, agent_id=_AGENT_A, session_id=_SESSION_A)
    add_session(storage, agent_id=_AGENT_B, session_id=_SESSION_B)
    storage.reminders.update(
        {
            _REMINDER_A: make_fired_reminder(_REMINDER_A, session_id=_SESSION_A),
            _REMINDER_B: make_fired_reminder(_REMINDER_B, session_id=_SESSION_B),
        }
    )
    storage.reminder_occurrences.update(
        {
            "occurrence-a": make_pending_occurrence(
                _REMINDER_A,
                session_id=_SESSION_A,
                occurrence_id="occurrence-a",
            ),
            "occurrence-b": make_pending_occurrence(
                _REMINDER_B,
                session_id=_SESSION_B,
                occurrence_id="occurrence-b",
            ),
        }
    )
    wakes: list[tuple[str, str]] = []

    def publish(agent_id: str, session_id: str) -> bool:
        wakes.append((agent_id, session_id))
        if agent_id == _AGENT_A:
            raise RuntimeError("agent is unavailable")
        return True

    scheduler, timer_wheel = await start_scheduler(storage, publish_wake=publish)
    try:
        assert wakes == [
            (_AGENT_A, _SESSION_A),
            (_AGENT_B, _SESSION_B),
        ]
        assert all(
            occurrence.pending for occurrence in storage.reminder_occurrences.values()
        )
        assert scheduler._task is not None
        await asyncio.sleep(0)
        assert not scheduler._task.done()
    finally:
        await stop_scheduler(scheduler, timer_wheel)


@pytest.mark.asyncio
async def test_scheduler_runs_when_all_agents_fail_to_start(tmp_path: Path) -> None:
    agent_id = "0198d4e6-29c5-7465-b74b-88db31f0c118"
    node = NodeApplication(
        configuration=NodeConfiguration(
            storage="test",
            audit="test",
            agents=(
                AgentConfiguration(
                    id=agent_id,
                    name="Failed Agent",
                    channel=ChannelConfiguration(kind="missing-channel"),
                    runtime=RuntimeConfiguration(kind="missing-runtime"),
                ),
            ),
        ),
        shared_factories=AdapterRegistry().load_shared(storage="test", audit="test"),
        endpoint_path=tmp_path / "bcn.sock",
    )

    await node.start()
    try:
        health = node._health()
        assert health["ready"] is True
        assert health["started_agents"] == 0
        assert health["failed_agents"] == 1
        assert node.reminder_scheduler._task is not None
        assert not node.reminder_scheduler._task.done()
    finally:
        await node.stop()
