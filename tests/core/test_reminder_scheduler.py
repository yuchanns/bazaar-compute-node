from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import cast

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
    ChannelSession,
    ChannelTargetKind,
    ConsumerCursor,
    InboundAttachment,
    Message,
    MessageDirection,
    Reminder,
    ReminderState,
    SenderIdentity,
    SenderKind,
    SystemMessageKind,
)
from bazaar_compute_node.core.models.reminder_owner import OwnedReminder
from bazaar_compute_node.core.orchestration.reminder import ReminderScheduler
from bazaar_compute_node.core.storage import IStorage
from bazaar_compute_node.core.timerwheel import TimerWheel

_AGENT_A = "agent-a"
_AGENT_B = "agent-b"
_SESSION_A = "session-a"
_SESSION_B = "session-b"
_SESSION_C = "session-c"
_REMINDER_A = "018f0000-0000-7000-8000-000000000001"
_REMINDER_B = "018f0000-0000-7000-8000-000000000002"
_REMINDER_C = "018f0000-0000-7000-8000-000000000003"


def add_session(storage: MemoryStorage, *, agent_id: str, session_id: str) -> str:
    channel_session_id = f"channel-{session_id}"
    storage.channel_sessions[channel_session_id] = ChannelSession(
        id=channel_session_id,
        channel="test",
        provider_thread_id=f"provider-{session_id}",
        created_at_ms=0,
        updated_at_ms=0,
        target_kind=ChannelTargetKind.DM,
    )
    storage.bcn_sessions[session_id] = BcnSession(
        id=session_id,
        channel_session_id=channel_session_id,
        workspace_id=agent_id,
        created_at_ms=0,
        updated_at_ms=0,
    )
    anchor_id = f"018f0000-0000-7000-8000-{len(storage.messages) + 10:012d}"
    storage.messages[session_id] = [
        Message[InboundAttachment](
            direction=MessageDirection.INBOUND,
            seq=len(storage.messages) + 1,
            message_id=anchor_id,
            session_id=session_id,
            channel_session_id=channel_session_id,
            channel="test",
            provider_thread_id=f"provider-{session_id}",
            provider_message_id=f"provider-message-{session_id}",
            received_at_ms=0,
            sender=SenderIdentity(name="human"),
            target=f"dm:{session_id}",
            target_kind=ChannelTargetKind.DM,
            body="anchor",
            metadata={"sender_kind": SenderKind.HUMAN.value},
        )
    ]
    return anchor_id


def make_scheduled_reminder(
    reminder_id: str,
    *,
    session_id: str,
    anchor_message_id: str,
    next_fire_at_ms: int = 100,
) -> Reminder:
    return Reminder(
        reminder_id=reminder_id,
        owner_session_id=session_id,
        anchor_message_id=anchor_message_id,
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


def _recording_publish(
    wakes: list[tuple[str, Message[InboundAttachment]]],
) -> Callable[[str, Message[InboundAttachment]], Awaitable[bool]]:
    async def publish(agent_id: str, message: Message[InboundAttachment]) -> bool:
        wakes.append((agent_id, message))
        return True

    return publish


async def start_scheduler(
    storage: MemoryStorage,
    *,
    publish_wake: Callable[[str, Message[InboundAttachment]], bool],
    clock: Callable[[], int] = lambda: 100,
) -> tuple[ReminderScheduler, TimerWheel]:
    async def publish(agent_id: str, message: Message[InboundAttachment]) -> bool:
        return publish_wake(agent_id, message)

    timer_wheel = TimerWheel()
    await timer_wheel.start()
    scheduler = ReminderScheduler(
        storage=cast(IStorage, storage),
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
async def test_global_scheduler_materializes_system_messages_and_routes_wakes() -> None:
    storage = MemoryStorage()
    anchors = {
        _SESSION_A: add_session(storage, agent_id=_AGENT_A, session_id=_SESSION_A),
        _SESSION_C: add_session(storage, agent_id=_AGENT_A, session_id=_SESSION_C),
        _SESSION_B: add_session(storage, agent_id=_AGENT_B, session_id=_SESSION_B),
    }
    storage.reminders.update(
        {
            _REMINDER_A: make_scheduled_reminder(
                _REMINDER_A,
                session_id=_SESSION_A,
                anchor_message_id=anchors[_SESSION_A],
            ),
            _REMINDER_B: make_scheduled_reminder(
                _REMINDER_B,
                session_id=_SESSION_B,
                anchor_message_id=anchors[_SESSION_B],
            ),
            _REMINDER_C: make_scheduled_reminder(
                _REMINDER_C,
                session_id=_SESSION_C,
                anchor_message_id=anchors[_SESSION_C],
            ),
        }
    )
    storage.cursors.update(
        {
            session_id: ConsumerCursor(
                session_id=session_id,
                delivered_through_seq=storage.messages[session_id][0].seq,
            )
            for session_id in anchors
        }
    )
    wakes: list[tuple[str, Message[InboundAttachment]]] = []

    def publish(agent_id: str, message: Message[InboundAttachment]) -> bool:
        wakes.append((agent_id, message))
        return True

    scheduler, timer_wheel = await start_scheduler(storage, publish_wake=publish)
    try:
        assert [(agent_id, message.session_id) for agent_id, message in wakes] == [
            (_AGENT_A, _SESSION_A),
            (_AGENT_A, _SESSION_C),
            (_AGENT_B, _SESSION_B),
        ]
        assert {reminder.state for reminder in storage.reminders.values()} == {
            ReminderState.FIRED
        }
        system_messages = [message for _, message in wakes]
        assert all(
            message.sender_kind is SenderKind.SYSTEM
            and message.system_message_kind is SystemMessageKind.REMINDER
            and message.notifies_runtime
            and message.metadata.get("reminder_id") in storage.reminders
            for message in system_messages
        )
        assert all("(one-time)" in message.body for message in system_messages)
    finally:
        await stop_scheduler(scheduler, timer_wheel)


@pytest.mark.asyncio
async def test_scheduler_cancels_orphan_and_materializes_valid_reminder() -> None:
    storage = MemoryStorage()
    orphan_anchor = add_session(
        storage,
        agent_id=_AGENT_A,
        session_id=_SESSION_A,
    )
    valid_anchor = add_session(
        storage,
        agent_id=_AGENT_A,
        session_id=_SESSION_B,
    )
    storage.reminders.update(
        {
            _REMINDER_A: make_scheduled_reminder(
                _REMINDER_A,
                session_id=_SESSION_A,
                anchor_message_id=orphan_anchor,
            ),
            _REMINDER_B: make_scheduled_reminder(
                _REMINDER_B,
                session_id=_SESSION_B,
                anchor_message_id=valid_anchor,
            ),
        }
    )
    storage.messages[_SESSION_A].clear()
    wakes: list[tuple[str, Message[InboundAttachment]]] = []

    def publish(agent_id: str, message: Message[InboundAttachment]) -> bool:
        wakes.append((agent_id, message))
        return True

    scheduler, timer_wheel = await start_scheduler(storage, publish_wake=publish)
    try:
        assert storage.reminders[_REMINDER_A].state is ReminderState.CANCELED
        assert storage.reminders[_REMINDER_B].state is ReminderState.FIRED
        assert any(message.session_id == _SESSION_B for _, message in wakes)
    finally:
        await stop_scheduler(scheduler, timer_wheel)


@pytest.mark.asyncio
async def test_scheduler_runs_when_all_agents_fail_to_start(tmp_path: Path) -> None:
    agent_id = "0198d4e6-29c5-7465-b74b-88db31f0c118"
    node = NodeApplication(
        configuration=NodeConfiguration(
            version_check=False,
            storage="test",
            audit="test",
            agents=(
                AgentConfiguration(
                    id=agent_id,
                    name="Failed Agent",
                    channel=ChannelConfiguration(kind="missing-channel"),
                    runtimes=(RuntimeConfiguration(kind="missing-runtime"),),
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


@pytest.mark.asyncio
async def test_node_exits_when_shared_timer_driver_stops(tmp_path: Path) -> None:
    node = NodeApplication(
        configuration=NodeConfiguration(
            storage="test",
            audit="test",
            agents=(),
            version_check=False,
        ),
        shared_factories=AdapterRegistry().load_shared(storage="test", audit="test"),
        endpoint_path=tmp_path / "bcn.sock",
    )

    await node.start()
    try:
        driver = node.timer_wheel._driver_task
        assert driver is not None
        driver.cancel()

        with pytest.raises(RuntimeError):
            await asyncio.wait_for(node.wait(), timeout=1)
        assert node.ready is False
    finally:
        await node.stop()


class _StorageRefusingOneReminder(MemoryStorage):
    """Fail the durable fire of one Reminder the way a storage error would."""

    def __init__(self, reminder_id: str) -> None:
        super().__init__()
        self._refused_reminder_id = reminder_id

    async def materialize_owned_reminder_message(
        self,
        expected_revision: int,
        reminder: OwnedReminder,
        system_message: Message[InboundAttachment],
    ) -> Message[InboundAttachment] | None:
        if reminder.reminder.reminder_id == self._refused_reminder_id:
            raise RuntimeError("materialize failed")
        return cast(
            Message[InboundAttachment] | None,
            await self._invoke(
                None,
                None,
                "materialize_owned_reminder_message",
                expected_revision,
                reminder,
                system_message,
            ),
        )


@pytest.mark.asyncio
async def test_committed_wakes_are_published_when_a_later_reminder_fails() -> None:
    storage = _StorageRefusingOneReminder(_REMINDER_B)
    anchors = {
        _SESSION_A: add_session(storage, agent_id=_AGENT_A, session_id=_SESSION_A),
        _SESSION_B: add_session(storage, agent_id=_AGENT_B, session_id=_SESSION_B),
    }
    storage.reminders.update(
        {
            _REMINDER_A: make_scheduled_reminder(
                _REMINDER_A,
                session_id=_SESSION_A,
                anchor_message_id=anchors[_SESSION_A],
                next_fire_at_ms=50,
            ),
            _REMINDER_B: make_scheduled_reminder(
                _REMINDER_B,
                session_id=_SESSION_B,
                anchor_message_id=anchors[_SESSION_B],
                next_fire_at_ms=60,
            ),
        }
    )
    storage.cursors.update(
        {
            session_id: ConsumerCursor(
                session_id=session_id,
                delivered_through_seq=storage.messages[session_id][0].seq,
            )
            for session_id in anchors
        }
    )
    wakes: list[tuple[str, Message[InboundAttachment]]] = []

    timer_wheel = TimerWheel()
    await timer_wheel.start()
    scheduler = ReminderScheduler(
        storage=cast(IStorage, storage),
        timer_wheel=timer_wheel,
        concurrency=SessionLockRegistry(),
        publish_wake=_recording_publish(wakes),
        clock=lambda: 100,
    )
    try:
        with pytest.raises(RuntimeError, match="materialize failed"):
            await scheduler._materialize_due_batches()

        # the first reminder already fired and its unread system message would
        # never be woken again, so its wake has to survive the failed cycle
        assert storage.reminders[_REMINDER_A].state is ReminderState.FIRED
        assert [(agent_id, message.session_id) for agent_id, message in wakes] == [
            (_AGENT_A, _SESSION_A)
        ]
    finally:
        await timer_wheel.close()
