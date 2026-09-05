from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Protocol, cast
from unittest.mock import AsyncMock
from uuid import NAMESPACE_URL, UUID, uuid5, uuid7

import pytest
from bcn_test_support import (
    MemoryStorage,
    RecordingAudit,
    StaticChannelBuilder,
    TestChannel,
    TestRuntime,
    TestTurnPlan,
    wait_for_turn_terminal,
)

import bazaar_compute_node.core.orchestration.turn as turn_module
from bazaar_compute_node.app.application import NodeApplication
from bazaar_compute_node.app.attachments import AttachmentMaterializer
from bazaar_compute_node.app.command import (
    format_check_message,
    format_read_message,
    serialize_message,
)
from bazaar_compute_node.app.config import (
    AgentConfiguration,
    ChannelConfiguration,
    NodeConfiguration,
    RuntimeConfiguration,
)
from bazaar_compute_node.app.registry import (
    AdapterRegistry,
    AgentAdapterFactories,
    SharedAdapterFactories,
)
from bazaar_compute_node.contrib.lark.api import LarkApi
from bazaar_compute_node.contrib.lark.channel import LarkChannel
from bazaar_compute_node.contrib.lark.identity import LarkBotIdentity
from bazaar_compute_node.contrib.sqlite import SqliteDatabase
from bazaar_compute_node.contrib.telegram.channel import TelegramChannel
from bazaar_compute_node.core.actor import Actors, Agent, Mode, Thread
from bazaar_compute_node.core.agent import State
from bazaar_compute_node.core.audit import AuditEvent, ErrorKind
from bazaar_compute_node.core.channel import (
    ChannelContext,
    ChannelDeliveryReceipt,
    ChannelSendRequest,
    IChannel,
)
from bazaar_compute_node.core.command import (
    ICommandService,
    MessageSendFreshnessHold,
    MessageSendSuccess,
)
from bazaar_compute_node.core.lifecycle import TimeoutBudget
from bazaar_compute_node.core.models import (
    ApprovalDecision,
    ApprovalRequest,
    ChannelTargetKind,
    ChannelTargetPresentation,
    ContentDelta,
    Message,
    MessageDirection,
    OutboundDeliveryState,
    Reminder,
    ReminderState,
    RuntimeAttempt,
    RuntimeEventState,
    RuntimeSession,
    RuntimeTurn,
    RuntimeTurnState,
    SenderIdentity,
    SenderKind,
    SystemMessageKind,
    TurnCancelled,
    TurnCompleted,
    TurnFailed,
    TurnStarted,
    TurnUnknown,
)
from bazaar_compute_node.core.orchestration import SessionOrchestrator
from bazaar_compute_node.core.orchestration.session import (
    _NOTICE_WINDOW,
    _RuntimeNotification,
)
from bazaar_compute_node.core.orchestration.turn import inbox_notice
from bazaar_compute_node.core.outcomes import ProviderCallResult, ProviderCallStatus
from bazaar_compute_node.core.runtime import (
    IRuntime,
    Runtime,
    RuntimeCommandContext,
)
from bazaar_compute_node.core.storage import InboxTargetResolutionError, IStorage
from bazaar_compute_node.core.timerwheel import TimerWheel
from bazaar_compute_node.i18n import (
    ENGLISH,
    SIMPLIFIED_CHINESE,
    Translator,
    create_translator,
)

ACCEPTANCE_AGENT_ID = "0198d4e6-29c5-7465-b74b-88db31f0c118"
_ENGLISH_TRANSLATOR = create_translator(ENGLISH)


def unchanged_error_feedback_detail(
    _: str,
    error_message: str,
) -> str:
    return error_message


class _AcceptanceRegistry(AdapterRegistry):
    def __init__(
        self, *, channel: IChannel, runtime: Callable[[RuntimeCommandContext], IRuntime]
    ) -> None:
        self._channel = channel
        self._runtime = runtime

    def load_agent(
        self,
        *,
        channel: str,
        runtimes: Sequence[str],
    ) -> AgentAdapterFactories:
        del channel
        return AgentAdapterFactories(
            channel=StaticChannelBuilder(self._channel),
            runtimes={kind: self._runtime for kind in runtimes},
        )


def make_message(
    *,
    session_id: str = "bcn-1",
    seq: int = 1,
    message_id: str | None = None,
    body: str | None = None,
    sender_kind: str = "human",
) -> Message:
    channel_session_id = f"channel-{session_id}"
    metadata = {"sender_kind": sender_kind}
    provider_message_id = f"provider-{session_id}-{seq}"
    sender = SenderIdentity(id="sender-id", name="Sender")
    if sender_kind == SenderKind.SYSTEM.value:
        metadata["system_message_kind"] = SystemMessageKind.REMINDER.value
        provider_message_id = None
        sender = SenderIdentity(name="system")
    return Message(
        direction=MessageDirection.INBOUND,
        seq=seq,
        message_id=message_id or f"message-{session_id}-{seq}",
        session_id=session_id,
        channel_session_id=channel_session_id,
        channel="test",
        provider_thread_id=f"thread-{session_id}",
        provider_message_id=provider_message_id,
        received_at_ms=seq,
        sender=sender,
        message_type="text",
        target=f"dm:{channel_session_id}",
        body=body if body is not None else f"inbound-{seq}",
        metadata=metadata,
    )


def make_budget() -> TimeoutBudget:
    return TimeoutBudget(
        startup_seconds=1,
        provider_call_seconds=1,
        command_seconds=1,
        shutdown_seconds=1,
    )


def test_inbox_notice_matches_target_delta_contract() -> None:
    group_first = replace(
        make_message(
            seq=1,
            message_id="11111111-1111-4111-8111-111111111111",
        ),
        target="group:thread",
        target_kind=ChannelTargetKind.GROUP,
        metadata={"sender_kind": "human", "threaded": True},
    )
    group_latest = replace(
        make_message(
            seq=2,
            message_id="22222222-2222-4222-8222-222222222222",
        ),
        target="group:thread",
        target_kind=ChannelTargetKind.GROUP,
        mentions_agent=True,
        metadata={"sender_kind": "human", "threaded": True},
    )
    direct = replace(
        make_message(
            seq=3,
            message_id="33333333-3333-4333-8333-333333333333",
        ),
        target="dm:alice",
    )

    assert inbox_notice(
        (group_first, group_latest, direct),
        total_unread_count=5,
        closing_bracket_on_own_line=True,
    ) == (
        "[inbox notice:\n"
        "Inbox update: 5 unread messages total; 2 changed targets\n"
        "dm:alice  pending: 1 message · first msg=33333333 · "
        "latest sender @Sender · latest msg=33333333 · dm\n"
        "group:thread  pending: 2 messages · first msg=11111111 · "
        "latest sender @Sender · latest msg=22222222 · "
        "you were mentioned · thread\n"
        "]"
    )


def test_inbox_notice_carries_the_upgrade_line_inside_the_bracket() -> None:
    message = make_message(seq=1, message_id="11111111-1111-4111-8111-111111111111")

    without_upgrade = inbox_notice(
        (message,),
        total_unread_count=1,
        closing_bracket_on_own_line=True,
    )
    with_upgrade = inbox_notice(
        (message,),
        total_unread_count=1,
        closing_bracket_on_own_line=True,
        upgrade_version="0.2.0",
        installed_version="0.1.31",
    )

    # case: a node with nothing to offer renders exactly what it renders today
    assert "Upgrade available" not in without_upgrade
    assert without_upgrade.splitlines()[:-1] == with_upgrade.splitlines()[:-2]

    # case: the offer is the last thing before the notice closes
    lines = with_upgrade.splitlines()
    assert lines[-1] == "]"
    assert lines[-2].startswith(
        "Upgrade available: bazaar-compute-node 0.2.0 (installed 0.1.31)."
    )
    assert "`bcc node upgrade`" in lines[-2]

    # case: half an answer is not an offer
    assert "Upgrade available" not in inbox_notice(
        (message,),
        total_unread_count=1,
        closing_bracket_on_own_line=True,
        upgrade_version="0.2.0",
    )


def test_inbox_notice_hands_the_upgrade_over_where_the_node_cannot_run_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(turn_module.os, "name", "nt")
    message = make_message(seq=1, message_id="11111111-1111-4111-8111-111111111111")

    rendered = inbox_notice(
        (message,),
        total_unread_count=1,
        closing_bracket_on_own_line=True,
        upgrade_version="0.2.0",
        installed_version="0.1.31",
    )

    # case: the offer still names the release, and what follows is what the
    # user runs rather than what the Agent runs
    line = rendered.splitlines()[-2]
    assert line.startswith(
        "Upgrade available: bazaar-compute-node 0.2.0 (installed 0.1.31)."
    )
    for command in (
        "bcn system-service stop",
        (
            "uv tool install --force --refresh-package bazaar-compute-node "
            "bazaar-compute-node==0.2.0"
        ),
        "bcn system-service start",
    ):
        assert command in line

    # case: and the command the Agent has on other platforms is not offered
    assert "bcc node upgrade" not in line


def test_inbox_notice_keeps_the_upgrade_line_inside_an_inline_bracket() -> None:
    message = make_message(seq=1, message_id="11111111-1111-4111-8111-111111111111")

    notice = inbox_notice(
        (message,),
        total_unread_count=1,
        closing_bracket_on_own_line=False,
        upgrade_version="0.2.0",
        installed_version="0.1.31",
    )

    # case: the steer form closes on the offer rather than after it
    assert notice.endswith("just carry on.]")


class _InvalidSendResultChannel(TestChannel):
    async def send(
        self,
        request: ChannelSendRequest,
        *,
        timeout: float,
    ) -> ProviderCallResult[ChannelDeliveryReceipt]:
        del timeout
        self.send_requests.append(request)
        self.send_attempts.append(request)
        return cast(ProviderCallResult[ChannelDeliveryReceipt], object())


async def make_node(
    *,
    workspace: Callable[[], Path] = Path.cwd,
    channel: TestChannel | None = None,
    translator: Translator = _ENGLISH_TRANSLATOR,
    error_feedback_detail: Callable[[str, str], str] = unchanged_error_feedback_detail,
    upgrade_notice: Callable[[], tuple[str, str] | None] = lambda: None,
    mode: Mode = Mode.SESSION,
) -> tuple[
    SessionOrchestrator,
    TestChannel,
    TestRuntime,
    MemoryStorage,
    RecordingAudit,
]:
    channel = channel or TestChannel()
    runtime = TestRuntime()
    storage = MemoryStorage()
    audit = RecordingAudit()
    await storage.start(timeout=1)
    orchestrator = SessionOrchestrator(
        actors=Actors(agent_id="workspace-1", mode=mode),
        channel=channel,
        runtimes=(runtime,),
        storage=storage.scope("workspace-1", "Test Agent"),
        audit=audit,
        timeout_budget=make_budget(),
        timer_wheel=TimerWheel(),
        workspace=workspace,
        translator=translator,
        error_feedback_detail=error_feedback_detail,
        upgrade_notice=upgrade_notice,
    )
    runtime.command_service = orchestrator.command_service
    await orchestrator.start(timeout=1)
    return orchestrator, channel, runtime, storage, audit


async def make_sqlite_node() -> tuple[
    SessionOrchestrator,
    TestChannel,
    TestRuntime,
    SqliteDatabase,
    RecordingAudit,
]:
    channel = TestChannel()
    runtime = TestRuntime()
    storage = SqliteDatabase(database_name=f"task4-{uuid7()}.sqlite3")
    audit = RecordingAudit()
    await storage.start(timeout=2)
    storage_scope = storage.scope("workspace-1", "Test Agent")
    orchestrator = SessionOrchestrator(
        actors=Actors(agent_id="workspace-1", mode=Mode.SESSION),
        channel=channel,
        runtimes=(runtime,),
        storage=storage_scope,
        audit=audit,
        timeout_budget=make_budget(),
        timer_wheel=TimerWheel(),
        workspace=Path.cwd,
        translator=_ENGLISH_TRANSLATOR,
        error_feedback_detail=unchanged_error_feedback_detail,
    )
    runtime.command_service = orchestrator.command_service
    await orchestrator.start(timeout=2)
    return orchestrator, channel, runtime, storage, audit


async def make_idle_timeout_node(
    idle_timeout_ms: int,
) -> tuple[
    SessionOrchestrator,
    TestRuntime,
    MemoryStorage,
    TimerWheel,
]:
    channel = TestChannel()
    runtime = TestRuntime()
    storage = MemoryStorage()
    await storage.start(timeout=1)
    wheel = TimerWheel()
    await wheel.start()
    orchestrator = SessionOrchestrator(
        actors=Actors(agent_id="workspace-1", mode=Mode.SESSION),
        channel=channel,
        runtimes=(runtime,),
        storage=storage.scope("workspace-1", "Test Agent"),
        audit=RecordingAudit(),
        timeout_budget=make_budget(),
        timer_wheel=wheel,
        runtime_idle_timeout_ms=idle_timeout_ms,
        workspace=Path.cwd,
        translator=_ENGLISH_TRANSLATOR,
        error_feedback_detail=unchanged_error_feedback_detail,
    )
    runtime.command_service = orchestrator.command_service
    await orchestrator.start(timeout=1)
    return orchestrator, runtime, storage, wheel


@pytest.mark.asyncio
async def test_orchestrator_degrades_and_recovers_local_worker_failures() -> None:
    orchestrator, _, _, storage, _ = await make_node()
    try:
        orchestrator._runtime_queue_for_actor(Thread("session-1"))
        worker = orchestrator._runtime_workers[Thread("session-1")]
        worker.cancel()
        await wait_until(
            lambda: orchestrator._runtime_workers.get(Thread("session-1")) is not worker
        )

        receive_task = orchestrator._receive_task
        assert receive_task is not None
        receive_task.cancel()
        await wait_until(lambda: "channel_receive" in orchestrator._background_failures)

        assert orchestrator.health["state"] == "degraded"
        assert "channel_receive" in orchestrator._background_failures
        assert "runtime_worker:session-1" in orchestrator._background_failures
    finally:
        await orchestrator.stop(timeout=1)
        await storage.stop(timeout=1)


async def wait_until(predicate: object) -> None:
    if not callable(predicate):
        raise TypeError("predicate must be callable")
    for _ in range(200):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition was not reached")


def _turn_endings(channel: TestChannel, turn_id: str) -> list[str]:
    """Say which conversations a turn was ended on, in the order they saw it."""

    return [
        session_id
        for session_id, event in zip(channel.event_sessions, channel.events)
        if event.envelope.turn_id == turn_id
        and isinstance(
            event.payload,
            TurnCompleted | TurnFailed | TurnCancelled | TurnUnknown,
        )
    ]


def _stored_messages(
    storage: MemoryStorage,
    session_id: str,
    *,
    direction: MessageDirection | None = None,
) -> list[Message]:
    return [
        message
        for message in storage.messages.get(session_id, [])
        if direction is None or message.direction is direction
    ]


def _stored_message_index(
    storage: MemoryStorage,
    *,
    direction: MessageDirection | None = None,
) -> dict[str, Message]:
    return {
        message.message_id: message
        for messages in storage.messages.values()
        for message in messages
        if direction is None or message.direction is direction
    }


class _AcceptanceChannel(Protocol):
    sent_messages: list[ChannelSendRequest]

    async def inject(self, message: Message) -> None: ...


class _AcceptanceAudit(RecordingAudit):
    def __init__(self) -> None:
        super().__init__()
        self.first_check_completed = asyncio.Event()
        self.release_first_check = asyncio.Event()
        self._first_check_blocked = False

    async def append(self, event: AuditEvent, *, timeout: float) -> None:
        del timeout
        self.events.append(event)
        if (
            not self._first_check_blocked
            and event.metadata.get("operation") == "bcc.message.check"
            and event.metadata.get("status") == "completed"
        ):
            self._first_check_blocked = True
            self.first_check_completed.set()
            await self.release_first_check.wait()


async def _wait_for_inbound_messages(
    storage: IStorage,
    session_id: str,
    count: int,
) -> tuple[Message, ...]:
    async with asyncio.timeout(180):
        while True:
            repository = storage
            messages = await repository.list_messages(
                session_id,
                direction=MessageDirection.INBOUND,
            )
            if len(messages) >= count:
                return messages
            await asyncio.sleep(0.05)


async def _wait_for_audit_event(
    audit: RecordingAudit,
    *,
    session_id: str,
    event_name: str | None = None,
    event_suffix: str | None = None,
    operation: str | None = None,
    turn_id: str | None = None,
) -> None:
    async with asyncio.timeout(600):
        while True:
            if any(
                event.correlation.bcn_session_id == session_id
                and (event_name is None or event.event_name == event_name)
                and (event_suffix is None or event.event_name.endswith(event_suffix))
                and (operation is None or event.metadata.get("operation") == operation)
                and (turn_id is None or event.correlation.turn_id == turn_id)
                for event in audit.events
            ):
                return
            await asyncio.sleep(0.05)


async def run_natural_conversation_contract(
    *,
    channel: Callable[[], IChannel],
    runtime: Callable[[RuntimeCommandContext], IRuntime],
    endpoint_root: Path,
) -> None:
    """Assert the session contract with one selected Channel and runtime."""

    scenarios = (
        (
            "no-conflict",
            (
                "明天下午三点我们做后端评审，地点是 A 栋 3 楼 302，参会人是你、我和 "
                "API 小组。请先记住这个安排，后面我会补充。"
            ),
            (
                "补充确认：会议仍按明天下午三点、A 栋 3 楼 302 进行，参会人也不变。"
                "现在请告诉我你记录的安排。"
            ),
            "谢谢，按这个安排继续。请把会议时间、地点和参会人概括成一句话。",
        ),
        (
            "correction",
            (
                "明天下午三点我们做后端评审，地点是 A 栋 3 楼 302，我会参加。"
                "请先记住这个安排，后面我会补充。"
            ),
            (
                "更正刚才的安排：我临时无法参加明天下午三点的会议，请不要再确认我会参加；"
                "时间和地点仍供其他人参考。"
            ),
            "收到更正。请只概括目前仍有效的会议时间和地点，不要把我的出席状态写进去。",
        ),
    )

    for scenario_name, first_body, second_body, third_body in scenarios:
        session_id = f"natural-{scenario_name}-{uuid7()}"
        first = make_message(
            session_id=session_id,
            seq=1,
            body=first_body,
        )
        second = make_message(
            session_id=session_id,
            seq=2,
            body=second_body,
        )
        third = make_message(
            session_id=session_id,
            seq=3,
            body=third_body,
        )
        channel_instance = cast(_AcceptanceChannel, channel())
        scoped_session_id = str(
            uuid5(
                NAMESPACE_URL,
                f"bcn:{ACCEPTANCE_AGENT_ID}:bcn-session:{session_id}",
            )
        )
        storage = SqliteDatabase()
        audit = _AcceptanceAudit()
        storage_scope = storage.scope(ACCEPTANCE_AGENT_ID, "Test Agent")
        node = NodeApplication(
            configuration=NodeConfiguration(
                version_check=False,
                storage="sqlite",
                audit="test",
                agents=(
                    AgentConfiguration(
                        id=ACCEPTANCE_AGENT_ID,
                        name="Test Agent",
                        channel=ChannelConfiguration(kind="test"),
                        runtimes=(RuntimeConfiguration(kind="test"),),
                    ),
                ),
            ),
            shared_factories=SharedAdapterFactories(
                storage=lambda storage=storage: cast(IStorage, storage),
                audit=lambda audit=audit: audit,
            ),
            registry=_AcceptanceRegistry(
                channel=cast(IChannel, channel_instance),
                runtime=runtime,
            ),
            endpoint_path=endpoint_root / f"natural-{scenario_name}.sock",
            timeout_budget=TimeoutBudget(
                startup_seconds=30,
                provider_call_seconds=30,
                command_seconds=30,
                shutdown_seconds=30,
            ),
        )
        try:
            await node.start()
            await channel_instance.inject(first)
            persisted = await _wait_for_inbound_messages(
                storage_scope,
                scoped_session_id,
                1,
            )
            first_row = persisted[0]
            await _wait_for_audit_event(
                audit,
                session_id=scoped_session_id,
                event_suffix="turn.started",
                turn_id=f"turn-{first_row.message_id}",
            )
            async with asyncio.timeout(180):
                await audit.first_check_completed.wait()
            await channel_instance.inject(second)
            persisted = await _wait_for_inbound_messages(
                storage_scope,
                scoped_session_id,
                2,
            )
            second_row = persisted[1]
            assert [message.provider_message_id for message in persisted[:2]] == [
                first.provider_message_id,
                second.provider_message_id,
            ]
            audit.release_first_check.set()

            await _wait_for_audit_event(
                audit,
                session_id=scoped_session_id,
                event_suffix="turn.completed",
                turn_id=f"turn-{first_row.message_id}",
            )
            repository = storage_scope
            cursor = await repository.get_consumer_cursor(scoped_session_id)
            if cursor is None or cursor.delivered_through_seq < second_row.seq:
                await _wait_for_audit_event(
                    audit,
                    session_id=scoped_session_id,
                    event_suffix="turn.completed",
                    turn_id=f"turn-{second_row.message_id}",
                )

            await channel_instance.inject(third)
            persisted = await _wait_for_inbound_messages(
                storage_scope,
                scoped_session_id,
                3,
            )
            third_row = persisted[2]
            assert [message.provider_message_id for message in persisted[:3]] == [
                first.provider_message_id,
                second.provider_message_id,
                third.provider_message_id,
            ]
            await _wait_for_audit_event(
                audit,
                session_id=scoped_session_id,
                event_suffix="turn.completed",
                turn_id=f"turn-{third_row.message_id}",
            )
            for inbound in (second_row, third_row):
                delivery_ids = {
                    event.correlation.outbound_message_id
                    for event in audit.events
                    if (
                        event.correlation.bcn_session_id == scoped_session_id
                        and event.event_name == "channel.outbound.sent"
                        and event.correlation.inbound_seq == inbound.seq
                        and event.correlation.outbound_message_id is not None
                    )
                }
                assert delivery_ids
                assert delivery_ids.issubset(
                    {
                        event.correlation.outbound_message_id
                        for event in audit.events
                        if (
                            event.correlation.bcn_session_id == scoped_session_id
                            and event.event_name == "bcc.send.fresh_check.passed"
                            and event.correlation.inbound_seq == inbound.seq
                            and event.correlation.outbound_message_id is not None
                        )
                    }
                )
                assert any(
                    message.session_id == scoped_session_id
                    and message.provider_thread_id == inbound.provider_thread_id
                    and bool(message.body.strip())
                    for message in channel_instance.sent_messages
                )
            rejected_delivery_ids = {
                event.correlation.outbound_message_id
                for event in audit.events
                if (
                    event.correlation.bcn_session_id == scoped_session_id
                    and event.event_name == "bcc.send.fresh_check.failed"
                    and event.correlation.outbound_message_id is not None
                )
            }
            sent_delivery_ids = {
                event.correlation.outbound_message_id
                for event in audit.events
                if event.event_name == "channel.outbound.sent"
                and event.correlation.outbound_message_id is not None
            }
            assert rejected_delivery_ids.isdisjoint(sent_delivery_ids)
        finally:
            audit.release_first_check.set()
            await node.stop()


@pytest.mark.asyncio
async def test_channel_storage_runtime_turn_path() -> None:
    orchestrator, channel, runtime, storage, audit = await make_node()
    try:
        message = make_message()
        await channel.inject(message)
        await wait_until(
            lambda: any(
                event.event_name == "runtime.turn.completed"
                and event.correlation.turn_id == "turn-message-bcn-1-1"
                for event in audit.events
            )
        )

        assert _stored_messages(
            storage,
            "bcn-1",
            direction=MessageDirection.INBOUND,
        ) == [message]
        assert orchestrator.session_runtime_state(Thread("bcn-1")) is State.IDLE
        assert runtime.started_turns
        assert any(
            event.correlation.bcn_session_id == "bcn-1"
            and event.correlation.turn_id == "turn-message-bcn-1-1"
            for event in audit.events
        )
        unfollowed = await orchestrator.command_service.unfollow(
            Thread("bcn-1"), raw_target="dm:channel-bcn-1"
        )
        assert unfollowed.changed is False
        assert storage.channel_sessions["channel-bcn-1"].following is True
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_stream_events() -> None:
    # stream events skip durable storage and audit
    orchestrator, channel, runtime, _, audit = await make_node()
    runtime.queue_turn_plan(TestTurnPlan(update_count=20_000))
    try:
        await channel.inject(make_message())
        await wait_until(
            lambda: any(
                event.event_name == "runtime.turn.completed"
                and event.correlation.turn_id == "turn-message-bcn-1-1"
                for event in audit.events
            )
        )

        assert len(channel.stream_events) == 20_000
        runtime_events = [
            item
            for item in channel.events
            if isinstance(item.payload, TurnStarted | TurnCompleted)
        ]
        assert [type(event.payload) for event in runtime_events] == [
            TurnStarted,
            TurnCompleted,
        ]
        assert {event.envelope.actor.id for event in channel.stream_events} == {"bcn-1"}
        first_delta = channel.stream_events[0].payload
        last_delta = channel.stream_events[-1].payload
        assert isinstance(first_delta, ContentDelta)
        assert isinstance(last_delta, ContentDelta)
        assert first_delta.text == "delta-1"
        assert last_delta.text == "delta-20000"
        assert not any(
            event.event_name == "reasoning-summary-delta" for event in audit.events
        )
    finally:
        await orchestrator.stop(timeout=1)

    # a channel failure on a stream event does not fail the turn
    orchestrator, channel, runtime, _, audit = await make_node()
    runtime.queue_turn_plan(TestTurnPlan(update_count=1))
    channel.stream_event_error = RuntimeError("stream consumer unavailable")
    try:
        await channel.inject(make_message())
        await wait_until(
            lambda: any(
                event.event_name == "runtime.turn.completed"
                and event.correlation.turn_id == "turn-message-bcn-1-1"
                for event in audit.events
            )
        )

        assert not channel.stream_events
        assert orchestrator.session_runtime_state(Thread("bcn-1")) is State.IDLE
    finally:
        await orchestrator.stop(timeout=1)

    # a stream event for another session is discarded
    orchestrator, channel, runtime, _, audit = await make_node()
    runtime.queue_turn_plan(
        TestTurnPlan(update_count=1, stream_session_id="another-session")
    )
    try:
        await channel.inject(make_message())
        await wait_until(
            lambda: any(
                event.event_name == "runtime.turn.completed"
                and event.correlation.turn_id == "turn-message-bcn-1-1"
                for event in audit.events
            )
        )

        assert not channel.stream_events
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_runtime_can_run_real_command_service_behavior() -> None:
    orchestrator, channel, runtime, storage, audit = await make_node()

    async def command_script(commands: ICommandService, session_id: str) -> None:
        (checked,) = await commands.check(Thread(session_id))
        if not checked.messages:
            raise AssertionError("command did not observe the inbound message")
        history = await commands.read(
            Thread(session_id),
            raw_target=checked.messages[0].target,
        )
        if not history.messages:
            raise AssertionError("history command did not observe the inbound message")
        outbound = await commands.send(
            actor=Thread(session_id),
            command_id="command-1",
            raw_target=checked.messages[0].target,
            body="runtime-generated reply",
            created_at_ms=2,
        )
        assert isinstance(outbound, MessageSendSuccess)
        if outbound.message.delivery_state is not OutboundDeliveryState.SENT:
            raise AssertionError("command did not deliver the outbound message")

    try:
        runtime.queue_turn_plan(TestTurnPlan(command_script=command_script))
        message = make_message()
        await channel.inject(message)
        await wait_for_turn_terminal(
            orchestrator=orchestrator,
            channel=channel,
            session_id=message.session_id,
            client_user_message_id=message.message_id,
            sent_after=0,
            timeout=1,
        )
        assert len(channel.sent_messages) == 1
        assert storage.cursors["bcn-1"].delivered_through_seq == 1
        assert _stored_message_index(storage, direction=MessageDirection.OUTBOUND)
        tool_events = [
            event for event in audit.events if event.metadata.get("kind") == "tool_call"
        ]
        assert {event.metadata["operation"] for event in tool_events} == {
            "bcc.message.check",
            "bcc.message.read",
            "bcc.message.send",
        }
        send_events = [
            event
            for event in tool_events
            if event.metadata["operation"] == "bcc.message.send"
        ]
        assert send_events[-1].metadata["status"] == "sent"
        send_arguments = send_events[-1].metadata["arguments"]
        assert isinstance(send_arguments, dict)
        assert "body" not in send_arguments
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_a_conversation_reads_its_own_history_and_no_other() -> None:
    orchestrator, _, _, storage, _ = await make_sqlite_node()
    caller_id = "bcn-caller"
    target_session_id = "bcn-target"
    caller_message = make_message(session_id=caller_id)
    target_parent = make_message(session_id=target_session_id)
    target_reply = replace(
        target_parent,
        seq=2,
        message_id="message-bcn-target-2",
        provider_message_id="provider-bcn-target-2",
        received_at_ms=2,
        body="target reply",
        reply_to_message_id=target_parent.message_id,
    )
    await orchestrator.handle_inbound(caller_message)
    await orchestrator.handle_inbound(target_parent)
    await orchestrator.handle_inbound(target_reply)

    try:
        history = await orchestrator.command_service.read(
            Thread(target_session_id),
            raw_target=target_reply.target,
            around_message_id=target_reply.message_id,
            limit=1,
        )
        assert [message.message_id for message in history.messages] == [
            target_reply.message_id
        ]
        assert [message.message_id for message in history.referenced_messages] == [
            target_parent.message_id
        ]
        assert history.messages[0].target == target_reply.target

        # another conversation's history belongs to whoever answers for it
        with pytest.raises(InboxTargetResolutionError):
            await orchestrator.command_service.read(
                Thread(caller_id),
                raw_target=target_reply.target,
                limit=1,
            )
    finally:
        await orchestrator.stop(timeout=1)
        await storage.stop(timeout=2)


@pytest.mark.asyncio
async def test_approval_is_routed_to_the_current_channel_session() -> None:
    orchestrator, channel, runtime, _, audit = await make_node()
    try:
        first_turn = await orchestrator.handle_inbound(make_message(seq=1))
        assert first_turn is not None
        runtime_session = orchestrator.runtime_session(Thread("bcn-1"))
        assert runtime_session is not None
        request = ApprovalRequest(
            request_id="approval-1",
            actor=Thread("bcn-1"),
            runtime_session_id=runtime_session.id,
            action="test-action",
            created_at_ms=1,
            turn_id="turn-message-bcn-1-2",
        )
        runtime.queue_turn_plan(TestTurnPlan(approval_request=request))
        await channel.inject(make_message(seq=2))
        await wait_until(
            lambda: any(
                event.event_name == "runtime.turn.completed"
                and event.correlation.turn_id == "turn-message-bcn-1-2"
                for event in audit.events
            )
        )

        assert channel.approval_requests == [request]
        assert len(channel.channel_approval_requests) == 1
        channel_request = channel.channel_approval_requests[0]
        assert channel_request.approval == request
        assert channel_request.target_kind is ChannelTargetKind.DM
        assert channel_request.provider_thread_id == "thread-bcn-1"
        assert channel_request.provider_reply_to_message_id == "provider-bcn-1-2"
        assert channel_request.provider_sender_id == "sender-id"
        assert runtime.approval_results
        assert runtime.approval_results[0].request_id == request.request_id
        assert [
            event.event_name
            for event in audit.events
            if event.event_name.startswith("approval.")
        ] == ["approval.requested", "approval.decided"]
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_an_individual_actor_allows_tool_use_without_asking_anyone() -> None:
    orchestrator, channel, runtime, _, audit = await make_node(
        mode=Mode.DANGEROUS_INDIVIDUAL
    )
    agent = Agent("workspace-1")
    try:
        opening_turn = await orchestrator.handle_inbound(
            make_message(session_id="bcn-a", seq=1)
        )
        assert opening_turn is not None
        runtime_session = orchestrator.runtime_session(agent)
        assert runtime_session is not None
        request = ApprovalRequest(
            request_id="approval-1",
            actor=agent,
            runtime_session_id=runtime_session.id,
            action="test-action",
            created_at_ms=1,
            turn_id="turn-message-bcn-a-2",
        )
        runtime.queue_turn_plan(TestTurnPlan(approval_request=request))
        await channel.inject(make_message(session_id="bcn-a", seq=2))
        await wait_until(
            lambda: any(
                event.event_name == "runtime.turn.completed"
                and event.correlation.turn_id == "turn-message-bcn-a-2"
                for event in audit.events
            )
        )

        assert channel.approval_requests == []
        assert channel.channel_approval_requests == []
        assert runtime.approval_results
        allowed = runtime.approval_results[0]
        assert allowed.request_id == request.request_id
        assert allowed.decision is ApprovalDecision.APPROVED
        assert allowed.reason == (
            "This Agent answers for every conversation and allows tool use "
            "without asking."
        )
        decided = [
            event for event in audit.events if event.event_name.startswith("approval.")
        ]
        assert [event.event_name for event in decided] == ["approval.decided"]
        assert decided[0].metadata["decision"] == "approved"
        assert decided[0].metadata["reason"] == allowed.reason
        assert decided[0].correlation.request_id == request.request_id
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_approval_preserves_sender_id_after_sqlite_round_trip() -> None:
    orchestrator, channel, runtime, storage, _ = await make_sqlite_node()
    try:
        first_turn = await orchestrator.handle_inbound(make_message(seq=1))
        assert first_turn is not None
        runtime_session = orchestrator.runtime_session(Thread("bcn-1"))
        assert runtime_session is not None
        context, message, created = await orchestrator._record_inbound(
            make_message(seq=2)
        )
        persisted = await storage.scope("workspace-1", "Test Agent").get_message(
            message.message_id,
            direction=MessageDirection.INBOUND,
        )
        assert created is True
        assert context is not None
        assert persisted is not None
        runtime.queue_turn_plan(
            TestTurnPlan(
                approval_request=ApprovalRequest(
                    request_id="approval-persisted-sender",
                    actor=Thread(message.session_id),
                    runtime_session_id=runtime_session.id,
                    action="test-action",
                    created_at_ms=1,
                    turn_id="turn-persisted-sender",
                )
            )
        )

        turn = await orchestrator._run_notification(
            (
                _RuntimeNotification(
                    message=persisted,
                    context=context,
                    wake_id="persisted-sender",
                ),
            )
        )

        assert turn is not None
        assert channel.channel_approval_requests[0].provider_sender_id == "sender-id"
    finally:
        await orchestrator.stop(timeout=1)
        await storage.stop(timeout=2)


@pytest.mark.asyncio
async def test_reminder_approval_uses_its_human_anchor_as_the_target() -> None:
    orchestrator, channel, runtime, storage, _ = await make_node()
    anchor = make_message(seq=1, message_id=str(uuid7()))
    try:
        context, anchor, created = await orchestrator._record_inbound(anchor)
        assert context is not None
        assert created
        first_turn = await orchestrator._run_notification(
            (_RuntimeNotification(message=anchor, context=context),)
        )
        assert first_turn is not None
        reminder = await storage.scope("workspace-1", "Test Agent").save_new_reminder(
            Reminder(
                reminder_id="pending",
                owner_session_id=anchor.session_id,
                anchor_message_id=anchor.message_id,
                title="Review",
                state=ReminderState.SCHEDULED,
                next_fire_at_ms=10,
                repeat_rule=None,
                timezone="UTC",
                revision=1,
                last_occurrence_no=0,
                created_at_ms=2,
                updated_at_ms=2,
            )
        )
        message = Message(
            direction=MessageDirection.INBOUND,
            seq=0,
            message_id=str(uuid7()),
            session_id=anchor.session_id,
            channel_session_id=anchor.channel_session_id,
            channel=anchor.channel,
            provider_thread_id=anchor.provider_thread_id,
            provider_message_id=None,
            received_at_ms=3,
            sender=SenderIdentity(name="system"),
            target=anchor.target,
            target_kind=anchor.target_kind,
            body='🔔 Reminder #019c1234 (one-time) — dm:alice — "Review"',
            metadata={
                "sender_kind": SenderKind.SYSTEM.value,
                "system_message_kind": SystemMessageKind.REMINDER.value,
                "reminder_id": reminder.reminder_id,
            },
        )
        runtime_session = orchestrator.runtime_session(Thread(anchor.session_id))
        assert runtime_session is not None
        message = await cast(IStorage, storage).save_message(message)
        runtime.queue_turn_plan(
            TestTurnPlan(
                approval_request=ApprovalRequest(
                    request_id="approval-reminder-anchor",
                    actor=Thread(anchor.session_id),
                    runtime_session_id=runtime_session.id,
                    action="test-action",
                    created_at_ms=3,
                    turn_id=f"turn-{message.message_id}",
                )
            )
        )

        turn = await orchestrator._run_notification(
            (_RuntimeNotification(message=message, context=context),)
        )

        assert turn is not None
        assert anchor.sender is not None
        channel_request = channel.channel_approval_requests[-1]
        assert channel_request.provider_sender_id == anchor.sender.id
        assert (
            channel_request.provider_reply_to_message_id == anchor.provider_message_id
        )
        assert runtime.approval_results[-1].decision is ApprovalDecision.APPROVED

        # the turn's own output belongs under the message the Reminder was set from
        anchored_session_id, anchored = channel.turn_anchors[-1]
        assert anchored_session_id == anchor.session_id
        assert anchored.message_id == anchor.message_id
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.parametrize(
    "sender_kind",
    ["agent", "unknown", "system"],
)
@pytest.mark.asyncio
async def test_non_human_approval_is_rejected_before_channel_call(
    sender_kind: str,
) -> None:
    orchestrator, channel, runtime, storage, audit = await make_node()
    try:
        context, first_message, created = await orchestrator._record_inbound(
            make_message(seq=1)
        )
        assert context is not None
        assert created
        first_turn = await orchestrator._run_notification(
            (_RuntimeNotification(message=first_message, context=context),)
        )
        assert first_turn is not None
        runtime_session = orchestrator.runtime_session(Thread("bcn-1"))
        assert runtime_session is not None
        request = ApprovalRequest(
            request_id=f"approval-{sender_kind}-1",
            actor=Thread("bcn-1"),
            runtime_session_id=runtime_session.id,
            action="test-action",
            created_at_ms=1,
            turn_id="turn-message-bcn-1-2",
        )
        runtime.queue_turn_plan(TestTurnPlan(approval_request=request))
        message = make_message(seq=2, sender_kind=sender_kind)
        if sender_kind == SenderKind.SYSTEM.value:
            message = await cast(IStorage, storage).save_message(message)
            turn = await orchestrator._run_notification(
                (_RuntimeNotification(message=message, context=context),)
            )
            assert turn is not None
        else:
            await channel.inject(message)
            await wait_until(
                lambda: any(
                    event.event_name == "runtime.turn.completed"
                    and event.correlation.turn_id == "turn-message-bcn-1-2"
                    for event in audit.events
                )
            )

        assert channel.approval_requests == []
        assert channel.channel_approval_requests == []
        assert runtime.approval_results
        result = runtime.approval_results[0]
        assert result.request_id == request.request_id
        assert result.decision is ApprovalDecision.REJECTED
        assert result.reason is not None
        assert "No person can approve tool use" in result.reason
        assert "Explain in your reply" in result.reason
        requested = next(
            event for event in audit.events if event.event_name == "approval.requested"
        )
        assert requested.metadata["sender_kind"] == sender_kind
        decided = next(
            event for event in audit.events if event.event_name == "approval.decided"
        )
        assert decided.metadata["reason"] == result.reason
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_fresh_check_holds_draft_until_context_is_reviewed() -> None:
    orchestrator, channel, _, storage, _ = await make_node()
    try:
        await channel.inject(make_message(seq=1))
        await wait_until(
            lambda: (
                len(
                    _stored_messages(
                        storage,
                        "bcn-1",
                        direction=MessageDirection.INBOUND,
                    )
                )
                == 1
            )
        )

        held_without_snapshot = await orchestrator.command_service.send(
            actor=Thread("bcn-1"),
            command_id="command-before-check",
            raw_target="dm:channel-bcn-1",
            body="reply",
            created_at_ms=2,
            reply_to_message_id=_stored_messages(
                storage,
                "bcn-1",
                direction=MessageDirection.INBOUND,
            )[0].message_id,
        )
        assert isinstance(held_without_snapshot, MessageSendFreshnessHold)
        assert held_without_snapshot.newer_message_total == 1
        assert held_without_snapshot.messages == tuple(
            _stored_messages(
                storage,
                "bcn-1",
                direction=MessageDirection.INBOUND,
            )
        )
        assert held_without_snapshot.draft_replaced is False
        assert not _stored_message_index(
            storage,
            direction=MessageDirection.OUTBOUND,
        )
        assert not channel.send_attempts

        (checked,) = await orchestrator.command_service.check(Thread("bcn-1"))
        assert checked.messages
        delivered = await orchestrator.command_service.send(
            actor=Thread("bcn-1"),
            command_id="command-after-check",
            raw_target="dm:channel-bcn-1",
            body="",
            created_at_ms=3,
            send_draft=True,
        )
        assert isinstance(delivered, MessageSendSuccess)
        delivered = delivered.message
        assert delivered.delivery_state is OutboundDeliveryState.SENT
        assert delivered.body == "reply"
        assert channel.send_requests[0].provider_reply_to_message_id == (
            _stored_messages(
                storage,
                "bcn-1",
                direction=MessageDirection.INBOUND,
            )[0].provider_message_id
        )
        assert len(channel.send_attempts) == 1
        with pytest.raises(ValueError, match="no active draft"):
            await orchestrator.command_service.send(
                actor=Thread("bcn-1"),
                command_id="command-consumed-draft",
                raw_target="dm:channel-bcn-1",
                body="",
                created_at_ms=4,
                send_draft=True,
            )

        await channel.inject(make_message(seq=2))
        await wait_until(
            lambda: (
                len(
                    _stored_messages(
                        storage,
                        "bcn-1",
                        direction=MessageDirection.INBOUND,
                    )
                )
                == 2
            )
        )
        stale = await orchestrator.command_service.send(
            actor=Thread("bcn-1"),
            command_id="command-stale",
            raw_target="dm:channel-bcn-1",
            body="stale draft",
            created_at_ms=5,
        )
        assert isinstance(stale, MessageSendFreshnessHold)
        assert stale.draft_replaced is False
        revised = await orchestrator.command_service.send(
            actor=Thread("bcn-1"),
            command_id="command-revised",
            raw_target="dm:channel-bcn-1",
            body="revised draft",
            created_at_ms=6,
        )
        assert isinstance(revised, MessageSendSuccess)
        revised = revised.message
        assert revised.body == "revised draft"
        assert len(channel.send_attempts) == 2
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_sqlite_freshness_hold_returns_latest_bounded_context() -> None:
    orchestrator, channel, _, storage, _ = await make_sqlite_node()
    try:
        for seq in range(1, 26):
            await orchestrator._record_inbound(make_message(seq=seq))

        held = await orchestrator.command_service.send(
            actor=Thread("bcn-1"),
            command_id="command-bounded-context",
            raw_target="dm:channel-bcn-1",
            body="reply",
            created_at_ms=26,
        )

        assert isinstance(held, MessageSendFreshnessHold)
        assert held.newer_message_total == 25
        assert [message.seq for message in held.messages] == list(range(6, 26))
        assert not channel.send_attempts
    finally:
        await orchestrator.stop(timeout=1)
        await storage.stop(timeout=2)


@pytest.mark.asyncio
async def test_readable_target_contract(tmp_path: Path) -> None:
    async def referenced_paths() -> set[str]:
        return set()

    orchestrator, channel, _, storage, _ = await make_sqlite_node()
    repository = storage.scope("workspace-1", "Test Agent")
    context = ChannelContext(
        agent_id="workspace-1",
        attachments=AttachmentMaterializer(lambda: tmp_path, referenced_paths),
        options={},
        workspace=lambda: tmp_path,
    )
    telegram = TelegramChannel(context, token="token")
    telegram._bot_id = 1
    telegram._bot_username = "test_bot"
    telegram._started_at_s = 1
    try:
        case = "DM observation resolves a readable selector"
        dm_message = replace(
            make_message(session_id="readable-dm"),
            target_presentation=ChannelTargetPresentation(handle="Alice"),
        )
        _, dm_message, created = await orchestrator._record_inbound(dm_message)
        target = await repository.resolve_inbox_target("dm:@ALICE")
        assert created is True, case
        assert dm_message.target == "dm:channel-readable-dm", case
        assert target.bcn_session.id == "readable-dm", case
        assert target.canonical_target == "dm:channel-readable-dm", case
        assert target.display_target == "dm:@Alice", case

        case = "history and outbound persistence use the canonical target"
        history = await orchestrator.command_service.read(
            Thread("readable-dm"),
            raw_target="dm:@alice",
        )
        assert [message.target for message in history.messages] == [
            "dm:channel-readable-dm"
        ], case
        await orchestrator.command_service.check(Thread("readable-dm"))
        delivered = await orchestrator.command_service.send(
            actor=Thread("readable-dm"),
            command_id="readable-target-send",
            raw_target="dm:@Alice",
            body="Readable target reply",
            created_at_ms=2,
        )
        assert isinstance(delivered, MessageSendSuccess), case
        assert delivered.target == "dm:@Alice", case
        assert delivered.message.target == "dm:channel-readable-dm", case
        assert channel.send_attempts[-1].session_id == "readable-dm", case

        case = "group labels are current presentation over a stable UUID"
        group_message = replace(
            make_message(session_id="readable-group"),
            target_kind=ChannelTargetKind.GROUP,
            target_presentation=ChannelTargetPresentation(display_name="Platform Team"),
        )
        await orchestrator._record_inbound(group_message)
        target = await repository.resolve_inbox_target(
            "#Previous Name:channel-readable-group"
        )
        assert target.canonical_target == "group:channel-readable-group", case
        assert target.display_target == "#Platform Team:channel-readable-group", case
        await orchestrator._record_inbound(
            replace(
                group_message,
                seq=2,
                message_id="message-readable-group-2",
                provider_message_id="provider-readable-group-2",
                received_at_ms=2,
                target_presentation=ChannelTargetPresentation(
                    display_name="Core Platform"
                ),
            )
        )
        target = await repository.resolve_inbox_target(
            "#Platform Team:channel-readable-group"
        )
        assert target.display_target == "#Core Platform:channel-readable-group", case

        case = "Telegram private chat username is the target handle"
        await telegram._handle_message(
            {
                "message_id": 2,
                "date": 1,
                "chat": {
                    "id": 42,
                    "type": "private",
                    "username": "TelegramAlice",
                },
                "from": {"id": 42, "username": "SenderAlice"},
                "text": "Current private message",
                "reply_to_message": {
                    "message_id": 1,
                    "date": 1,
                    "chat": {
                        "id": 42,
                        "type": "private",
                        "username": "OldTelegramAlice",
                    },
                    "from": {"id": 43, "username": "QuotedSender"},
                    "text": "Quoted private message",
                },
            },
            update_id=1,
        )
        quoted = telegram._inbound.get_nowait()
        private = telegram._inbound.get_nowait()
        assert isinstance(quoted, Message), case
        assert isinstance(private, Message), case
        assert quoted.target_presentation == ChannelTargetPresentation(
            handle="TelegramAlice"
        ), case
        assert private.target_presentation == quoted.target_presentation, case
        await orchestrator._record_inbound(quoted)
        await orchestrator._record_inbound(private)
        target = await repository.resolve_inbox_target("dm:@telegramalice")
        assert target.display_target == "dm:@TelegramAlice", case

        case = "Telegram topic name keeps the topic-specific UUID suffix"
        await telegram._handle_message(
            {
                "message_id": 9,
                "message_thread_id": 9,
                "date": 1,
                "chat": {
                    "id": -100,
                    "type": "supergroup",
                    "title": "Parent Group",
                },
                "from": {"id": 44, "username": "PlatformMember"},
                "forum_topic_created": {
                    "name": "Original Platform",
                    "icon_color": 7_322_092,
                },
            },
            update_id=2,
        )
        await telegram._handle_message(
            {
                "message_id": 10,
                "message_thread_id": 9,
                "date": 1,
                "chat": {
                    "id": -100,
                    "type": "supergroup",
                    "title": "Parent Group",
                },
                "from": {"id": 44, "username": "PlatformMember"},
                "forum_topic_edited": {"name": "Core: Platform"},
            },
            update_id=3,
        )
        await telegram._handle_message(
            {
                "message_id": 11,
                "message_thread_id": 9,
                "date": 1,
                "chat": {
                    "id": -100,
                    "type": "supergroup",
                    "title": "Parent Group",
                },
                "from": {"id": 44, "username": "PlatformMember"},
                "text": "Topic message",
                "reply_to_message": {
                    "message_id": 9,
                    "message_thread_id": 9,
                    "date": 1,
                    "chat": {
                        "id": -100,
                        "type": "supergroup",
                        "title": "Parent Group",
                    },
                    "forum_topic_created": {
                        "name": "Original Platform",
                        "icon_color": 7_322_092,
                    },
                },
            },
            update_id=4,
        )
        group = telegram._inbound.get_nowait()
        assert isinstance(group, Message), case
        assert group.target_presentation == ChannelTargetPresentation(
            display_name="Core: Platform"
        ), case
        await orchestrator._record_inbound(group)
        target = await repository.resolve_inbox_target(
            f"#Previous Title:{group.channel_session_id}"
        )
        assert target.display_target == (
            f"#Core: Platform:{group.channel_session_id}"
        ), case

        api = SimpleNamespace(
            get_chat=AsyncMock(return_value={"data": {"name": "Lark Platform"}}),
            get_user=AsyncMock(return_value={"data": {"user": {"name": "Lark User"}}}),
            token_refresh_failures=0,
        )
        lark = LarkChannel(
            context,
            app_id="app-id",
            app_secret="app-secret",
            region="feishu",
            base_url="https://open.feishu.cn",
            timer_wheel=TimerWheel(),
        )
        lark._api = cast(LarkApi, api)
        lark._identity = LarkBotIdentity(open_id="ou_bot")

        case = "Lark contact cache coalesces successful lookups"
        names = await asyncio.gather(
            lark._contact_name(tenant_key="tenant-key", open_id="ou_contact"),
            lark._contact_name(tenant_key="tenant-key", open_id="ou_contact"),
        )
        assert names == ["Lark User", "Lark User"], case
        assert api.get_user.await_count == 1, case

        case = "Lark group lookup projects a cached readable title"
        payload = {
            "schema": "2.0",
            "header": {
                "event_id": "event-lark-group-1",
                "event_type": "im.message.receive_v1",
                "tenant_key": "tenant-key",
            },
            "event": {
                "message": {
                    "message_id": "om_lark_group_1",
                    "chat_id": "oc_lark_group",
                    "chat_type": "group",
                    "message_type": "text",
                    "content": json.dumps({"text": "Lark group message"}),
                    "create_time": "1",
                },
                "sender": {
                    "sender_id": {"open_id": "ou_sender"},
                    "sender_type": "app",
                    "tenant_key": "tenant-key",
                },
            },
        }
        assert await lark._handle_event("event", payload, object()) is True, case
        group = lark._inbound.get_nowait()
        assert isinstance(group, Message), case
        assert group.target_presentation == ChannelTargetPresentation(
            display_name="Lark Platform"
        ), case
        await orchestrator._record_inbound(group)
        target = await repository.resolve_inbox_target(group.target)
        assert target.display_target == (
            f"#Lark Platform:{group.channel_session_id}"
        ), case

        message = cast(
            dict[str, object], cast(dict[str, object], payload["event"])["message"]
        )
        message["message_id"] = "om_lark_group_2"
        header = cast(dict[str, object], payload["header"])
        header["event_id"] = "event-lark-group-2"
        assert await lark._handle_event("event", payload, object()) is True, case
        group = lark._inbound.get_nowait()
        assert isinstance(group, Message), case
        assert api.get_chat.await_count == 1, case
        assert lark.health["chat_cache_hits"] == 1, case

        case = "Lark expired cache refreshes a renamed group"
        lark._chat_cache[("app-id", "tenant-key", "oc_lark_group")] = (
            0.0,
            "Lark Platform",
        )
        api.get_chat.return_value = {"data": {"name": "Lark Core"}}
        message["message_id"] = "om_lark_group_3"
        header["event_id"] = "event-lark-group-3"
        assert await lark._handle_event("event", payload, object()) is True, case
        group = lark._inbound.get_nowait()
        assert isinstance(group, Message), case
        await orchestrator._record_inbound(group)
        target = await repository.resolve_inbox_target(group.target)
        assert target.display_target == f"#Lark Core:{group.channel_session_id}", case
        lark_target = target
        assert api.get_chat.await_count == 2, case

        case = "Lark concurrent lookups collapse to one provider request"
        api.get_chat.return_value = {"data": {"name": "Lark Concurrent"}}
        requests = api.get_chat.await_count
        names = await asyncio.gather(
            lark._chat_name(tenant_key="tenant-key", chat_id="oc_lark_concurrent"),
            lark._chat_name(tenant_key="tenant-key", chat_id="oc_lark_concurrent"),
        )
        assert names == ["Lark Concurrent", "Lark Concurrent"], case
        assert api.get_chat.await_count == requests + 1, case

        case = "all displayed targets round-trip through command resolution"
        await orchestrator._record_inbound(
            replace(
                make_message(session_id="readable-dm"),
                seq=3,
                message_id="message-readable-dm-3",
                provider_message_id="provider-readable-dm-3",
                received_at_ms=3,
                target_presentation=ChannelTargetPresentation(handle="Alice"),
            )
        )
        case = "check and read headers use the current display projection"
        (checked,) = await orchestrator.command_service.check(Thread("readable-dm"))
        checked_payload = serialize_message(
            checked.messages[-1], checked.target_projections
        )
        assert format_check_message(checked_payload).startswith("[target=dm:@Alice "), (
            case
        )
        history = await orchestrator.command_service.read(
            Thread("readable-dm"),
            raw_target="dm:@Alice",
        )
        history_payload = serialize_message(
            history.messages[-1], history.target_projections
        )
        assert "replyTarget=dm:@Alice" in format_read_message(
            history_payload,
            index=1,
            count=1,
        ), case

        case = "freshness and send outputs use a resolvable display target"
        await orchestrator._record_inbound(
            replace(
                make_message(session_id="readable-dm"),
                seq=4,
                message_id="message-readable-dm-4",
                provider_message_id="provider-readable-dm-4",
                received_at_ms=4,
                target_presentation=ChannelTargetPresentation(handle="Alice"),
            )
        )
        held = await orchestrator.command_service.send(
            actor=Thread("readable-dm"),
            command_id="readable-target-hold",
            raw_target="dm:@Alice",
            body="Held readable target reply",
            created_at_ms=5,
        )
        assert isinstance(held, MessageSendFreshnessHold), case
        assert held.target == "dm:@Alice", case
        held_payload = serialize_message(held.messages[-1], held.target_projections)
        assert held_payload["target"] == "dm:@Alice", case

        case = "another conversation is out of reach, and unfollow still names it"
        await orchestrator.command_service.check(Thread("readable-dm"))
        with pytest.raises(InboxTargetResolutionError):
            await orchestrator.command_service.send(
                actor=Thread("readable-dm"),
                command_id="readable-target-out-of-reach",
                raw_target=lark_target.display_target,
                body="reply",
                created_at_ms=6,
            )
        await repository.save_channel_session(
            replace(lark_target.channel_session, following=True)
        )
        unfollowed = await orchestrator.command_service.unfollow(
            Thread(lark_target.bcn_session.id),
            raw_target=lark_target.display_target,
        )
        assert unfollowed.target == lark_target.display_target, case
        assert unfollowed.changed is True, case

        case = "schema v22 persists presentation separately from messages"
        async with storage.reader() as reader:
            columns = await reader.fetchall("PRAGMA table_info(channel_sessions)")
            indexes = await reader.fetchall(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND name = ?",
                ("idx_channel_sessions_target_handle",),
            )
        assert {
            "target_display_name",
            "target_handle",
            "target_handle_key",
        }.issubset({str(column["name"]) for column in columns}), case
        assert [row["name"] for row in indexes] == [
            "idx_channel_sessions_target_handle"
        ], case
    finally:
        await orchestrator.stop(timeout=1)
        await storage.stop(timeout=2)


@pytest.mark.asyncio
async def test_active_drafts_are_isolated_by_resolved_session() -> None:
    orchestrator, channel, _, _, _ = await make_node()
    try:
        await orchestrator._record_inbound(make_message(session_id="bcn-a"))
        await orchestrator._record_inbound(make_message(session_id="bcn-b"))

        first_hold = await orchestrator.command_service.send(
            actor=Thread("bcn-a"),
            command_id="command-hold-a",
            raw_target="dm:channel-bcn-a",
            body="draft a",
            created_at_ms=2,
        )
        second_hold = await orchestrator.command_service.send(
            actor=Thread("bcn-b"),
            command_id="command-hold-b",
            raw_target="dm:channel-bcn-b",
            body="draft b",
            created_at_ms=2,
        )
        assert isinstance(first_hold, MessageSendFreshnessHold)
        assert isinstance(second_hold, MessageSendFreshnessHold)

        await orchestrator.command_service.check(Thread("bcn-a"))
        await orchestrator.command_service.check(Thread("bcn-b"))
        first_sent = await orchestrator.command_service.send(
            actor=Thread("bcn-a"),
            command_id="command-send-a",
            raw_target="dm:channel-bcn-a",
            body="",
            created_at_ms=3,
            send_draft=True,
        )
        second_sent = await orchestrator.command_service.send(
            actor=Thread("bcn-b"),
            command_id="command-send-b",
            raw_target="dm:channel-bcn-b",
            body="",
            created_at_ms=3,
            send_draft=True,
        )

        assert isinstance(first_sent, MessageSendSuccess)
        assert isinstance(second_sent, MessageSendSuccess)
        assert [request.body for request in channel.send_requests] == [
            "draft a",
            "draft b",
        ]
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_send_delivers_ordered_attachments_to_the_channel(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.json"
    first.write_text("first\n")
    second.write_text('{"second": true}\n')
    orchestrator, channel, _, storage, _ = await make_node(workspace=lambda: tmp_path)
    try:
        await channel.inject(make_message(seq=1))
        await wait_until(
            lambda: (
                len(
                    _stored_messages(
                        storage,
                        "bcn-1",
                        direction=MessageDirection.INBOUND,
                    )
                )
                == 1
            )
        )
        await orchestrator.command_service.check(Thread("bcn-1"))

        delivered = await orchestrator.command_service.send(
            actor=Thread("bcn-1"),
            command_id="command-with-attachments",
            raw_target="dm:channel-bcn-1",
            body="Attached reports.",
            created_at_ms=2,
            attachment_paths=(str(first), str(second)),
        )
        assert isinstance(delivered, MessageSendSuccess)
        delivered = delivered.message

        assert delivered.delivery_state is OutboundDeliveryState.SENT
        assert channel.send_requests[0].attachments == delivered.attachments
        assert [attachment.relative_path for attachment in delivered.attachments] == [
            "first.txt",
            "second.json",
        ]
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_send_preserves_provider_delivery_states() -> None:
    orchestrator, channel, _, storage, audit = await make_node()
    try:
        await channel.inject(make_message(seq=1))
        await wait_until(
            lambda: (
                len(
                    _stored_messages(
                        storage,
                        "bcn-1",
                        direction=MessageDirection.INBOUND,
                    )
                )
                == 1
            )
        )
        target_anchor = make_message(session_id="bcn-other")
        await orchestrator._record_inbound(target_anchor)
        await orchestrator.command_service.check(Thread("bcn-1"))

        # another conversation is answered by another actor, and nothing here
        # reaches it
        with pytest.raises(InboxTargetResolutionError):
            await orchestrator.command_service.send(
                actor=Thread("bcn-1"),
                command_id="command-other-conversation",
                raw_target="dm:channel-bcn-other",
                body="reply",
                created_at_ms=2,
                reply_to_message_id=target_anchor.message_id,
            )
        assert not [
            message
            for message in _stored_messages(
                storage,
                "bcn-other",
                direction=MessageDirection.OUTBOUND,
            )
        ]

        unusable_reply = await orchestrator.command_service.send(
            actor=Thread("bcn-1"),
            command_id="command-invalid-reply",
            raw_target="dm:channel-bcn-1",
            body="invalid reply",
            created_at_ms=3,
            reply_to_message_id=make_message(seq=9).message_id,
        )
        assert isinstance(unusable_reply, MessageSendSuccess)
        assert unusable_reply.message.delivery_state is OutboundDeliveryState.SENT
        assert channel.send_attempts[-1].provider_reply_to_message_id is None
        # falling back to a non-reply send must not persist the reply pointer,
        # or reading the target's history would keep hitting a dangling id
        assert unusable_reply.message.reply_to_message_id is None
        assert any(
            event.event_name == "tool.bcc.message.send.sent" for event in audit.events
        )

        channel.queue_send_result(
            ProviderCallResult(
                status=ProviderCallStatus.FAILED,
                error_kind="provider_rejected",
                error_message="provider rejected delivery",
            )
        )
        failed = await orchestrator.command_service.send(
            actor=Thread("bcn-1"),
            command_id="command-failed",
            raw_target="dm:channel-bcn-1",
            body="failed reply",
            created_at_ms=3,
        )
        assert isinstance(failed, MessageSendSuccess)
        assert failed.message.delivery_state is OutboundDeliveryState.FAILED
        cross_session_attempt_count = len(channel.send_attempts)

        await orchestrator.command_service.check(Thread("bcn-1"))
        with pytest.raises(ValueError, match="must not be empty"):
            await orchestrator.command_service.send(
                actor=Thread("bcn-1"),
                command_id="command-empty-body",
                raw_target="dm:channel-bcn-1",
                body=" \t",
                created_at_ms=3,
            )
        assert all(
            message.command_id != "command-empty-body"
            for message in _stored_message_index(
                storage,
                direction=MessageDirection.OUTBOUND,
            ).values()
        )
        assert len(channel.send_attempts) == cross_session_attempt_count

        channel.queue_send_result(
            ProviderCallResult(
                status=ProviderCallStatus.QUEUED,
                value=ChannelDeliveryReceipt(provider_receipt_ref="queue-1"),
            )
        )
        queued = await orchestrator.command_service.send(
            actor=Thread("bcn-1"),
            command_id="command-queued",
            raw_target="dm:channel-bcn-1",
            body="queued reply",
            created_at_ms=4,
        )
        assert isinstance(queued, MessageSendSuccess)
        queued = queued.message
        assert queued.delivery_state is OutboundDeliveryState.QUEUED
        assert queued.provider_receipt_ref == "queue-1"
        assert channel.queued_messages == [channel.send_attempts[-1]]

        channel.queue_send_result(
            ProviderCallResult(
                status=ProviderCallStatus.UNKNOWN,
                error_kind="transport_eof",
                error_message="delivery outcome is unknown",
                receipt={"provider_receipt_ref": "attempted-send-1"},
            )
        )
        unknown = await orchestrator.command_service.send(
            actor=Thread("bcn-1"),
            command_id="command-unknown",
            raw_target="dm:channel-bcn-1",
            body="unknown reply",
            created_at_ms=5,
        )
        assert isinstance(unknown, MessageSendSuccess)
        unknown = unknown.message
        assert unknown.delivery_state is OutboundDeliveryState.UNKNOWN
        assert unknown.provider_receipt_ref == "attempted-send-1"

        channel.queue_send_result(
            ProviderCallResult(
                status=ProviderCallStatus.PARTIAL,
                value=ChannelDeliveryReceipt(provider_receipt_ref="batch-1"),
                error_kind="provider_rejected_batch",
                error_message="second batch rejected",
                receipt={
                    "total_batches": 2,
                    "confirmed_batches": 1,
                    "batches": (
                        {
                            "provider_request_id": "batch-1",
                            "state": "confirmed",
                        },
                        {
                            "provider_request_id": "batch-2",
                            "state": "failed",
                        },
                    ),
                },
            )
        )
        partial = await orchestrator.command_service.send(
            actor=Thread("bcn-1"),
            command_id="command-partial",
            raw_target="dm:channel-bcn-1",
            body="partial reply",
            created_at_ms=6,
        )
        assert isinstance(partial, MessageSendSuccess)
        partial = partial.message
        assert partial.delivery_state is OutboundDeliveryState.PARTIAL
        assert partial.provider_receipt_ref == "batch-1"
        assert partial.metadata["delivery_receipt"] == {
            "total_batches": 2,
            "confirmed_batches": 1,
            "batches": (
                {
                    "provider_request_id": "batch-1",
                    "state": "confirmed",
                },
                {
                    "provider_request_id": "batch-2",
                    "state": "failed",
                },
            ),
        }

        channel.queue_send_result(
            ProviderCallResult(
                status=ProviderCallStatus.FAILED,
                error_kind="provider_rejected",
                error_message="provider rejected delivery",
                receipt={"provider_receipt_ref": "attempted-send-2"},
            )
        )
        failed = await orchestrator.command_service.send(
            actor=Thread("bcn-1"),
            command_id="command-failed",
            raw_target="dm:channel-bcn-1",
            body="failed reply",
            created_at_ms=7,
        )
        assert isinstance(failed, MessageSendSuccess)
        failed = failed.message
        assert failed.delivery_state is OutboundDeliveryState.FAILED
        assert failed.provider_receipt_ref == "attempted-send-2"
        assert len(channel.send_attempts) == 6
        assert len(channel.sent_messages) == 1
        # a delivered message leaves nothing behind in any conversation
        assert not [
            message
            for message in _stored_message_index(storage).values()
            if message.system_message_kind is not None
        ]
        assert any(
            event.event_name == "channel.outbound.queued" for event in audit.events
        )
        assert any(
            event.event_name == "bcc.send.fresh_check.passed" for event in audit.events
        )
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_graceful_stop_cancels_turn_and_closes_runtime_stream() -> None:
    orchestrator, channel, runtime, storage, audit = await make_node()
    runtime.queue_turn_plan(TestTurnPlan(block_until_release=True))
    task = orchestrator.dispatch_inbound(make_message())
    await wait_until(lambda: bool(runtime.active_streams))
    assert orchestrator.session_runtime_state(Thread("bcn-1")) is State.WORKING

    await orchestrator.stop(timeout=1)
    result = await asyncio.gather(task, return_exceptions=True)

    assert isinstance(result[0], asyncio.CancelledError)
    assert any(
        event.event_name == "runtime.turn.cancelled"
        and event.correlation.turn_id == "turn-message-bcn-1-1"
        for event in audit.events
    )
    assert runtime.closed_streams
    assert runtime.stopped
    assert channel.stopped
    assert not storage.stopped
    assert orchestrator.session_runtime_state(Thread("bcn-1")) is None


@pytest.mark.asyncio
async def test_channel_persists_next_inbound_while_turn_is_active() -> None:
    orchestrator, channel, runtime, storage, audit = await make_node()
    runtime.queue_turn_plan(TestTurnPlan(block_until_release=True))
    first = make_message(seq=1)
    second = make_message(seq=2)

    try:
        await channel.inject(first)
        await wait_until(lambda: bool(runtime.active_streams))

        await channel.inject(second)
        await wait_until(
            lambda: (
                _stored_messages(
                    storage,
                    "bcn-1",
                    direction=MessageDirection.INBOUND,
                )
                == [first, second]
            )
        )
        await wait_until(lambda: len(runtime.steered_turns) == 1)
        assert len(runtime.started_turns) == 1
        steered_session, steered_turn, steer_input = runtime.steered_turns[0]
        assert steered_session.actor.id == "bcn-1"
        assert steered_session == orchestrator.runtime_session(Thread("bcn-1"))
        assert steered_turn.turn_id == "turn-message-bcn-1-1"
        assert steer_input == (
            "[inbox notice:\n"
            "Inbox update: 2 unread messages total; 1 changed target\n"
            "dm:channel-bcn-1  pending: 1 message · first msg=message- · "
            "latest sender @Sender · latest msg=message- · dm]"
        )
        second_body = second.body
        assert second_body is not None
        assert second_body not in steer_input
        second_sender = second.sender
        assert second_sender is not None
        assert f"latest sender @{second_sender.handle}" in steer_input

        runtime.queue_turn_plan(TestTurnPlan())
        next(iter(runtime.active_streams)).release()
        await wait_until(
            lambda: any(
                event.event_name == "runtime.turn.completed"
                and event.correlation.turn_id == "turn-message-bcn-1-2"
                for event in audit.events
            )
        )

        assert any(
            event.event_name == "runtime.turn.completed"
            and event.correlation.turn_id == "turn-message-bcn-1-1"
            for event in audit.events
        )
        assert len(runtime.started_turns) == 2
        assert runtime.started_turns[1][2] == (
            "[inbox notice:\n"
            "Inbox update: 2 unread messages total; 1 changed target\n"
            "dm:channel-bcn-1  pending: 2 messages · first msg=message- · "
            "latest sender @Sender · latest msg=message- · dm\n"
            "]"
        )
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_active_turn_without_provider_id_still_attempts_runtime_steer() -> None:
    orchestrator, channel, runtime, _, _ = await make_node()
    runtime.queue_turn_plan(TestTurnPlan(block_until_release=True))
    first = make_message(seq=1)
    second = make_message(seq=2)

    try:
        await channel.inject(first)
        await wait_until(lambda: bool(runtime.active_streams))
        _, started_turn, _ = runtime.started_turns[0]
        active_turn = orchestrator._runtime_turns[started_turn.turn_id]
        assert active_turn.state is RuntimeTurnState.RUNNING
        orchestrator._runtime_turns[started_turn.turn_id] = replace(
            active_turn, provider_turn_id=None
        )

        await channel.inject(second)
        await wait_until(lambda: len(runtime.steered_turns) == 1)

        _, steered_turn, _ = runtime.steered_turns[0]
        assert steered_turn.turn_id == started_turn.turn_id
        assert steered_turn.provider_turn_id is None
    finally:
        if runtime.active_streams:
            next(iter(runtime.active_streams)).release()
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_one_actor_answers_every_conversation_on_one_runtime() -> None:
    orchestrator, _, runtime, storage, audit = await make_node(
        mode=Mode.DANGEROUS_INDIVIDUAL
    )
    agent = Agent("workspace-1")
    try:
        first, second = await asyncio.gather(
            orchestrator.handle_inbound(make_message(session_id="bcn-a")),
            orchestrator.handle_inbound(make_message(session_id="bcn-b")),
        )

        assert first is not None
        assert second is not None
        assert first.state is RuntimeTurnState.COMPLETED
        assert second.state is RuntimeTurnState.COMPLETED

        # one mailbox, one runtime session, one binding for the whole Agent
        assert list(orchestrator._runtime_queues) == [agent]  # pyright: ignore[reportPrivateUsage]
        assert list(orchestrator._runtime_sessions) == [agent]  # pyright: ignore[reportPrivateUsage]
        assert {session.actor for session in runtime.started_sessions} == {agent}
        assert first.session_id == second.session_id

        # both conversations were taken into the same turn, and each message
        # stays filed under the conversation it came from
        assert first.turn_id == second.turn_id
        assert {
            session_id
            for session_id in storage.messages
            if _stored_messages(
                storage,
                session_id,
                direction=MessageDirection.INBOUND,
            )
        } == {"bcn-a", "bcn-b"}
        assert {
            event.correlation.bcn_session_id
            for event in audit.events
            if event.correlation.turn_id is not None
        } == {"bcn-a"}
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_a_turn_marks_every_conversation_steered_into_it() -> None:
    orchestrator, channel, runtime, _, _ = await make_node(
        mode=Mode.DANGEROUS_INDIVIDUAL
    )
    runtime.accepts_steer = True
    runtime.queue_turn_plan(TestTurnPlan(block_until_release=True))
    try:
        await channel.inject(make_message(session_id="bcn-a"))
        await wait_until(lambda: bool(runtime.active_streams))
        await channel.inject(make_message(session_id="bcn-b"))
        await wait_until(lambda: len(runtime.steered_turns) == 1)

        assert [session_id for session_id, _ in channel.turn_anchors] == [
            "bcn-a",
            "bcn-b",
        ]

        _, opened_turn, _ = runtime.started_turns[0]
        runtime.queue_turn_plan(TestTurnPlan())
        next(iter(runtime.active_streams)).release()

        await wait_until(
            lambda: (
                sorted(_turn_endings(channel, opened_turn.turn_id))
                == ["bcn-a", "bcn-b"]
            )
        )
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_a_conversation_steered_twice_is_marked_once() -> None:
    orchestrator, channel, runtime, _, _ = await make_node()
    runtime.accepts_steer = True
    runtime.queue_turn_plan(TestTurnPlan(block_until_release=True))
    try:
        await channel.inject(make_message(seq=1))
        await wait_until(lambda: bool(runtime.active_streams))
        await channel.inject(make_message(seq=2))
        await channel.inject(make_message(seq=3))
        await wait_until(lambda: len(runtime.steered_turns) == 2)

        assert [session_id for session_id, _ in channel.turn_anchors] == ["bcn-1"]

        _, opened_turn, _ = runtime.started_turns[0]
        runtime.queue_turn_plan(TestTurnPlan())
        next(iter(runtime.active_streams)).release()

        await wait_until(
            lambda: _turn_endings(channel, opened_turn.turn_id) == ["bcn-1"]
        )
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_a_failed_turn_answers_every_conversation_it_took_a_message_from() -> (
    None
):
    orchestrator, channel, runtime, _, _ = await make_node(
        mode=Mode.DANGEROUS_INDIVIDUAL
    )
    loop = asyncio.get_running_loop()
    completions: list[asyncio.Future[RuntimeTurn | None]] = []
    queue = orchestrator._runtime_queue_for_actor(Agent("workspace-1"))  # pyright: ignore[reportPrivateUsage]
    runtime.queue_turn_plan(
        TestTurnPlan(states=(RuntimeEventState.STARTED, RuntimeEventState.FAILED))
    )
    try:
        for session_id in ("bcn-a", "bcn-b", "bcn-c"):
            context, message, _ = await orchestrator._record_inbound(  # pyright: ignore[reportPrivateUsage]
                make_message(session_id=session_id)
            )
            assert context is not None
            if session_id == "bcn-c":
                continue
            completion: asyncio.Future[RuntimeTurn | None] = loop.create_future()
            completions.append(completion)
            queue.put_nowait(_RuntimeNotification(message, context, completion))

        results = await asyncio.gather(*completions)

        assert len(runtime.started_turns) == 1
        assert {result.state for result in results if result is not None} == {
            RuntimeTurnState.FAILED
        }
        assert sorted(request.session_id for request in channel.send_attempts) == [
            "bcn-a",
            "bcn-b",
        ]
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_one_held_error_receipt_does_not_hold_up_the_others() -> None:
    orchestrator, channel, runtime, _, _ = await make_node(
        mode=Mode.DANGEROUS_INDIVIDUAL
    )
    channel.send_gate = asyncio.Event()
    loop = asyncio.get_running_loop()
    completions: list[asyncio.Future[RuntimeTurn | None]] = []
    queue = orchestrator._runtime_queue_for_actor(Agent("workspace-1"))  # pyright: ignore[reportPrivateUsage]
    runtime.queue_turn_plan(
        TestTurnPlan(states=(RuntimeEventState.STARTED, RuntimeEventState.FAILED))
    )
    try:
        for session_id in ("bcn-a", "bcn-b"):
            context, message, _ = await orchestrator._record_inbound(  # pyright: ignore[reportPrivateUsage]
                make_message(session_id=session_id)
            )
            assert context is not None
            completion: asyncio.Future[RuntimeTurn | None] = loop.create_future()
            completions.append(completion)
            queue.put_nowait(_RuntimeNotification(message, context, completion))

        await wait_until(lambda: len(channel.send_attempts) == 2)
        channel.send_gate.set()

        await asyncio.gather(*completions)
        assert sorted(request.session_id for request in channel.send_attempts) == [
            "bcn-a",
            "bcn-b",
        ]
    finally:
        channel.send_gate.set()
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_an_individual_notice_covers_every_conversation_in_reach() -> None:
    orchestrator, _, runtime, _, _ = await make_node(mode=Mode.DANGEROUS_INDIVIDUAL)
    try:
        await orchestrator._record_inbound(make_message(session_id="bcn-b"))  # pyright: ignore[reportPrivateUsage]

        result = await orchestrator.handle_inbound(make_message(session_id="bcn-a"))

        assert result is not None
        assert runtime.started_turns[0][2] == (
            "[inbox notice:\n"
            "Inbox update: 2 unread messages total; 2 changed targets\n"
            "dm:channel-bcn-a  pending: 1 message · first msg=message- · "
            "latest sender @Sender · latest msg=message- · dm\n"
            "dm:channel-bcn-b  pending: 1 message · first msg=message- · "
            "latest sender @Sender · latest msg=message- · dm\n"
            "]"
        )
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_a_batch_notice_covers_every_conversation_it_batched() -> None:
    orchestrator, _, runtime, _, _ = await make_node(mode=Mode.DANGEROUS_INDIVIDUAL)
    loop = asyncio.get_running_loop()
    completions: list[asyncio.Future[RuntimeTurn | None]] = []
    queue = orchestrator._runtime_queue_for_actor(Agent("workspace-1"))  # pyright: ignore[reportPrivateUsage]
    try:
        for session_id in ("bcn-a", "bcn-b"):
            context, message, _ = await orchestrator._record_inbound(  # pyright: ignore[reportPrivateUsage]
                make_message(session_id=session_id)
            )
            assert context is not None
            completion: asyncio.Future[RuntimeTurn | None] = loop.create_future()
            completions.append(completion)
            queue.put_nowait(_RuntimeNotification(message, context, completion))

        await asyncio.gather(*completions)

        assert len(runtime.started_turns) == 1
        assert runtime.started_turns[0][2] == (
            "[inbox notice:\n"
            "Inbox update: 2 unread messages total; 2 changed targets\n"
            "dm:channel-bcn-b  pending: 1 message · first msg=message- · "
            "latest sender @Sender · latest msg=message- · dm\n"
            "dm:channel-bcn-a  pending: 1 message · first msg=message- · "
            "latest sender @Sender · latest msg=message- · dm\n"
            "]"
        )
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_an_individual_actor_drains_and_answers_every_conversation() -> None:
    orchestrator, channel, _, storage, _ = await make_node(
        mode=Mode.DANGEROUS_INDIVIDUAL
    )
    agent = Agent("workspace-1")
    try:
        await orchestrator._record_inbound(make_message(session_id="bcn-a"))
        await orchestrator._record_inbound(make_message(session_id="bcn-b"))

        pending = await orchestrator.command_service.pending_targets(agent)
        assert {summary.session_id for summary in pending.targets} == {
            "bcn-a",
            "bcn-b",
        }

        drained = await orchestrator.command_service.check(agent)
        assert {
            message.session_id for result in drained for message in result.messages
        } == {"bcn-a", "bcn-b"}
        # every conversation keeps its own cursor, and each one moved
        assert storage.cursors["bcn-a"].delivered_through_seq > 0
        assert storage.cursors["bcn-b"].delivered_through_seq > 0
        assert not (await orchestrator.command_service.pending_targets(agent)).targets

        # unread somewhere else does not stand between the Agent and this target
        await orchestrator._record_inbound(make_message(session_id="bcn-b", seq=2))
        sent = await orchestrator.command_service.send(
            actor=agent,
            command_id="command-individual",
            raw_target="dm:channel-bcn-a",
            body="reply",
            created_at_ms=2,
        )
        assert isinstance(sent, MessageSendSuccess)
        assert channel.send_attempts[-1].session_id == "bcn-a"

        held = await orchestrator.command_service.send(
            actor=agent,
            command_id="command-individual-held",
            raw_target="dm:channel-bcn-b",
            body="reply",
            created_at_ms=3,
        )
        assert isinstance(held, MessageSendFreshnessHold)
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_multiple_sessions_keep_workspace_and_correlation_isolated() -> None:
    orchestrator, _, runtime, storage, audit = await make_node()
    try:
        first, second = await asyncio.gather(
            orchestrator.handle_inbound(make_message(session_id="bcn-a")),
            orchestrator.handle_inbound(make_message(session_id="bcn-b")),
        )

        assert first is not None
        assert second is not None
        assert first.state is RuntimeTurnState.COMPLETED
        assert second.state is RuntimeTurnState.COMPLETED
        assert storage.bcn_sessions["bcn-a"].workspace_id == "workspace-1"
        assert storage.bcn_sessions["bcn-b"].workspace_id == "workspace-1"
        assert {
            session_id
            for session_id in storage.messages
            if _stored_messages(
                storage,
                session_id,
                direction=MessageDirection.INBOUND,
            )
        } == {"bcn-a", "bcn-b"}
        assert {
            event.correlation.bcn_session_id
            for event in audit.events
            if event.correlation.turn_id is not None
        } == {"bcn-a", "bcn-b"}
        assert {session.actor.id for session in runtime.started_sessions} == {
            "bcn-a",
            "bcn-b",
        }
        first_runtime = orchestrator.runtime_session(Thread("bcn-a"))
        second_runtime = orchestrator.runtime_session(Thread("bcn-b"))
        assert first_runtime is not None
        assert second_runtime is not None
        assert first_runtime.id != second_runtime.id
        assert UUID(first_runtime.id).version == 7
        assert UUID(second_runtime.id).version == 7
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_inbound_deduplication_uses_external_conversation_identity() -> None:
    orchestrator, _, runtime, storage, _ = await make_node()
    try:
        first = replace(
            make_message(session_id="bcn-a"),
            provider_message_id="shared-provider-message",
        )
        first_turn = await orchestrator.handle_inbound(first)
        assert first_turn is not None

        replay = replace(
            first,
            message_id="volatile-retry-message-id",
            body="volatile retry body",
            received_at_ms=999,
        )
        assert await orchestrator.handle_inbound(replay) is None
        assert _stored_messages(
            storage,
            "bcn-a",
            direction=MessageDirection.INBOUND,
        ) == [replace(first, notifies_runtime=True)]

        other_conversation = replace(
            make_message(session_id="bcn-b"),
            provider_message_id=first.provider_message_id,
        )
        other_turn = await orchestrator.handle_inbound(other_conversation)
        assert other_turn is not None
        assert (
            len(
                _stored_messages(
                    storage,
                    "bcn-b",
                    direction=MessageDirection.INBOUND,
                )
            )
            == 1
        )
        assert len(runtime.started_turns) == 2
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_group_mention_starts_following_after_quiet_history() -> None:
    orchestrator, _, runtime, storage, _ = await make_node()
    try:
        quiet = replace(
            make_message(seq=1),
            target_kind=ChannelTargetKind.GROUP,
            mentions_agent=False,
        )
        assert await orchestrator.handle_inbound(quiet) is None
        assert storage.channel_sessions["channel-bcn-1"].following is False
        assert orchestrator.runtime_session(Thread("bcn-1")) is None
        assert runtime.started_sessions == []
        (checked,) = await orchestrator.command_service.check(Thread("bcn-1"))
        assert checked.messages == ()
        history = await orchestrator.command_service.read(
            Thread("bcn-1"), raw_target="#test:channel-bcn-1"
        )
        assert len(history.messages) == 1
        assert history.messages[0].notifies_runtime is False

        mention = replace(
            make_message(seq=2),
            target_kind=ChannelTargetKind.GROUP,
            mentions_agent=True,
        )
        turn = await orchestrator.handle_inbound(mention)
        assert turn is not None
        assert turn.state is RuntimeTurnState.COMPLETED
        assert storage.channel_sessions["channel-bcn-1"].following is True

        follow_up = replace(
            make_message(seq=3),
            target_kind=ChannelTargetKind.GROUP,
            mentions_agent=False,
        )
        follow_up_turn = await orchestrator.handle_inbound(follow_up)
        assert follow_up_turn is not None
        assert follow_up_turn.state is RuntimeTurnState.COMPLETED

        unfollowed = await orchestrator.command_service.unfollow(
            Thread("bcn-1"), raw_target="#test:channel-bcn-1"
        )
        assert unfollowed.changed is True
        assert storage.channel_sessions["channel-bcn-1"].following is False
        after_unfollow = replace(
            make_message(seq=4),
            target_kind=ChannelTargetKind.GROUP,
            mentions_agent=False,
        )
        assert await orchestrator.handle_inbound(after_unfollow) is None
        assert (
            _stored_messages(
                storage,
                "bcn-1",
                direction=MessageDirection.INBOUND,
            )[-1].notifies_runtime
            is False
        )

        assert await orchestrator.handle_inbound(quiet) is None
        inbound = _stored_messages(
            storage,
            "bcn-1",
            direction=MessageDirection.INBOUND,
        )
        assert len(inbound) == 4
        assert inbound[0].notifies_runtime is False
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_batched_notifications_collapse_into_one_turn() -> None:
    orchestrator, _, runtime, _, _ = await make_node()
    first_context, first_message, first_created = await orchestrator._record_inbound(
        make_message(seq=1)
    )
    second_context, second_message, second_created = await orchestrator._record_inbound(
        make_message(seq=2)
    )
    assert first_context is not None
    assert second_context is not None
    assert first_created
    assert second_created
    loop = asyncio.get_running_loop()
    first_completion: asyncio.Future[RuntimeTurn | None] = loop.create_future()
    second_completion: asyncio.Future[RuntimeTurn | None] = loop.create_future()
    runtime_queue = orchestrator._runtime_queue_for_actor(Thread("bcn-1"))
    runtime_queue.put_nowait(
        _RuntimeNotification(
            first_message,
            first_context,
            first_completion,
        )
    )
    runtime_queue.put_nowait(
        _RuntimeNotification(
            second_message,
            second_context,
            second_completion,
        )
    )
    try:
        first_result, second_result = await asyncio.gather(
            first_completion,
            second_completion,
        )

        assert first_result == second_result
        assert len(runtime.started_turns) == 1
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "language", "expected_body"),
    (
        (
            RuntimeEventState.FAILED,
            ENGLISH,
            "Execution failed: test <redacted> failure",
        ),
        (
            RuntimeEventState.UNKNOWN,
            SIMPLIFIED_CHINESE,
            "执行状态未知：test <redacted> failure",
        ),
    ),
)
async def test_terminal_runtime_error_replies_on_original_route(
    state: RuntimeEventState,
    language: str,
    expected_body: str,
) -> None:
    orchestrator, channel, runtime, storage, audit = await make_node(
        translator=create_translator(language),
        error_feedback_detail=lambda _, text: text.replace("provider", "<redacted>"),
    )
    message = replace(
        make_message(),
        target_kind=ChannelTargetKind.GROUP,
        mentions_agent=True,
        provider_thread_id="provider-thread-7",
        provider_message_id="provider-message-7",
    )
    runtime.queue_turn_plan(TestTurnPlan(states=(RuntimeEventState.STARTED, state)))
    try:
        result = await orchestrator.handle_inbound(message)

        assert result is not None
        assert result.state.value == state.value
        assert result.error_message == "test provider failure"
        assert len(channel.send_attempts) == 1
        request = channel.send_attempts[0]
        assert request.body == expected_body
        assert request.session_id == "bcn-1"
        assert request.target_kind is ChannelTargetKind.GROUP
        assert request.provider_thread_id == "provider-thread-7"
        assert request.provider_reply_to_message_id == "provider-message-7"
        assert not _stored_message_index(
            storage,
            direction=MessageDirection.OUTBOUND,
        )
        assert [
            event.event_name
            for event in audit.events
            if event.event_name.startswith("runtime.error_feedback.")
        ] == [
            "runtime.error_feedback.started",
            "runtime.error_feedback.sent",
        ]
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider_result",
    (
        ProviderCallResult[ChannelDeliveryReceipt](
            status=ProviderCallStatus.FAILED,
            error_kind="provider_failed",
            error_message="feedback failed",
        ),
        ProviderCallResult[ChannelDeliveryReceipt](
            status=ProviderCallStatus.PARTIAL,
            value=ChannelDeliveryReceipt(provider_receipt_ref="partial-feedback"),
            error_kind="provider_partial",
            error_message="feedback was partial",
        ),
        ProviderCallResult[ChannelDeliveryReceipt](
            status=ProviderCallStatus.UNKNOWN,
            error_kind="provider_unknown",
            error_message="feedback outcome is unknown",
        ),
    ),
)
async def test_feedback_delivery_outcome_preserves_original_runtime_turn(
    provider_result: ProviderCallResult[ChannelDeliveryReceipt],
) -> None:
    orchestrator, channel, runtime, _, audit = await make_node()
    runtime.queue_turn_plan(
        TestTurnPlan(states=(RuntimeEventState.STARTED, RuntimeEventState.FAILED))
    )
    channel.queue_send_result(provider_result)
    try:
        result = await orchestrator.handle_inbound(make_message())

        assert result is not None
        assert result.state is RuntimeTurnState.FAILED
        assert result.error_message == "test provider failure"
        assert len(channel.send_attempts) == 1
        feedback_events = [
            event
            for event in audit.events
            if event.event_name.startswith("runtime.error_feedback.")
        ]
        assert [event.event_name for event in feedback_events] == [
            "runtime.error_feedback.started",
            "runtime.error_feedback.failed",
        ]
        assert feedback_events[-1].metadata["delivery_state"] == (
            provider_result.status.value
        )
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_runtime_error_feedback() -> None:
    # a reporter exception preserves the original runtime turn
    invalid_channel = _InvalidSendResultChannel()
    orchestrator, channel, runtime, _, _ = await make_node(channel=invalid_channel)
    runtime.queue_turn_plan(
        TestTurnPlan(states=(RuntimeEventState.STARTED, RuntimeEventState.FAILED))
    )
    try:
        result = await orchestrator.handle_inbound(make_message())

        assert result is not None
        assert result.state is RuntimeTurnState.FAILED
        assert result.error_message == "test provider failure"
        assert len(channel.send_attempts) == 1
    finally:
        await orchestrator.stop(timeout=1)

    # batched notifications send one error feedback
    orchestrator, channel, runtime, _, _ = await make_node()
    first_context, first_message, first_created = await orchestrator._record_inbound(
        make_message(seq=1)
    )
    second_context, second_message, second_created = await orchestrator._record_inbound(
        make_message(seq=2)
    )
    assert first_context is not None
    assert second_context is not None
    assert first_created
    assert second_created
    loop = asyncio.get_running_loop()
    first_completion: asyncio.Future[RuntimeTurn | None] = loop.create_future()
    second_completion: asyncio.Future[RuntimeTurn | None] = loop.create_future()
    runtime.queue_turn_plan(
        TestTurnPlan(states=(RuntimeEventState.STARTED, RuntimeEventState.FAILED))
    )
    runtime_queue = orchestrator._runtime_queue_for_actor(Thread("bcn-1"))
    runtime_queue.put_nowait(
        _RuntimeNotification(
            first_message,
            first_context,
            first_completion,
        )
    )
    runtime_queue.put_nowait(
        _RuntimeNotification(
            second_message,
            second_context,
            second_completion,
        )
    )
    try:
        first_result, second_result = await asyncio.gather(
            first_completion,
            second_completion,
        )

        assert first_result == second_result
        assert len(runtime.started_turns) == 1
        assert len(channel.send_attempts) == 1
        assert channel.send_attempts[0].provider_reply_to_message_id == (
            first_message.provider_message_id
        )
    finally:
        await orchestrator.stop(timeout=1)

    # a reminder system message replies to the human anchor it speaks for
    orchestrator, channel, runtime, storage, _ = await make_node()
    anchor = make_message(seq=1, message_id=str(uuid7()))
    try:
        initial_turn = await orchestrator.handle_inbound(anchor)
        assert initial_turn is not None
        canonical_anchor = _stored_messages(
            storage,
            "bcn-1",
            direction=MessageDirection.INBOUND,
        )[0]
        reminder = await storage.scope("workspace-1", "Test Agent").save_new_reminder(
            Reminder(
                reminder_id="pending",
                owner_session_id=canonical_anchor.session_id,
                anchor_message_id=canonical_anchor.message_id,
                title="Review",
                state=ReminderState.SCHEDULED,
                next_fire_at_ms=10,
                repeat_rule=None,
                timezone="UTC",
                revision=1,
                last_occurrence_no=0,
                created_at_ms=2,
                updated_at_ms=2,
            )
        )
        reminder_message = await cast(IStorage, storage).save_message(
            Message(
                direction=MessageDirection.INBOUND,
                seq=0,
                message_id=str(uuid7()),
                session_id=canonical_anchor.session_id,
                channel_session_id=canonical_anchor.channel_session_id,
                channel=canonical_anchor.channel,
                provider_thread_id=canonical_anchor.provider_thread_id,
                provider_message_id=None,
                received_at_ms=2,
                sender=SenderIdentity(name="system"),
                target=canonical_anchor.target,
                target_kind=canonical_anchor.target_kind,
                body='🔔 Reminder #019c1234 (one-time) — dm:alice — "Review"',
                metadata={
                    "sender_kind": SenderKind.SYSTEM.value,
                    "system_message_kind": SystemMessageKind.REMINDER.value,
                    "reminder_id": reminder.reminder_id,
                },
            )
        )
        runtime.queue_turn_plan(
            TestTurnPlan(states=(RuntimeEventState.STARTED, RuntimeEventState.FAILED))
        )

        await orchestrator.publish_inbox_wake(reminder_message)
        await wait_until(lambda: len(channel.send_attempts) == 1)

        request = channel.send_attempts[0]
        assert request.session_id == canonical_anchor.session_id
        assert request.target_kind is canonical_anchor.target_kind
        assert request.provider_thread_id == canonical_anchor.provider_thread_id
        assert (
            request.provider_reply_to_message_id == canonical_anchor.provider_message_id
        )
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_reminder_wakes_use_ordinary_inbox_for_idle_active_and_duplicates() -> (
    None
):
    orchestrator, _, runtime, storage, _ = await make_node()
    runtime.queue_turn_plan(TestTurnPlan(block_until_release=True))
    active_task = orchestrator.dispatch_inbound(
        make_message(session_id="bcn-active", seq=1)
    )
    try:
        await runtime.turn_started.wait()
        idle_context, idle_anchor, created = await orchestrator._record_inbound(
            make_message(session_id="bcn-idle", seq=2)
        )
        assert idle_context is not None
        assert created
        active_anchor = _stored_messages(
            storage,
            "bcn-active",
            direction=MessageDirection.INBOUND,
        )[0]

        async def save_reminder_message(
            anchor: Message, received_at_ms: int
        ) -> Message:
            return await cast(IStorage, storage).save_message(
                Message(
                    direction=MessageDirection.INBOUND,
                    seq=0,
                    message_id=str(uuid7()),
                    session_id=anchor.session_id,
                    channel_session_id=anchor.channel_session_id,
                    channel=anchor.channel,
                    provider_thread_id=anchor.provider_thread_id,
                    provider_message_id=None,
                    received_at_ms=received_at_ms,
                    sender=SenderIdentity(name="system"),
                    target=anchor.target,
                    target_kind=anchor.target_kind,
                    body=(
                        f'🔔 Reminder #019c1234 (one-time) — {anchor.target} — "Review"'
                    ),
                    metadata={
                        "sender_kind": SenderKind.SYSTEM.value,
                        "system_message_kind": SystemMessageKind.REMINDER.value,
                    },
                )
            )

        idle_reminder = await save_reminder_message(idle_anchor, 3)
        active_reminder = await save_reminder_message(active_anchor, 4)
        storage.runtime_attempts[f"turn-{idle_reminder.message_id}"] = RuntimeAttempt(
            turn_id=f"turn-{idle_reminder.message_id}",
            session_id="previous-runtime",
            client_user_message_id=idle_reminder.message_id,
            started_at_ms=2,
        )
        await orchestrator.publish_inbox_wake(idle_reminder)
        await orchestrator.publish_inbox_wake(active_reminder)
        async with asyncio.timeout(1):
            while len(runtime.started_turns) != 2 or len(runtime.steered_turns) != 1:
                await asyncio.sleep(0.01)

        idle_notice = runtime.started_turns[1][2]
        _, _, active_notice = runtime.steered_turns[0]
        assert "Inbox update: 2 unread messages total; 1 changed target" in idle_notice
        assert "pending: 2 messages" in idle_notice
        assert "latest sender @system" in idle_notice
        assert (
            "Inbox update: 2 unread messages total; 1 changed target" in active_notice
        )
        assert "pending: 1 message" in active_notice
        assert "latest sender @system" in active_notice
        assert "reminder notice" not in idle_notice + active_notice

        await cast(IStorage, storage).check_messages(
            "bcn-idle",
            checked_at_ms=5,
        )
        await cast(IStorage, storage).check_messages(
            "bcn-active",
            checked_at_ms=5,
        )
        await orchestrator.publish_inbox_wake(idle_reminder)
        await orchestrator.publish_inbox_wake(active_reminder)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert len(runtime.started_turns) == 2
        assert len(runtime.steered_turns) == 1
    finally:
        for stream in tuple(runtime.active_streams):
            stream.release()
        await asyncio.gather(active_task, return_exceptions=True)
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_runtime_start_failure_handling(monkeypatch: pytest.MonkeyPatch) -> None:
    # an inbound failure rolls back new session state
    orchestrator, _, _, storage, _ = await make_node()
    try:
        invalid = replace(
            make_message(session_id="invalid", seq=2),
            target_kind=ChannelTargetKind.GROUP,
            mentions_agent=False,
            reply_to_message_id="missing-message",
        )
        with pytest.raises(ValueError, match="does not reference a message"):
            await orchestrator.handle_inbound(invalid)

        assert storage.channel_sessions == {}
        assert storage.bcn_sessions == {}
        assert storage.cursors == {}
        assert storage.messages == {}
        assert orchestrator.runtime_session(Thread("invalid")) is None
    finally:
        await orchestrator.stop(timeout=1)

    # a start failure replaces the session for the current inbound
    orchestrator, _, runtime, _, _ = await make_node()
    try:
        runtime.queue_start_result(
            ProviderCallResult(
                status=ProviderCallStatus.FAILED,
                error_kind="provider_failed",
                error_message="start failed",
            )
        )
        task = orchestrator.dispatch_inbound(make_message())
        result = await task

        assert result is not None
        assert result.state is RuntimeTurnState.COMPLETED
        assert orchestrator.session_runtime_state(Thread("bcn-1")) is State.IDLE
        current_runtime = orchestrator.runtime_session(Thread("bcn-1"))
        assert current_runtime is not None
        assert len(runtime.started_sessions) == 2
        assert runtime.started_sessions[0].id != current_runtime.id
        assert runtime.started_sessions[1].id == current_runtime.id
    finally:
        await orchestrator.stop(timeout=1)

    # repeated unknown starts abandon each session without blocking
    orchestrator, _, runtime, _, _ = await make_node()
    release_stop = asyncio.Event()

    async def blocked_stop_session(
        session: RuntimeSession,
        *,
        timeout: float,
    ) -> ProviderCallResult[RuntimeSession]:
        assert timeout == 1
        runtime.stopped_sessions.append(session)
        await release_stop.wait()
        return ProviderCallResult(status=ProviderCallStatus.CONFIRMED, value=session)

    monkeypatch.setattr(runtime, "stop_session", blocked_stop_session)
    try:
        for _ in range(2):
            runtime.queue_start_result(
                ProviderCallResult(
                    status=ProviderCallStatus.UNKNOWN,
                    error_kind="provider_unknown",
                    error_message="start outcome is unknown",
                )
            )

        result = await orchestrator.dispatch_inbound(make_message())

        assert result is not None
        assert result.state is RuntimeTurnState.UNKNOWN
        await wait_until(lambda: runtime.stopped_sessions == runtime.started_sessions)
        assert orchestrator.runtime_session(Thread("bcn-1")) is None
    finally:
        release_stop.set()
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_a_refused_start_is_reported_as_failed_not_unknown() -> None:
    # a provider that definitively refuses to start says so; only silence is unknown
    orchestrator, _, runtime, _, _ = await make_node()
    try:
        for _ in range(2):
            runtime.queue_start_result(
                ProviderCallResult(
                    status=ProviderCallStatus.FAILED,
                    error_kind="provider_failed",
                    error_message="start failed",
                )
            )

        result = await orchestrator.dispatch_inbound(make_message())

        assert result is not None
        assert result.state is RuntimeTurnState.FAILED
        assert result.error_kind == ErrorKind.PROVIDER_FAILED.value
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_provisional_unknown_waits_for_reconciliation_before_it_is_announced() -> (
    None
):
    orchestrator, channel, runtime, _, _ = await make_node()
    try:
        runtime.queue_reconcile_result(
            ProviderCallResult(
                status=ProviderCallStatus.FAILED,
                error_kind="provider_failed",
                error_message="runtime session cannot be reconciled",
            )
        )
        runtime.queue_turn_plan(
            TestTurnPlan(states=(RuntimeEventState.STARTED, RuntimeEventState.UNKNOWN))
        )

        turn = await orchestrator.handle_inbound(make_message())

        assert turn is not None
        assert turn.state is RuntimeTurnState.UNKNOWN
        terminals = [
            event
            for event in channel.events
            if isinstance(event.payload, TurnFailed | TurnUnknown)
        ]
        assert len(terminals) == 1
        terminal = terminals[0]
        assert isinstance(terminal.payload, TurnUnknown)
        assert terminal.payload.event_name == "bcn.turn.unknown"
        assert terminal.envelope.turn_id == turn.turn_id
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    (ProviderCallStatus.FAILED, ProviderCallStatus.UNKNOWN),
)
async def test_unconfirmed_reconcile_clears_session_before_next_inbound(
    status: ProviderCallStatus,
) -> None:
    orchestrator, _, runtime, _, _ = await make_node()
    try:
        runtime.queue_reconcile_result(
            ProviderCallResult(
                status=status,
                error_kind=(
                    "provider_failed"
                    if status is ProviderCallStatus.FAILED
                    else "provider_unknown"
                ),
                error_message="runtime session cannot be reconciled",
            )
        )
        runtime.queue_turn_plan(
            TestTurnPlan(states=(RuntimeEventState.STARTED, RuntimeEventState.UNKNOWN))
        )
        first_turn = await orchestrator.handle_inbound(make_message(seq=1))
        assert first_turn is not None
        assert first_turn.state is RuntimeTurnState.UNKNOWN
        assert orchestrator.runtime_session(Thread("bcn-1")) is None
        assert orchestrator.session_runtime_state(Thread("bcn-1")) is None
        assert len(runtime.reconciled_sessions) == 1
        first_runtime = runtime.reconciled_sessions[0]

        second_turn = await orchestrator.handle_inbound(make_message(seq=2))

        assert second_turn is not None
        assert second_turn.state is RuntimeTurnState.COMPLETED
        current_runtime = orchestrator.runtime_session(Thread("bcn-1"))
        assert current_runtime is not None
        assert current_runtime.id != first_runtime.id
        assert runtime.reconciled_sessions == [first_runtime]
        assert runtime.started_sessions[-1].id == current_runtime.id
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_runtime_session_reconciliation() -> None:
    # an unknown turn reconciles immediately
    orchestrator, _, runtime, _, _ = await make_node()
    try:
        runtime.queue_turn_plan(
            TestTurnPlan(states=(RuntimeEventState.STARTED, RuntimeEventState.UNKNOWN))
        )
        turn = await orchestrator.handle_inbound(make_message(seq=1))

        assert turn is not None
        assert turn.state is RuntimeTurnState.UNKNOWN
        current_runtime = orchestrator.runtime_session(Thread("bcn-1"))
        assert current_runtime is not None
        assert orchestrator.session_runtime_state(Thread("bcn-1")) is State.IDLE
        assert runtime.reconciled_sessions == [current_runtime]
    finally:
        await orchestrator.stop(timeout=1)

    # an unknown turn reconciliation restores the working turn and steers
    orchestrator, channel, runtime, _, _ = await make_node()
    runtime.queue_turn_plan(
        TestTurnPlan(states=(RuntimeEventState.STARTED, RuntimeEventState.UNKNOWN))
    )
    runtime.queue_reconcile_turn_plan(
        TestTurnPlan(
            states=(RuntimeEventState.COMPLETED,),
            block_until_release=True,
        )
    )
    first_task = orchestrator.dispatch_inbound(make_message(seq=1))

    try:
        await wait_until(
            lambda: bool(runtime.reconciled_sessions and runtime.active_streams)
        )
        current_runtime = orchestrator.runtime_session(Thread("bcn-1"))
        assert current_runtime is not None
        assert orchestrator.session_runtime_state(Thread("bcn-1")) is State.WORKING

        second_task = orchestrator.dispatch_inbound(make_message(seq=2))
        await wait_until(lambda: len(runtime.steered_turns) == 1)
        steered_session, steered_turn, _ = runtime.steered_turns[0]
        assert steered_session == current_runtime
        assert steered_turn.turn_id == "turn-message-bcn-1-1"
        assert steered_turn.provider_turn_id == ("test-provider-turn-message-bcn-1-1")

        next(iter(runtime.active_streams)).release()
        first_turn, second_turn = await asyncio.gather(first_task, second_task)

        assert first_turn is not None
        assert first_turn.state is RuntimeTurnState.COMPLETED
        assert second_turn is not None
        assert second_turn.state is RuntimeTurnState.COMPLETED
        assert orchestrator.runtime_session(Thread("bcn-1")) == current_runtime
        assert orchestrator.session_runtime_state(Thread("bcn-1")) is State.IDLE
        assert runtime.reconciled_sessions == [current_runtime]
        assert channel.send_attempts == []
    finally:
        if not first_task.done():
            first_task.cancel()
        await orchestrator.stop(timeout=1)

    # a confirmed stop replaces the runtime session on the next inbound
    orchestrator, _, runtime, _, _ = await make_node()
    try:
        first_turn = await orchestrator.handle_inbound(make_message(seq=1))
        assert first_turn is not None
        first_runtime = orchestrator.runtime_session(Thread("bcn-1"))
        assert first_runtime is not None

        await orchestrator._stop_runtime_session(first_runtime, timeout=1)

        assert orchestrator.runtime_session(Thread("bcn-1")) is None
        assert orchestrator.session_runtime_state(Thread("bcn-1")) is None
        second_turn = await orchestrator.handle_inbound(make_message(seq=2))
        assert second_turn is not None
        second_runtime = orchestrator.runtime_session(Thread("bcn-1"))
        assert second_runtime is not None
        assert second_runtime.id != first_runtime.id
        assert UUID(second_runtime.id).version == 7
        assert [session.id for session in runtime.started_sessions] == [
            first_runtime.id,
            second_runtime.id,
        ]
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_runtime_teardown_scheduling(monkeypatch: pytest.MonkeyPatch) -> None:
    # teardown is scheduled without blocking the next inbound
    orchestrator, _, runtime, _, _ = await make_node()
    stop_started = asyncio.Event()
    release_stop = asyncio.Event()

    async def blocked_stop_session_2(
        session: RuntimeSession,
        *,
        timeout: float,
    ) -> ProviderCallResult[RuntimeSession]:
        assert timeout == 1
        runtime.stopped_sessions.append(session)
        stop_started.set()
        await release_stop.wait()
        return ProviderCallResult(
            status=ProviderCallStatus.CONFIRMED,
            value=session,
        )

    monkeypatch.setattr(runtime, "stop_session", blocked_stop_session_2)
    try:
        first_turn = await orchestrator.handle_inbound(make_message(seq=1))
        assert first_turn is not None
        first_runtime = orchestrator.runtime_session(Thread("bcn-1"))
        assert first_runtime is not None

        await orchestrator._stop_runtime_session(first_runtime, timeout=1)
        await stop_started.wait()

        assert orchestrator.runtime_session(Thread("bcn-1")) is None
        assert len(orchestrator._runtime_teardown_tasks) == 1

        second_turn = await asyncio.wait_for(
            orchestrator.handle_inbound(make_message(seq=2)),
            timeout=1,
        )
        assert second_turn is not None
        second_runtime = orchestrator.runtime_session(Thread("bcn-1"))
        assert second_runtime is not None
        assert second_runtime.id != first_runtime.id
        assert not release_stop.is_set()

        release_stop.set()
        await wait_until(lambda: not orchestrator._runtime_teardown_tasks)
    finally:
        release_stop.set()
        await orchestrator.stop(timeout=1)

    # shutdown gathers the outstanding teardown tasks
    orchestrator, _, runtime, _, _ = await make_node()
    stop_started = asyncio.Event()
    release_stop = asyncio.Event()
    shutdown_task: asyncio.Task[None] | None = None

    async def blocked_stop_session_3(
        session: RuntimeSession,
        *,
        timeout: float,
    ) -> ProviderCallResult[RuntimeSession]:
        assert timeout == 1
        runtime.stopped_sessions.append(session)
        stop_started.set()
        await release_stop.wait()
        return ProviderCallResult(
            status=ProviderCallStatus.CONFIRMED,
            value=session,
        )

    monkeypatch.setattr(runtime, "stop_session", blocked_stop_session_3)
    try:
        first_turn = await orchestrator.handle_inbound(make_message(seq=1))
        assert first_turn is not None
        runtime_session = orchestrator.runtime_session(Thread("bcn-1"))
        assert runtime_session is not None

        await orchestrator._stop_runtime_session(runtime_session, timeout=1)
        await stop_started.wait()

        shutdown_task = asyncio.create_task(orchestrator.stop(timeout=1))
        await asyncio.sleep(0.05)
        assert not shutdown_task.done()

        release_stop.set()
        await shutdown_task
        assert orchestrator._runtime_teardown_tasks == set()
    finally:
        release_stop.set()
        if shutdown_task is not None and not shutdown_task.done():
            await asyncio.wait_for(shutdown_task, timeout=2)
        elif shutdown_task is None and not orchestrator._stopping:
            await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    (ProviderCallStatus.FAILED, ProviderCallStatus.UNKNOWN),
)
async def test_unconfirmed_stop_clears_runtime_session(
    status: ProviderCallStatus,
) -> None:
    orchestrator, _, runtime, _, _ = await make_node()
    try:
        first_turn = await orchestrator.handle_inbound(make_message(seq=1))
        assert first_turn is not None
        first_runtime = orchestrator.runtime_session(Thread("bcn-1"))
        assert first_runtime is not None
        runtime.queue_stop_result(
            ProviderCallResult(
                status=status,
                error_kind=(
                    "provider_failed"
                    if status is ProviderCallStatus.FAILED
                    else "provider_unknown"
                ),
                error_message="stop was not confirmed",
            )
        )

        await orchestrator._stop_runtime_session(first_runtime, timeout=1)

        assert orchestrator.runtime_session(Thread("bcn-1")) is None
        assert orchestrator.session_runtime_state(Thread("bcn-1")) is None
        second_turn = await orchestrator.handle_inbound(make_message(seq=2))
        assert second_turn is not None
        replacement = orchestrator.runtime_session(Thread("bcn-1"))
        assert replacement is not None
        assert replacement.id != first_runtime.id
        assert runtime.started_sessions[-1].id == replacement.id
        assert not runtime.reconciled_sessions
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_daemon_lifecycle_creates_a_new_runtime_session() -> None:
    first, _, _, storage, _ = await make_node()
    first_runtime_id: str | None = None
    try:
        first_turn = await first.handle_inbound(make_message(seq=1))
        assert first_turn is not None
        first_runtime = first.runtime_session(Thread("bcn-1"))
        assert first_runtime is not None
        first_runtime_id = first_runtime.id
    finally:
        await first.stop(timeout=1)

    channel = TestChannel()
    runtime = TestRuntime()
    second = SessionOrchestrator(
        actors=Actors(agent_id="workspace-1", mode=Mode.SESSION),
        channel=channel,
        runtimes=(runtime,),
        storage=storage.scope("workspace-1", "Test Agent"),
        audit=RecordingAudit(),
        timeout_budget=make_budget(),
        timer_wheel=TimerWheel(),
        workspace=Path.cwd,
        translator=_ENGLISH_TRANSLATOR,
        error_feedback_detail=unchanged_error_feedback_detail,
    )
    runtime.command_service = second.command_service
    await second.start(timeout=1)
    try:
        second_turn = await second.handle_inbound(make_message(seq=2))
        assert second_turn is not None
        second_runtime = second.runtime_session(Thread("bcn-1"))
        assert second_runtime is not None
        assert first_runtime_id is not None
        assert second_runtime.id != first_runtime_id
        assert UUID(second_runtime.id).version == 7
    finally:
        await second.stop(timeout=1)


@pytest.mark.asyncio
async def test_quiet_inbound_does_not_create_runtime_state_or_cursor() -> None:
    orchestrator, _, _, storage, _ = await make_node()
    try:
        result = await orchestrator.handle_inbound(
            replace(make_message(seq=1), notifies_runtime=False)
        )

        assert result is None
        assert orchestrator.runtime_session(Thread("bcn-1")) is None
        assert orchestrator.session_runtime_state(Thread("bcn-1")) is None
        assert await storage.get_consumer_cursor("bcn-1") is None
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_context_expiry() -> None:
    # a context expire fans out once to all live sessions
    orchestrator, _, runtime, _, _ = await make_node()
    try:
        await asyncio.gather(
            orchestrator.handle_inbound(make_message(session_id="bcn-a", seq=1)),
            orchestrator.handle_inbound(make_message(session_id="bcn-b", seq=1)),
        )
        first_a = orchestrator.runtime_session(Thread("bcn-a"))
        first_b = orchestrator.runtime_session(Thread("bcn-b"))
        assert first_a is not None
        assert first_b is not None

        runtime.emit_expire(first_a.id)
        runtime.emit_expire(first_a.id)
        await wait_until(
            lambda: (
                orchestrator.runtime_session(Thread("bcn-a")) is None
                and orchestrator.runtime_session(Thread("bcn-b")) is None
            )
        )
        await wait_until(lambda: len(runtime.stopped_sessions) == 2)

        assert {session.id for session in runtime.stopped_sessions} == {
            first_a.id,
            first_b.id,
        }
        assert len(runtime.stopped_sessions) == 2
        assert len(runtime.started_sessions) == 2

        await orchestrator.handle_inbound(make_message(session_id="bcn-a", seq=2))
        second_a = orchestrator.runtime_session(Thread("bcn-a"))
        assert second_a is not None
        assert second_a.id != first_a.id
        runtime.emit_expire(second_a.id)
        await wait_until(lambda: orchestrator.runtime_session(Thread("bcn-a")) is None)
        await wait_until(lambda: len(runtime.stopped_sessions) == 3)

        assert runtime.stopped_sessions.count(second_a) == 1
        assert len(runtime.started_sessions) == 3
    finally:
        await orchestrator.stop(timeout=1)

    # a context expire waits for an active turn, then precedes pending inbound
    orchestrator, _, runtime, _, _ = await make_node()
    runtime.queue_turn_plan(TestTurnPlan(block_until_release=True))
    first_task = orchestrator.dispatch_inbound(make_message(seq=1))
    try:
        await runtime.turn_started.wait()
        first_runtime = orchestrator.runtime_session(Thread("bcn-1"))
        assert first_runtime is not None

        runtime.emit_expire(first_runtime.id)
        await wait_until(lambda: first_runtime.id in orchestrator._expired_runtime_ids)
        assert runtime.stopped_sessions == []

        runtime.queue_turn_plan(TestTurnPlan())
        second_task = orchestrator.dispatch_inbound(make_message(seq=2))
        await wait_until(lambda: len(runtime.steered_turns) == 1)
        next(iter(runtime.active_streams)).release()
        first_turn, second_turn = await asyncio.gather(first_task, second_task)

        second_runtime = orchestrator.runtime_session(Thread("bcn-1"))
        assert first_turn is not None
        assert second_turn is not None
        assert second_runtime is not None
        assert second_runtime.id != first_runtime.id
        assert runtime.stopped_sessions.count(first_runtime) == 1
        assert [session.id for session in runtime.started_sessions] == [
            first_runtime.id,
            second_runtime.id,
        ]
    finally:
        if not first_task.done():
            first_task.cancel()
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_terminal_wait_accepts_confirmed_runtime_discard_after_turn() -> None:
    orchestrator, channel, runtime, _, _ = await make_node()

    async def command_script(commands: ICommandService, session_id: str) -> None:
        (checked,) = await commands.check(Thread(session_id))
        await commands.send(
            actor=Thread(session_id),
            command_id="terminal-wait",
            raw_target=checked.messages[0].target,
            body="terminal reply",
            created_at_ms=2,
        )

    message = make_message()
    runtime.queue_turn_plan(
        TestTurnPlan(command_script=command_script, block_until_release=True)
    )
    try:
        await channel.inject(message)
        await runtime.turn_started.wait()
        runtime_session = orchestrator.runtime_session(Thread(message.session_id))
        assert runtime_session is not None
        runtime.emit_expire(runtime_session.id)
        await wait_until(
            lambda: runtime_session.id in orchestrator._expired_runtime_ids
        )

        next(iter(runtime.active_streams)).release()
        outbound = await wait_for_turn_terminal(
            orchestrator=orchestrator,
            channel=channel,
            session_id=message.session_id,
            client_user_message_id=message.message_id,
            sent_after=0,
            timeout=1,
            expect_runtime_discarded=True,
        )

        assert [message.body for message in outbound] == ["terminal reply"]
        assert runtime.stopped_sessions == [runtime_session]
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
@pytest.mark.parametrize("context_first", (True, False))
async def test_context_and_timer_expiry_stop_the_runtime_once(
    context_first: bool,
) -> None:
    orchestrator, runtime, _, wheel = await make_idle_timeout_node(30)
    try:
        await orchestrator.handle_inbound(make_message(seq=1))
        runtime_session = orchestrator.runtime_session(Thread("bcn-1"))
        assert runtime_session is not None

        if context_first:
            runtime.emit_expire(runtime_session.id)
        else:
            await asyncio.sleep(0.06)
        await wait_until(lambda: orchestrator.runtime_session(Thread("bcn-1")) is None)

        if context_first:
            await asyncio.sleep(0.06)
        else:
            runtime.emit_expire(runtime_session.id)
            await asyncio.sleep(0)
        assert runtime.stopped_sessions == [runtime_session]
    finally:
        await orchestrator.stop(timeout=1)
        await wheel.close()


@pytest.mark.asyncio
async def test_runtime_idle_timeout() -> None:
    # a quiet inbound does not refresh a live idle deadline
    orchestrator, runtime, _, wheel = await make_idle_timeout_node(80)
    try:
        await orchestrator.handle_inbound(make_message(seq=1))
        runtime_session = orchestrator.runtime_session(Thread("bcn-1"))
        assert runtime_session is not None

        await asyncio.sleep(0.05)
        await orchestrator.handle_inbound(
            replace(make_message(seq=2), notifies_runtime=False)
        )
        async with asyncio.timeout(1):
            while orchestrator.runtime_session(Thread("bcn-1")) is not None:
                await asyncio.sleep(0.01)
        assert runtime.stopped_sessions == [runtime_session]
    finally:
        await orchestrator.stop(timeout=1)
        await wheel.close()

    # a notifying inbound reuses the current runtime session
    orchestrator, runtime, _, wheel = await make_idle_timeout_node(80)
    try:
        await orchestrator.handle_inbound(make_message(seq=1))
        runtime_session = orchestrator.runtime_session(Thread("bcn-1"))
        assert runtime_session is not None

        await asyncio.sleep(0.05)
        await orchestrator.handle_inbound(make_message(seq=2))
        await asyncio.sleep(0.05)

        assert orchestrator.runtime_session(Thread("bcn-1")) is runtime_session
        binding = orchestrator._runtime_timers.get(Thread("bcn-1"))
        assert binding is not None
        assert binding.timer.active
        assert len(runtime.started_sessions) == 1
    finally:
        await orchestrator.stop(timeout=1)
        await wheel.close()

    # background work suppresses the idle timeout
    orchestrator, runtime, _, wheel = await make_idle_timeout_node(30)
    runtime.background_job_present = True
    try:
        await orchestrator.handle_inbound(make_message(seq=1))
        runtime_session = orchestrator.runtime_session(Thread("bcn-1"))
        assert runtime_session is not None
        await asyncio.sleep(0.06)

        assert orchestrator.runtime_session(Thread("bcn-1")) is runtime_session
        assert orchestrator._runtime_timers == {}
        assert runtime.stopped_sessions == []

        runtime.emit_background_idle("stale-runtime")
        await asyncio.sleep(0)
        assert orchestrator._runtime_timers == {}
        runtime.emit_background_idle(runtime_session.id)
        await asyncio.sleep(0)
        assert orchestrator._runtime_timers == {}

        runtime.background_job_present = False
        runtime.emit_background_idle(runtime_session.id)
        runtime.emit_background_idle(runtime_session.id)
        async with asyncio.timeout(1):
            while not orchestrator._runtime_timers:
                await asyncio.sleep(0.01)
        binding = orchestrator._runtime_timers.get(Thread("bcn-1"))
        assert binding is not None
        assert binding.timer.active
        async with asyncio.timeout(1):
            while orchestrator.runtime_session(Thread("bcn-1")) is not None:
                await asyncio.sleep(0.01)
        assert runtime.stopped_sessions == [runtime_session]
    finally:
        await orchestrator.stop(timeout=1)
        await wheel.close()

    # idle expiry replaces the runtime on the next notification
    orchestrator, runtime, _, wheel = await make_idle_timeout_node(30)
    try:
        await orchestrator.handle_inbound(make_message(seq=1))
        first_runtime = orchestrator.runtime_session(Thread("bcn-1"))
        assert first_runtime is not None
        async with asyncio.timeout(1):
            while orchestrator.runtime_session(Thread("bcn-1")) is not None:
                await asyncio.sleep(0.01)

        await orchestrator.handle_inbound(make_message(seq=2))
        second_runtime = orchestrator.runtime_session(Thread("bcn-1"))

        assert second_runtime is not None
        assert second_runtime.id != first_runtime.id
        assert runtime.stopped_sessions[0] is first_runtime
    finally:
        await orchestrator.stop(timeout=1)
        await wheel.close()

    # expiry waits for an active turn to return to idle
    orchestrator, runtime, _, wheel = await make_idle_timeout_node(30)
    runtime.queue_turn_plan(TestTurnPlan(block_until_release=True))
    try:
        inbound_task = asyncio.create_task(
            orchestrator.handle_inbound(make_message(seq=1))
        )
        await runtime.turn_started.wait()
        runtime_session = orchestrator.runtime_session(Thread("bcn-1"))
        assert runtime_session is not None
        await asyncio.sleep(0.06)

        assert orchestrator.runtime_session(Thread("bcn-1")) is runtime_session
        assert orchestrator._runtime_timers == {}
        assert runtime.stopped_sessions == []

        stream = next(iter(runtime.active_streams))
        stream.release()
        await inbound_task
        assert orchestrator.runtime_session(Thread("bcn-1")) is runtime_session
        binding = orchestrator._runtime_timers.get(Thread("bcn-1"))
        assert binding is not None
        assert binding.timer.active
        async with asyncio.timeout(1):
            while orchestrator.runtime_session(Thread("bcn-1")) is not None:
                await asyncio.sleep(0.01)
        assert runtime.stopped_sessions == [runtime_session]
    finally:
        await orchestrator.stop(timeout=1)
        await wheel.close()

    # independent sessions expire without interfering
    orchestrator, runtime, _, wheel = await make_idle_timeout_node(50)
    try:
        await orchestrator.handle_inbound(make_message(session_id="bcn-a", seq=1))
        await asyncio.sleep(0.03)
        await orchestrator.handle_inbound(make_message(session_id="bcn-b", seq=1))
        async with asyncio.timeout(1):
            while orchestrator.runtime_session(Thread("bcn-a")) is not None:
                await asyncio.sleep(0.01)

        assert orchestrator.runtime_session(Thread("bcn-b")) is not None
        async with asyncio.timeout(1):
            while orchestrator.runtime_session(Thread("bcn-b")) is not None:
                await asyncio.sleep(0.01)
        assert {session.actor.id for session in runtime.stopped_sessions} == {
            "bcn-a",
            "bcn-b",
        }
    finally:
        await orchestrator.stop(timeout=1)
        await wheel.close()

    # replacing the runtime cancels the previous timer generation
    orchestrator, _, _, wheel = await make_idle_timeout_node(1_000)
    try:
        await orchestrator.handle_inbound(make_message(seq=1))
        first_runtime = orchestrator.runtime_session(Thread("bcn-1"))
        first_binding = orchestrator._runtime_timers[Thread("bcn-1")]
        assert first_runtime is not None
        await orchestrator._discard_runtime_session(first_runtime)

        await orchestrator.handle_inbound(make_message(seq=2))

        second_runtime = orchestrator.runtime_session(Thread("bcn-1"))
        second_binding = orchestrator._runtime_timers[Thread("bcn-1")]
        assert second_runtime is not None
        assert second_runtime.id != first_runtime.id
        assert first_binding.timer.active is False
        assert first_binding.watcher.done()
        assert second_binding.runtime_session_id == second_runtime.id
        assert second_binding.timer.active is True
        assert second_binding.timer.generation == 1
    finally:
        await orchestrator.stop(timeout=1)
        assert orchestrator._runtime_timers == {}
        await wheel.close()


@pytest.mark.asyncio
async def test_pre_start_failure_reconciles_and_retries_the_current_inbound() -> None:
    orchestrator, _, runtime, storage, _ = await make_node()
    try:
        first_turn = await orchestrator.handle_inbound(
            make_message(body="Please summarize the latest project update.")
        )
        assert first_turn is not None
        assert first_turn.state is RuntimeTurnState.COMPLETED

        runtime.queue_turn_plan(TestTurnPlan(pre_start_unavailable=True))
        follow_up_turn = await orchestrator.handle_inbound(
            make_message(
                seq=2,
                body="Could you also call out the remaining delivery risk?",
            )
        )

        assert follow_up_turn is not None
        assert follow_up_turn.state is RuntimeTurnState.COMPLETED
        assert orchestrator.session_runtime_state(Thread("bcn-1")) is State.IDLE
        assert len(runtime.reconciled_sessions) == 1
        assert runtime.reconciled_sessions[0].provider_thread_id is not None
        assert len(runtime.started_turns) == 2
        assert len(storage.runtime_attempts) == 2
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_repeated_pre_start_failure_stops_after_one_retry() -> None:
    orchestrator, channel, runtime, storage, _ = await make_node()
    try:
        first_turn = await orchestrator.handle_inbound(
            make_message(body="Please summarize the latest project update.")
        )
        assert first_turn is not None
        assert first_turn.state is RuntimeTurnState.COMPLETED

        runtime.queue_turn_plan(TestTurnPlan(pre_start_unavailable=True))
        runtime.queue_turn_plan(TestTurnPlan(pre_start_unavailable=True))
        follow_up_turn = await orchestrator.handle_inbound(
            make_message(
                seq=2,
                body="Could you also call out the remaining delivery risk?",
            )
        )

        assert follow_up_turn is not None
        assert follow_up_turn.state is RuntimeTurnState.FAILED
        assert orchestrator.session_runtime_state(Thread("bcn-1")) is None
        assert orchestrator.runtime_session(Thread("bcn-1")) is None
        assert len(runtime.reconciled_sessions) == 1
        assert len(runtime.started_turns) == 1
        assert len(storage.runtime_attempts) == 2
        assert _terminal_failures(channel) == 1
    finally:
        await orchestrator.stop(timeout=1)


class _NamedTestRuntime(TestRuntime):
    """A test runtime that reports the kind and variables it was built for."""

    def __init__(self, kind: str, environment_names: tuple[str, ...]) -> None:
        super().__init__()
        self._kind = kind
        self._environment_names = environment_names

    @property
    def name(self) -> str:
        return self._kind

    def environment_variable_names(self) -> tuple[str, ...]:
        return self._environment_names


class _MultiRuntimeRegistry(AdapterRegistry):
    """Serve one runtime factory per kind, recording the contexts it builds."""

    def __init__(self, channel: IChannel, kinds: Mapping[str, tuple[str, ...]]) -> None:
        self._channel = channel
        self._kinds = kinds
        self.contexts: list[RuntimeCommandContext] = []

    def load_agent(
        self,
        *,
        channel: str,
        runtimes: Sequence[str],
    ) -> AgentAdapterFactories:
        del channel
        missing = [kind for kind in runtimes if kind not in self._kinds]
        if missing:
            raise AssertionError(f"unexpected runtime kinds: {missing}")
        return AgentAdapterFactories(
            channel=StaticChannelBuilder(self._channel),
            runtimes={kind: self._factory(kind) for kind in dict.fromkeys(runtimes)},
        )

    def _factory(self, kind: str) -> Callable[[RuntimeCommandContext], IRuntime]:
        names = self._kinds[kind]

        def build(context: RuntimeCommandContext) -> IRuntime:
            self.contexts.append(context)
            return _NamedTestRuntime(kind, names)

        return build


@pytest.mark.asyncio
async def test_multi_runtime_agents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BCN_TEST_TOKEN_WORK", "work-token")
    monkeypatch.setenv("BCN_TEST_TOKEN_PERSONAL", "personal-token")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/run/agent.sock")
    monkeypatch.setenv("TEST_RUNTIME_HOME", "/home/test-runtime")
    monkeypatch.setenv("OTHER_RUNTIME_HOME", "/home/other-runtime")

    channel = TestChannel()
    registry = _MultiRuntimeRegistry(
        cast(IChannel, channel),
        {
            "test": ("TEST_RUNTIME_HOME",),
            "other": ("OTHER_RUNTIME_HOME",),
        },
    )
    node = NodeApplication(
        configuration=NodeConfiguration(
            version_check=False,
            storage="sqlite",
            audit="test",
            agents=(
                AgentConfiguration(
                    id=ACCEPTANCE_AGENT_ID,
                    name="Multi Runtime Agent",
                    channel=ChannelConfiguration(kind="test"),
                    runtimes=(
                        RuntimeConfiguration(
                            kind="test",
                            model="first-model",
                            env={
                                "TEST_TOKEN": "BCN_TEST_TOKEN_WORK",
                                "SSH_AUTH_SOCK": "SSH_AUTH_SOCK",
                            },
                        ),
                        RuntimeConfiguration(
                            kind="other",
                            model="second-model",
                            env={
                                "TEST_TOKEN": "BCN_TEST_TOKEN_PERSONAL",
                                "BCN_TEST_TOKEN_PERSONAL": ("BCN_TEST_TOKEN_PERSONAL"),
                            },
                        ),
                    ),
                ),
            ),
        ),
        shared_factories=SharedAdapterFactories(
            storage=lambda: cast(IStorage, SqliteDatabase()),
            audit=lambda: RecordingAudit(),
        ),
        registry=registry,
        endpoint_path=tmp_path / "multi-runtime.sock",
        timeout_budget=TimeoutBudget(
            startup_seconds=5,
            provider_call_seconds=5,
            command_seconds=5,
            shutdown_seconds=5,
        ),
    )
    await node.start()
    try:
        assert node.agent_startup_results[ACCEPTANCE_AGENT_ID].status == "started", (
            node.agent_startup_results[ACCEPTANCE_AGENT_ID]
        )
        application = node.agents[ACCEPTANCE_AGENT_ID]

        # case: one instance and one command context per runtime configuration
        assert [runtime.name for runtime in application.runtimes] == ["test", "other"]
        assert [context.runtime_options["model"] for context in registry.contexts] == [
            "first-model",
            "second-model",
        ]

        # case: health reports every runtime, not just the first one
        assert application.health_record()["runtimes"] == ("test", "other")
        assert node.agent_startup_results[ACCEPTANCE_AGENT_ID].as_health_record()[
            "runtimes"
        ] == ("test", "other")

        first = application._build_command_environment(
            "session-multi",
            "runtime-session-multi",
            runtime_index=0,
        )
        second = application._build_command_environment(
            "session-multi",
            "runtime-session-multi",
            runtime_index=1,
        )

        # case: the same env name reads a different source variable per runtime
        assert first["TEST_TOKEN"] == "work-token"
        assert second["TEST_TOKEN"] == "personal-token"

        # case: a renamed credential reaches the child only under the child name
        assert "BCN_TEST_TOKEN_WORK" not in first
        assert "BCN_TEST_TOKEN_PERSONAL" not in first
        assert "BCN_TEST_TOKEN_WORK" not in second

        # case: a source name is only stripped when nothing else asked for it
        assert second["BCN_TEST_TOKEN_PERSONAL"] == "personal-token"

        # case: a name that is the same on both sides is passed straight through
        assert first["SSH_AUTH_SOCK"] == "/run/agent.sock"
        assert "SSH_AUTH_SOCK" not in second

        # case: environment_variable_names() applies per runtime
        assert first["TEST_RUNTIME_HOME"] == "/home/test-runtime"
        assert "OTHER_RUNTIME_HOME" not in first
        assert second["OTHER_RUNTIME_HOME"] == "/home/other-runtime"
        assert "TEST_RUNTIME_HOME" not in second
    finally:
        await node.stop()


async def make_multi_runtime_node() -> tuple[
    SessionOrchestrator,
    tuple[_NamedTestRuntime, _NamedTestRuntime],
    TimerWheel,
    TestChannel,
    RecordingAudit,
]:
    channel = TestChannel()
    runtimes = (_NamedTestRuntime("first", ()), _NamedTestRuntime("second", ()))
    storage = MemoryStorage()
    audit = RecordingAudit()
    await storage.start(timeout=1)
    wheel = TimerWheel()
    await wheel.start()
    orchestrator = SessionOrchestrator(
        actors=Actors(agent_id="workspace-1", mode=Mode.SESSION),
        channel=channel,
        runtimes=runtimes,
        storage=storage.scope("workspace-1", "Test Agent"),
        audit=audit,
        timeout_budget=make_budget(),
        timer_wheel=wheel,
        workspace=Path.cwd,
        translator=_ENGLISH_TRANSLATOR,
        error_feedback_detail=unchanged_error_feedback_detail,
    )
    for runtime in runtimes:
        runtime.command_service = orchestrator.command_service
    await orchestrator.start(timeout=1)
    return orchestrator, runtimes, wheel, channel, audit


@pytest.mark.asyncio
async def test_multi_runtime_session_binding() -> None:
    # case: consecutive sessions take the runtimes in configuration order
    orchestrator, (first, second), wheel, _, _ = await make_multi_runtime_node()
    first.queue_turn_plan(TestTurnPlan(block_until_release=True))
    first_task = orchestrator.dispatch_inbound(make_message(session_id="bcn-a", seq=1))
    try:
        await first.turn_started.wait()
        session_a = orchestrator.runtime_session(Thread("bcn-a"))
        assert session_a is not None
        assert (session_a.runtime, session_a.runtime_index) == ("first", 0)

        # case: a steer for a live turn reaches the instance that started it
        first.queue_turn_plan(TestTurnPlan())
        second_task = orchestrator.dispatch_inbound(
            make_message(session_id="bcn-a", seq=2)
        )
        await wait_until(lambda: len(first.steered_turns) == 1)
        next(iter(first.active_streams)).release()
        await asyncio.gather(first_task, second_task)

        # only a ban moves selection on, so bcn-b needs the first one banned
        orchestrator._runtimes.record_failure(0)
        await orchestrator.handle_inbound(make_message(session_id="bcn-b", seq=1))
        session_b = orchestrator.runtime_session(Thread("bcn-b"))
        assert session_b is not None
        assert (session_b.runtime, session_b.runtime_index) == ("second", 1)

        # case: start and turn calls never cross over to the other instance
        assert [session.id for session in first.started_sessions] == [session_a.id]
        assert [session.id for session in second.started_sessions] == [session_b.id]
        assert {session.id for session, _, _ in first.started_turns} == {session_a.id}
        assert {session.id for session, _, _ in second.started_turns} == {session_b.id}
        assert {session.id for session, _, _ in first.steered_turns} == {session_a.id}
        assert second.steered_turns == []
    finally:
        await orchestrator.stop(timeout=1)
        await wheel.close()

    # case: shutdown stops each session through the instance that owns it
    assert [session.id for session in first.stopped_sessions] == [session_a.id]
    assert [session.id for session in second.stopped_sessions] == [session_b.id]


@pytest.mark.asyncio
async def test_multi_runtime_expiry_is_scoped_to_its_runtime() -> None:
    # case: an expire fans out only to the sessions of the runtime that sent it
    orchestrator, (first, second), wheel, _, _ = await make_multi_runtime_node()
    try:
        await orchestrator.handle_inbound(make_message(session_id="bcn-a", seq=1))
        orchestrator._runtimes.record_failure(0)
        await orchestrator.handle_inbound(make_message(session_id="bcn-b", seq=1))
        session_a = orchestrator.runtime_session(Thread("bcn-a"))
        session_b = orchestrator.runtime_session(Thread("bcn-b"))
        assert session_a is not None
        assert session_b is not None
        assert (session_a.runtime_index, session_b.runtime_index) == (0, 1)

        first.emit_expire(session_a.id)
        await wait_until(lambda: orchestrator.runtime_session(Thread("bcn-a")) is None)
        await wait_until(lambda: len(first.stopped_sessions) == 1)

        assert orchestrator.runtime_session(Thread("bcn-b")) is session_b
        assert [session.id for session in first.stopped_sessions] == [session_a.id]
        assert second.stopped_sessions == []
    finally:
        await orchestrator.stop(timeout=1)
        await wheel.close()


def test_runtime_pool_moves_on_to_the_next_runtime_when_one_is_banned() -> None:
    now_ms = [1_000]
    pool = Runtime(
        (
            _NamedTestRuntime("first", ()),
            _NamedTestRuntime("second", ()),
            _NamedTestRuntime("third", ()),
        ),
        clock=lambda: now_ms[0],
    )

    # case: configuration order is priority order, so a working runtime keeps
    # serving instead of handing over on every selection
    assert [pool.select() for _ in range(3)] == [0, 0, 0]

    # case: only a ban moves selection on, and it settles on the next runtime
    ban_until_ms = pool.record_failure(0)
    assert ban_until_ms == now_ms[0] + 3_600_000
    assert [pool.select() for _ in range(2)] == [1, 1]

    # case: a ban lapses on its own once the clock passes it
    now_ms[0] = ban_until_ms
    assert pool.select() == 0

    # case: with everything banned only the one banned longest ago is let through
    for index in range(3):
        pool.record_failure(index)
        now_ms[0] += 1
    assert pool.select() == 0
    assert pool.select() == 0

    # case: a failed probe moves the half-open slot on to the next runtime
    pool.record_failure(0)
    assert pool.select() == 1

    # case: a successful probe lifts the ban and reports what it lifted
    assert pool.record_success(1) is not None
    assert pool.record_success(1) is None
    assert pool.select() == 1


def test_single_runtime_pool_always_offers_its_only_runtime() -> None:
    pool = Runtime((_NamedTestRuntime("only", ()),), clock=lambda: 1_000)

    # case: the only runtime stays selectable after it is banned
    assert pool.select() == 0
    pool.record_failure(0)
    assert pool.select() == 0


def _terminal_failures(channel: TestChannel) -> int:
    return sum(
        1
        for event in channel.events
        if isinstance(event.payload, TurnFailed | TurnUnknown)
    )


def _pool_events(audit: RecordingAudit) -> list[tuple[str, object, object]]:
    return [
        (event.event_name, event.metadata["runtime_index"], event.metadata["runtime"])
        for event in audit.events
        if event.event_name.startswith("runtime.pool.")
    ]


@pytest.mark.asyncio
async def test_multi_runtime_hands_over_a_runtime_that_cannot_start_a_session() -> None:
    (
        orchestrator,
        (first, second),
        wheel,
        channel,
        audit,
    ) = await make_multi_runtime_node()
    try:
        # case: a runtime that cannot start a session is banned and handed over
        # rather than escaping the ban when its session is discarded
        first.queue_turn_plan(TestTurnPlan(pre_start_unavailable=True))
        first.queue_turn_plan(TestTurnPlan(pre_start_unavailable=True))
        result = await orchestrator.handle_inbound(
            make_message(session_id="bcn-a", seq=1)
        )

        assert result is not None
        assert result.state is RuntimeTurnState.COMPLETED
        assert channel.send_attempts == []
        assert len(second.started_turns) == 1
        assert ("runtime.pool.banned", 0, "first") in _pool_events(audit)

        # case: later sessions keep away from the banned runtime
        await orchestrator.handle_inbound(make_message(session_id="bcn-b", seq=1))
        session_b = orchestrator.runtime_session(Thread("bcn-b"))
        assert session_b is not None
        assert session_b.runtime_index == 1
    finally:
        await orchestrator.stop(timeout=1)
        await wheel.close()


@pytest.mark.asyncio
async def test_multi_runtime_failover_answers_before_reporting_an_error() -> None:
    (
        orchestrator,
        (first, second),
        wheel,
        channel,
        audit,
    ) = await make_multi_runtime_node()
    try:
        # case: a runtime that fails hands the turn to the next one instead of
        # answering the Channel with an error
        first.queue_turn_plan(
            TestTurnPlan(states=(RuntimeEventState.STARTED, RuntimeEventState.FAILED))
        )
        result = await orchestrator.handle_inbound(
            make_message(session_id="bcn-a", seq=1)
        )

        assert result is not None
        assert result.state is RuntimeTurnState.COMPLETED
        assert channel.send_attempts == []
        assert len(second.started_turns) == 1
        session_a = orchestrator.runtime_session(Thread("bcn-a"))
        assert session_a is not None
        assert session_a.runtime_index == 1
        assert ("runtime.pool.banned", 0, "first") in _pool_events(audit)
    finally:
        await orchestrator.stop(timeout=1)
        await wheel.close()


@pytest.mark.asyncio
async def test_multi_runtime_reports_once_every_runtime_has_failed() -> None:
    (
        orchestrator,
        (first, second),
        wheel,
        channel,
        audit,
    ) = await make_multi_runtime_node()
    try:
        # case: only the turn that exhausted every runtime reaches the channel
        first.queue_turn_plan(
            TestTurnPlan(states=(RuntimeEventState.STARTED, RuntimeEventState.FAILED))
        )
        second.queue_turn_plan(
            TestTurnPlan(states=(RuntimeEventState.STARTED, RuntimeEventState.UNKNOWN))
        )
        result = await orchestrator.handle_inbound(
            make_message(session_id="bcn-a", seq=1)
        )

        assert result is not None
        assert result.state is RuntimeTurnState.UNKNOWN
        assert _terminal_failures(channel) == 1
        terminal = next(
            event
            for event in channel.events
            if isinstance(event.payload, TurnFailed | TurnUnknown)
        )
        assert isinstance(terminal.payload, TurnUnknown)
        assert terminal.envelope.turn_id == result.turn_id

        # case: with every runtime banned the next turn probes the one banned
        # longest ago, and completing on it lifts that ban
        await orchestrator.handle_inbound(make_message(session_id="bcn-b", seq=1))
        session_b = orchestrator.runtime_session(Thread("bcn-b"))
        assert session_b is not None
        assert session_b.runtime_index == 0
        events = _pool_events(audit)
        assert ("runtime.pool.banned", 0, "first") in events
        assert ("runtime.pool.banned", 1, "second") in events
        assert ("runtime.pool.released", 0, "first") in events
    finally:
        await orchestrator.stop(timeout=1)
        await wheel.close()


@pytest.mark.asyncio
async def test_upgrade_is_offered_once_per_release() -> None:
    offer = ["0.2.0"]
    orchestrator, channel, runtime, _, audit = await make_node(
        upgrade_notice=lambda: (offer[0], "0.1.31"),
    )
    try:
        first = make_message(seq=1)
        await channel.inject(first)
        await wait_until(
            lambda: any(
                event.event_name == "runtime.turn.completed"
                and event.correlation.turn_id == "turn-message-bcn-1-1"
                for event in audit.events
            )
        )
        second = make_message(seq=2)
        await channel.inject(second)
        await wait_until(
            lambda: any(
                event.event_name == "runtime.turn.completed"
                and event.correlation.turn_id == "turn-message-bcn-1-2"
                for event in audit.events
            )
        )

        assert len(runtime.started_turns) == 2

        # case: the conversation hears of a release once
        assert (
            "Upgrade available: bazaar-compute-node 0.2.0"
            in (runtime.started_turns[0][2])
        )

        # case: and is not told the same thing again on its next turn
        assert "Upgrade available" not in runtime.started_turns[1][2]

        offer[0] = "0.3.0"
        third = make_message(seq=3)
        await channel.inject(third)
        await wait_until(
            lambda: any(
                event.event_name == "runtime.turn.completed"
                and event.correlation.turn_id == "turn-message-bcn-1-3"
                for event in audit.events
            )
        )

        # case: a release it has not heard of is worth saying, however long the
        # conversation has been going
        assert (
            "Upgrade available: bazaar-compute-node 0.3.0"
            in (runtime.started_turns[2][2])
        )
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_a_backlog_wider_than_the_notice_window_reports_its_newest() -> None:
    orchestrator, channel, runtime, _, audit = await make_node()
    backlog = _NOTICE_WINDOW + 20
    try:
        for seq in range(1, backlog + 1):
            await orchestrator._record_inbound(
                make_message(seq=seq, message_id=f"old-{seq:04d}")
            )
        await channel.inject(make_message(seq=backlog + 1, message_id="new-0001"))
        await wait_until(
            lambda: any(
                event.event_name == "runtime.turn.completed"
                and event.correlation.turn_id == "turn-new-0001"
                for event in audit.events
            )
        )

        notice = runtime.started_turns[0][2]

        # case: the count is the whole backlog, not the window that carries it
        assert f"Inbox update: {backlog + 1} unread messages total" in notice

        # case: and the message that woke the turn is the one it reports
        assert "latest msg=new-0001" in notice
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_no_upgrade_leaves_the_opening_turn_untouched() -> None:
    orchestrator, channel, runtime, _, audit = await make_node()
    try:
        await channel.inject(make_message(seq=1))
        await wait_until(
            lambda: any(
                event.event_name == "runtime.turn.completed"
                and event.correlation.turn_id == "turn-message-bcn-1-1"
                for event in audit.events
            )
        )

        # case: a node that knows of no newer release says nothing about upgrades
        assert "Upgrade available" not in runtime.started_turns[0][2]
    finally:
        await orchestrator.stop(timeout=1)
