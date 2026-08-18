from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Protocol, cast
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

from bazaar_compute_node.app.application import NodeApplication
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
from bazaar_compute_node.contrib.sqlite import SqliteDatabase
from bazaar_compute_node.core.audit import AuditEvent
from bazaar_compute_node.core.channel import (
    ChannelDeliveryReceipt,
    ChannelSendRequest,
    IChannel,
)
from bazaar_compute_node.core.command import ICommandService
from bazaar_compute_node.core.lifecycle import TimeoutBudget
from bazaar_compute_node.core.models import (
    ApprovalRequest,
    ChannelTargetKind,
    InboundMessage,
    OutboundDeliveryState,
    ReminderOccurrence,
    RuntimeEvent,
    RuntimeEventState,
    RuntimeTurn,
    RuntimeTurnState,
    SenderIdentity,
    SessionRuntimeObservation,
    SessionRuntimeObservationSource,
    SessionRuntimeSignal,
    SessionRuntimeState,
)
from bazaar_compute_node.core.orchestration import SessionOrchestrator
from bazaar_compute_node.core.orchestration.session import (
    _RuntimeNotification,
)
from bazaar_compute_node.core.outcomes import ProviderCallResult, ProviderCallStatus
from bazaar_compute_node.core.runtime import (
    IRuntime,
    RuntimeCommandContext,
    RuntimeSessionReconciliation,
)
from bazaar_compute_node.core.storage import IStorage
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
        runtime: str,
        storage: str,
    ) -> AgentAdapterFactories:
        del channel, runtime, storage
        return AgentAdapterFactories(
            channel=StaticChannelBuilder(self._channel),
            runtime=self._runtime,
        )


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


def make_message(
    *,
    session_id: str = "bcn-1",
    seq: int = 1,
    message_id: str | None = None,
    body: str | None = None,
) -> InboundMessage:
    channel_session_id = f"channel-{session_id}"
    return InboundMessage(
        seq=seq,
        message_id=message_id or f"message-{session_id}-{seq}",
        session_id=session_id,
        channel_session_id=channel_session_id,
        channel="test",
        provider_thread_id=f"thread-{session_id}",
        provider_message_id=f"provider-{session_id}-{seq}",
        received_at_ms=seq,
        sender=SenderIdentity(id="sender-id", name="Sender"),
        message_type="text",
        canonical_target=f"#test:{session_id}",
        body=body if body is not None else f"inbound-{seq}",
    )


def make_budget() -> TimeoutBudget:
    return TimeoutBudget(
        startup_seconds=1,
        provider_call_seconds=1,
        command_seconds=1,
        shutdown_seconds=1,
    )


async def make_node(
    *,
    workspace: Callable[[], Path] = Path.cwd,
    translator: Translator = _ENGLISH_TRANSLATOR,
    error_feedback_detail: Callable[[str, str], str] = unchanged_error_feedback_detail,
    channel: TestChannel | None = None,
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
        agent_id="workspace-1",
        channel=channel,
        runtime=runtime,
        storage=storage.scope("workspace-1", "Test Agent"),
        audit=audit,
        timeout_budget=make_budget(),
        timer_wheel=TimerWheel(),
        workspace=workspace,
        translator=translator,
        error_feedback_detail=error_feedback_detail,
    )
    runtime.command_service = orchestrator.command_service
    await orchestrator.start(timeout=1)
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
        agent_id="workspace-1",
        channel=channel,
        runtime=runtime,
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
async def test_orchestrator_uses_agent_scoped_storage_before_runtime() -> None:
    channel = TestChannel()
    runtime = TestRuntime()
    storage = MemoryStorage()
    audit = RecordingAudit()
    await storage.start(timeout=1)

    orchestrator = SessionOrchestrator(
        agent_id="workspace-1",
        channel=channel,
        runtime=runtime,
        storage=storage.scope("workspace-1", "Test Agent"),
        audit=audit,
        timeout_budget=make_budget(),
        timer_wheel=TimerWheel(),
        workspace=Path.cwd,
        translator=_ENGLISH_TRANSLATOR,
        error_feedback_detail=unchanged_error_feedback_detail,
    )
    await orchestrator.start(timeout=1)
    try:
        assert runtime.started
        assert channel.started
    finally:
        await orchestrator.stop(timeout=1)


async def wait_until(predicate: object) -> None:
    if not callable(predicate):
        raise TypeError("predicate must be callable")
    for _ in range(200):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition was not reached")


class _AcceptanceChannel(Protocol):
    sent_messages: list[ChannelSendRequest]

    async def inject(self, message: InboundMessage) -> None: ...


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
) -> tuple[InboundMessage, ...]:
    async with asyncio.timeout(180):
        while True:
            async with storage.transaction() as transaction:
                messages = await transaction.list_inbound_messages(session_id)
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
                storage="sqlite",
                audit="test",
                agents=(
                    AgentConfiguration(
                        id=ACCEPTANCE_AGENT_ID,
                        name="Test Agent",
                        channel=ChannelConfiguration(kind="test"),
                        runtime=RuntimeConfiguration(kind="test"),
                    ),
                ),
            ),
            shared_factories=SharedAdapterFactories(
                storage=lambda storage=storage: storage,
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
            async with storage_scope.transaction() as transaction:
                cursor = await transaction.get_consumer_cursor(scoped_session_id)
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

        assert storage.inbound_messages["bcn-1"] == [message]
        assert orchestrator.session_runtime_state("bcn-1") is SessionRuntimeState.IDLE
        assert runtime.started_turns
        assert any(
            event.correlation.bcn_session_id == "bcn-1"
            and event.correlation.turn_id == "turn-message-bcn-1-1"
            for event in audit.events
        )
        assert (
            await orchestrator.command_service.unfollow("bcn-1", target="#test:bcn-1")
            is False
        )
        assert storage.channel_sessions["channel-bcn-1"].following is True
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_stream_events_bypass_durable_storage_and_audit() -> None:
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
            item for item in channel.events if isinstance(item, RuntimeEvent)
        ]
        assert [event.state for event in runtime_events] == [
            RuntimeEventState.STARTED,
            RuntimeEventState.COMPLETED,
        ]
        assert {event.session_id for event in channel.stream_events} == {"bcn-1"}
        assert channel.stream_events[0].content == "delta-1"
        assert channel.stream_events[-1].content == "delta-20000"
        assert not any(
            event.event_name == "reasoning-summary-delta" for event in audit.events
        )
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_stream_event_channel_failure_does_not_fail_turn() -> None:
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
        assert orchestrator.session_runtime_state("bcn-1") is SessionRuntimeState.IDLE
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_stream_event_for_another_session_is_discarded() -> None:
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
        checked = await commands.check(session_id)
        if not checked.messages:
            raise AssertionError("command did not observe the inbound message")
        history = await commands.read(
            session_id,
            target=checked.messages[0].canonical_target,
        )
        if not history.messages:
            raise AssertionError("history command did not observe the inbound message")
        outbound = await commands.send(
            session_id=session_id,
            command_id="command-1",
            target=checked.messages[0].canonical_target,
            body="runtime-generated reply",
            created_at_ms=2,
        )
        if outbound.state is not OutboundDeliveryState.SENT:
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
        assert storage.outbound_messages
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
async def test_check_drains_read_preserves_cursor_and_snapshot() -> None:
    orchestrator, channel, _, storage, _ = await make_node()
    try:
        await channel.inject(make_message(seq=1))
        await wait_until(lambda: len(storage.inbound_messages.get("bcn-1", [])) == 1)

        history = await orchestrator.command_service.read(
            "bcn-1",
            target="#test:bcn-1",
            limit=1,
        )
        assert [message.seq for message in history.messages] == [1]
        assert history.snapshot_seq == 1
        assert history.first_seq == 1
        assert history.last_seq == 1
        assert storage.cursors["bcn-1"].delivered_through_seq == 0
        assert storage.cursors["bcn-1"].inbox_snapshot_seq == 1

        checked = await orchestrator.command_service.check("bcn-1")
        assert [message.seq for message in checked.messages] == [1]
        assert checked.snapshot_seq == 1
        assert checked.delivered_through_seq == 1
        assert storage.cursors["bcn-1"].delivered_through_seq == 1

        await channel.inject(make_message(seq=2))
        await wait_until(lambda: len(storage.inbound_messages["bcn-1"]) == 2)
        around = await orchestrator.command_service.read(
            "bcn-1",
            target="#test:bcn-1",
            around_message_id=storage.inbound_messages["bcn-1"][1].message_id,
            limit=1,
        )
        assert [message.seq for message in around.messages] == [2]
        assert storage.cursors["bcn-1"].delivered_through_seq == 1
        assert storage.cursors["bcn-1"].inbox_snapshot_seq == 2
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_approval_is_routed_to_the_current_channel_session() -> None:
    orchestrator, channel, runtime, _, audit = await make_node()
    try:
        first_turn = await orchestrator.handle_inbound(make_message(seq=1))
        assert first_turn is not None
        runtime_session = orchestrator.runtime_session("bcn-1")
        assert runtime_session is not None
        request = ApprovalRequest(
            request_id="approval-1",
            session_id="bcn-1",
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
        assert any(event.event_name == "approval.decided" for event in audit.events)
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_fresh_check_rejects_stale_send_before_channel_call() -> None:
    orchestrator, channel, _, storage, _ = await make_node()
    try:
        await channel.inject(make_message(seq=1))
        await wait_until(lambda: len(storage.inbound_messages.get("bcn-1", [])) == 1)

        rejected_without_snapshot = await orchestrator.command_service.send(
            session_id="bcn-1",
            command_id="command-before-check",
            target="#test:bcn-1",
            body="reply",
            created_at_ms=2,
        )
        assert rejected_without_snapshot.state is OutboundDeliveryState.REJECTED
        assert not channel.send_attempts

        checked = await orchestrator.command_service.check("bcn-1")
        assert checked.messages
        delivered = await orchestrator.command_service.send(
            session_id="bcn-1",
            command_id="command-after-check",
            target="#test:bcn-1",
            body="reply",
            created_at_ms=3,
            reply_to_message_id=storage.inbound_messages["bcn-1"][0].message_id,
        )
        assert delivered.state is OutboundDeliveryState.SENT
        assert channel.send_requests[0].provider_reply_to_message_id == (
            storage.inbound_messages["bcn-1"][0].provider_message_id
        )
        assert len(channel.send_attempts) == 1

        await channel.inject(make_message(seq=2))
        await wait_until(lambda: len(storage.inbound_messages["bcn-1"]) == 2)
        stale = await orchestrator.command_service.send(
            session_id="bcn-1",
            command_id="command-stale",
            target="#test:bcn-1",
            body="reply",
            created_at_ms=4,
        )
        assert stale.state is OutboundDeliveryState.REJECTED
        assert len(channel.send_attempts) == 1
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
        await wait_until(lambda: len(storage.inbound_messages.get("bcn-1", [])) == 1)
        await orchestrator.command_service.check("bcn-1")

        delivered = await orchestrator.command_service.send(
            session_id="bcn-1",
            command_id="command-with-attachments",
            target="#test:bcn-1",
            body="Attached reports.",
            created_at_ms=2,
            attachment_paths=(str(first), str(second)),
        )

        assert delivered.state is OutboundDeliveryState.SENT
        assert channel.send_requests[0].attachments == delivered.attachments
        assert [attachment.relative_path for attachment in delivered.attachments] == [
            "first.txt",
            "second.json",
        ]
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_send_validates_target_and_preserves_provider_delivery_states() -> None:
    orchestrator, channel, _, storage, audit = await make_node()
    try:
        await channel.inject(make_message(seq=1))
        await wait_until(lambda: len(storage.inbound_messages.get("bcn-1", [])) == 1)

        invalid_target = await orchestrator.command_service.send(
            session_id="bcn-1",
            command_id="command-invalid-target",
            target="#test:missing",
            body="reply",
            created_at_ms=2,
        )
        assert invalid_target.state is OutboundDeliveryState.REJECTED
        assert invalid_target.error_kind == "target_not_replyable"
        assert invalid_target.draft_saved_at_ms is not None
        assert not channel.send_attempts
        assert any(
            event.event_name == "bcc.send.target.failed" for event in audit.events
        )

        await orchestrator.command_service.check("bcn-1")
        empty_body = await orchestrator.command_service.send(
            session_id="bcn-1",
            command_id="command-empty-body",
            target="#test:bcn-1",
            body=" \t",
            created_at_ms=3,
        )
        assert empty_body.state is OutboundDeliveryState.REJECTED
        assert empty_body.error_kind == "empty_body"
        assert empty_body.draft_saved_at_ms is None
        assert all(
            message.command_id != "command-empty-body"
            for message in storage.outbound_messages.values()
        )
        assert not channel.send_attempts
        assert any(
            event.event_name == "bcc.send.empty_body.failed" for event in audit.events
        )

        channel.queue_send_result(
            ProviderCallResult(
                status=ProviderCallStatus.QUEUED,
                value=ChannelDeliveryReceipt(provider_receipt_ref="queue-1"),
            )
        )
        queued = await orchestrator.command_service.send(
            session_id="bcn-1",
            command_id="command-queued",
            target="#test:bcn-1",
            body="queued reply",
            created_at_ms=4,
        )
        assert queued.state is OutboundDeliveryState.QUEUED
        assert queued.provider_receipt_ref == "queue-1"
        assert channel.queued_messages == [channel.send_attempts[0]]

        channel.queue_send_result(
            ProviderCallResult(
                status=ProviderCallStatus.UNKNOWN,
                error_kind="transport_eof",
                error_message="delivery outcome is unknown",
                receipt={"provider_receipt_ref": "attempted-send-1"},
            )
        )
        unknown = await orchestrator.command_service.send(
            session_id="bcn-1",
            command_id="command-unknown",
            target="#test:bcn-1",
            body="unknown reply",
            created_at_ms=5,
        )
        assert unknown.state is OutboundDeliveryState.UNKNOWN
        assert unknown.provider_receipt_ref == "attempted-send-1"
        assert unknown.next_action == "reconcile channel delivery before retrying"

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
            session_id="bcn-1",
            command_id="command-partial",
            target="#test:bcn-1",
            body="partial reply",
            created_at_ms=6,
        )
        assert partial.state is OutboundDeliveryState.PARTIAL
        assert partial.provider_receipt_ref == "batch-1"
        assert partial.next_action == "do not retry the complete message automatically"
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
            session_id="bcn-1",
            command_id="command-failed",
            target="#test:bcn-1",
            body="failed reply",
            created_at_ms=7,
        )
        assert failed.state is OutboundDeliveryState.FAILED
        assert failed.provider_receipt_ref == "attempted-send-2"
        assert len(channel.send_attempts) == 4
        assert not channel.sent_messages
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
    assert orchestrator.session_runtime_state("bcn-1") is SessionRuntimeState.WORKING

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
    assert orchestrator.session_runtime_state("bcn-1") is None


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
            lambda: storage.inbound_messages.get("bcn-1") == [first, second]
        )
        await wait_until(lambda: len(runtime.steered_turns) == 1)
        assert len(runtime.started_turns) == 1
        steered_session, steered_turn, steer_input = runtime.steered_turns[0]
        assert steered_session.bcn_session_id == "bcn-1"
        assert steered_session == orchestrator.runtime_session("bcn-1")
        assert steered_turn.turn_id == "turn-message-bcn-1-1"
        assert steer_input == (
            "[inbox notice session=bcn-1]\n"
            "Inbox update: 2 unread message(s). "
            "Use the message command to read them."
        )
        second_body = second.body
        assert second_body is not None
        assert second_body not in steer_input
        second_sender = second.sender
        assert second_sender is not None
        assert second_sender.display_name not in steer_input

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
        assert "session=bcn-1" in runtime.started_turns[1][2]
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_session_runtime_observation_api_serializes_duplicate_runtime_and_channel_observations() -> (
    None
):
    orchestrator, channel, _, storage, _ = await make_node()
    try:
        await channel.inject(make_message())
        await wait_until(
            lambda: (
                storage.bcn_sessions.get("bcn-1") is not None
                and orchestrator.session_runtime_state("bcn-1")
                is SessionRuntimeState.IDLE
            )
        )

        started_observation = SessionRuntimeObservation(
            source=SessionRuntimeObservationSource.CHANNEL,
            signal=SessionRuntimeSignal.TURN_STARTED,
            observed_at_ms=storage.bcn_sessions["bcn-1"].updated_at_ms,
        )
        started = await asyncio.gather(
            orchestrator.observe_runtime("bcn-1", started_observation),
            orchestrator.observe_runtime("bcn-1", started_observation),
        )
        assert set(started) == {SessionRuntimeState.WORKING}

        completed_observation = SessionRuntimeObservation(
            source=SessionRuntimeObservationSource.RUNTIME,
            signal=SessionRuntimeSignal.TURN_COMPLETED,
            observed_at_ms=storage.bcn_sessions["bcn-1"].updated_at_ms,
        )
        completed = await asyncio.gather(
            orchestrator.observe_runtime("bcn-1", completed_observation),
            orchestrator.observe_runtime("bcn-1", completed_observation),
        )
        assert set(completed) == {SessionRuntimeState.IDLE}

        for signal, expected in (
            (
                SessionRuntimeSignal.COMPACTION_STARTED,
                SessionRuntimeState.COMPACTION_STARTING,
            ),
            (
                SessionRuntimeSignal.COMPACTION_IN_PROGRESS,
                SessionRuntimeState.COMPACTING,
            ),
            (
                SessionRuntimeSignal.COMPACTION_COMPLETED,
                SessionRuntimeState.COMPACTION_COMPLETED,
            ),
            (SessionRuntimeSignal.WORKING_OBSERVED, SessionRuntimeState.WORKING),
        ):
            current = storage.bcn_sessions["bcn-1"]
            updated = await orchestrator.observe_runtime(
                "bcn-1",
                SessionRuntimeObservation(
                    source=SessionRuntimeObservationSource.RUNTIME,
                    signal=signal,
                    observed_at_ms=current.updated_at_ms,
                ),
            )
            assert updated is expected
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
        assert set(storage.inbound_messages) == {"bcn-a", "bcn-b"}
        assert {
            event.correlation.bcn_session_id
            for event in audit.events
            if event.correlation.turn_id is not None
        } == {"bcn-a", "bcn-b"}
        assert {session.bcn_session_id for session in runtime.started_sessions} == {
            "bcn-a",
            "bcn-b",
        }
        first_runtime = orchestrator.runtime_session("bcn-a")
        second_runtime = orchestrator.runtime_session("bcn-b")
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
        assert storage.inbound_messages["bcn-a"] == [
            replace(first, notifies_runtime=True)
        ]

        other_conversation = replace(
            make_message(session_id="bcn-b"),
            provider_message_id=first.provider_message_id,
        )
        other_turn = await orchestrator.handle_inbound(other_conversation)
        assert other_turn is not None
        assert len(storage.inbound_messages["bcn-b"]) == 1
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
        assert orchestrator.runtime_session("bcn-1") is None
        assert runtime.started_sessions == []
        assert (await orchestrator.command_service.check("bcn-1")).messages == ()
        history = await orchestrator.command_service.read("bcn-1", target="#test:bcn-1")
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

        assert (
            await orchestrator.command_service.unfollow("bcn-1", target="#test:bcn-1")
            is True
        )
        assert storage.channel_sessions["channel-bcn-1"].following is False
        after_unfollow = replace(
            make_message(seq=4),
            target_kind=ChannelTargetKind.GROUP,
            mentions_agent=False,
        )
        assert await orchestrator.handle_inbound(after_unfollow) is None
        assert storage.inbound_messages["bcn-1"][-1].notifies_runtime is False

        assert await orchestrator.handle_inbound(quiet) is None
        assert len(storage.inbound_messages["bcn-1"]) == 4
        assert storage.inbound_messages["bcn-1"][0].notifies_runtime is False
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
        assert storage.outbound_messages == {}
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
async def test_feedback_reporter_exception_preserves_original_runtime_turn() -> None:
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


@pytest.mark.asyncio
async def test_batched_runtime_notifications_send_one_error_feedback() -> None:
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
    runtime_queue = orchestrator._runtime_queue_for_session("bcn-1")
    runtime_queue.put_nowait(
        _RuntimeNotification(
            first_message,
            first_context,
            first_completion,
            1,
        )
    )
    runtime_queue.put_nowait(
        _RuntimeNotification(
            second_message,
            second_context,
            second_completion,
            2,
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


@pytest.mark.asyncio
async def test_reminder_error_feedback_replies_to_anchor_message() -> None:
    orchestrator, channel, runtime, storage, _ = await make_node()
    anchor = make_message(seq=1, message_id=str(uuid7()))
    try:
        initial_turn = await orchestrator.handle_inbound(anchor)
        assert initial_turn is not None
        canonical_anchor = storage.inbound_messages["bcn-1"][0]
        occurrence_id = str(uuid7())
        storage.reminder_occurrences[occurrence_id] = ReminderOccurrence(
            occurrence_id=occurrence_id,
            reminder_id=str(uuid7()),
            owner_session_id="bcn-1",
            occurrence_no=1,
            anchor_message_id=canonical_anchor.message_id,
            scheduled_for_ms=2,
            fired_at_ms=2,
            next_fire_at_ms=None,
            overdue=False,
            read_at_ms=None,
            created_at_ms=2,
        )
        runtime.queue_turn_plan(
            TestTurnPlan(states=(RuntimeEventState.STARTED, RuntimeEventState.FAILED))
        )

        await orchestrator.publish_reminder_wake("bcn-1")
        await wait_until(lambda: len(channel.send_attempts) == 1)

        request = channel.send_attempts[0]
        assert request.session_id == canonical_anchor.session_id
        assert request.target_kind is canonical_anchor.target_kind
        assert request.provider_thread_id == canonical_anchor.provider_thread_id
        assert request.provider_reply_to_message_id == (
            canonical_anchor.provider_message_id
        )
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_inbound_failure_rolls_back_new_session_state() -> None:
    orchestrator, _, _, storage, _ = await make_node()
    try:
        invalid = replace(
            make_message(session_id="invalid", seq=2),
            target_kind=ChannelTargetKind.GROUP,
            mentions_agent=False,
        )
        with pytest.raises(ValueError, match="inbound sequence must be contiguous"):
            await orchestrator.handle_inbound(invalid)

        assert "channel-invalid" not in storage.channel_sessions
        assert "invalid" not in storage.bcn_sessions
        assert "invalid" not in storage.cursors
        assert "invalid" not in storage.inbound_messages
        assert orchestrator.runtime_session("invalid") is None
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_runtime_start_failure_replaces_session_for_current_inbound() -> None:
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
        assert orchestrator.session_runtime_state("bcn-1") is SessionRuntimeState.IDLE
        current_runtime = orchestrator.runtime_session("bcn-1")
        assert current_runtime is not None
        assert len(runtime.started_sessions) == 2
        assert runtime.started_sessions[0].id != current_runtime.id
        assert runtime.started_sessions[1].id == current_runtime.id
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
        assert orchestrator.runtime_session("bcn-1") is None
        assert orchestrator.session_runtime_state("bcn-1") is None
        assert len(runtime.reconciled_sessions) == 1
        first_runtime = runtime.reconciled_sessions[0]

        second_turn = await orchestrator.handle_inbound(make_message(seq=2))

        assert second_turn is not None
        assert second_turn.state is RuntimeTurnState.COMPLETED
        current_runtime = orchestrator.runtime_session("bcn-1")
        assert current_runtime is not None
        assert current_runtime.id != first_runtime.id
        assert runtime.reconciled_sessions == [first_runtime]
        assert runtime.started_sessions[-1].id == current_runtime.id
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_unknown_turn_reconciles_immediately() -> None:
    orchestrator, _, runtime, _, _ = await make_node()
    try:
        runtime.queue_turn_plan(
            TestTurnPlan(states=(RuntimeEventState.STARTED, RuntimeEventState.UNKNOWN))
        )
        turn = await orchestrator.handle_inbound(make_message(seq=1))

        assert turn is not None
        assert turn.state is RuntimeTurnState.UNKNOWN
        current_runtime = orchestrator.runtime_session("bcn-1")
        assert current_runtime is not None
        assert orchestrator.session_runtime_state("bcn-1") is SessionRuntimeState.IDLE
        assert runtime.reconciled_sessions == [current_runtime]
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_confirmed_failed_reconciliation_stops_live_session() -> None:
    orchestrator, _, runtime, _, _ = await make_node()
    try:
        first_turn = await orchestrator.handle_inbound(make_message(seq=1))
        assert first_turn is not None
        current_runtime = orchestrator.runtime_session("bcn-1")
        assert current_runtime is not None
        runtime.queue_reconcile_result(
            ProviderCallResult(
                status=ProviderCallStatus.CONFIRMED,
                value=RuntimeSessionReconciliation(
                    session=current_runtime,
                    state=SessionRuntimeState.FAILED,
                ),
            )
        )
        runtime.queue_turn_plan(
            TestTurnPlan(states=(RuntimeEventState.STARTED, RuntimeEventState.UNKNOWN))
        )

        second_turn = await orchestrator.handle_inbound(make_message(seq=2))

        assert second_turn is not None
        assert second_turn.state is RuntimeTurnState.UNKNOWN
        assert runtime.stopped_sessions[-1] == current_runtime
        assert orchestrator.runtime_session("bcn-1") is None
        assert orchestrator.session_runtime_state("bcn-1") is None
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_unknown_turn_reconciliation_restores_working_turn_and_steers() -> None:
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
        current_runtime = orchestrator.runtime_session("bcn-1")
        assert current_runtime is not None
        assert (
            orchestrator.session_runtime_state("bcn-1") is SessionRuntimeState.WORKING
        )

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
        assert orchestrator.runtime_session("bcn-1") == current_runtime
        assert orchestrator.session_runtime_state("bcn-1") is SessionRuntimeState.IDLE
        assert runtime.reconciled_sessions == [current_runtime]
        assert channel.send_attempts == []
    finally:
        if not first_task.done():
            first_task.cancel()
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_confirmed_stop_replaces_runtime_session_on_next_inbound() -> None:
    orchestrator, _, runtime, _, _ = await make_node()
    try:
        first_turn = await orchestrator.handle_inbound(make_message(seq=1))
        assert first_turn is not None
        first_runtime = orchestrator.runtime_session("bcn-1")
        assert first_runtime is not None

        await orchestrator._stop_runtime_session(first_runtime, timeout=1)

        assert orchestrator.runtime_session("bcn-1") is None
        assert orchestrator.session_runtime_state("bcn-1") is None
        second_turn = await orchestrator.handle_inbound(make_message(seq=2))
        assert second_turn is not None
        second_runtime = orchestrator.runtime_session("bcn-1")
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
        first_runtime = orchestrator.runtime_session("bcn-1")
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

        assert orchestrator.runtime_session("bcn-1") is None
        assert orchestrator.session_runtime_state("bcn-1") is None
        second_turn = await orchestrator.handle_inbound(make_message(seq=2))
        assert second_turn is not None
        replacement = orchestrator.runtime_session("bcn-1")
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
        first_runtime = first.runtime_session("bcn-1")
        assert first_runtime is not None
        first_runtime_id = first_runtime.id
    finally:
        await first.stop(timeout=1)

    channel = TestChannel()
    runtime = TestRuntime()
    second = SessionOrchestrator(
        agent_id="workspace-1",
        channel=channel,
        runtime=runtime,
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
        second_runtime = second.runtime_session("bcn-1")
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
        assert orchestrator.runtime_session("bcn-1") is None
        assert orchestrator.session_runtime_state("bcn-1") is None
        async with storage.transaction() as transaction:
            assert await transaction.get_consumer_cursor("bcn-1") is None
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_context_expire_fans_out_once_to_all_live_sessions() -> None:
    orchestrator, _, runtime, _, _ = await make_node()
    try:
        await asyncio.gather(
            orchestrator.handle_inbound(make_message(session_id="bcn-a", seq=1)),
            orchestrator.handle_inbound(make_message(session_id="bcn-b", seq=1)),
        )
        first_a = orchestrator.runtime_session("bcn-a")
        first_b = orchestrator.runtime_session("bcn-b")
        assert first_a is not None
        assert first_b is not None

        runtime.emit_expire(first_a.id)
        runtime.emit_expire(first_a.id)
        await wait_until(
            lambda: (
                orchestrator.runtime_session("bcn-a") is None
                and orchestrator.runtime_session("bcn-b") is None
            )
        )

        assert {session.id for session in runtime.stopped_sessions} == {
            first_a.id,
            first_b.id,
        }
        assert len(runtime.stopped_sessions) == 2
        assert len(runtime.started_sessions) == 2

        await orchestrator.handle_inbound(make_message(session_id="bcn-a", seq=2))
        second_a = orchestrator.runtime_session("bcn-a")
        assert second_a is not None
        assert second_a.id != first_a.id
        runtime.emit_expire(second_a.id)
        await wait_until(lambda: orchestrator.runtime_session("bcn-a") is None)

        assert runtime.stopped_sessions.count(second_a) == 1
        assert len(runtime.started_sessions) == 3
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_context_expire_waits_for_active_turn_then_precedes_pending_inbound() -> (
    None
):
    orchestrator, _, runtime, _, _ = await make_node()
    runtime.queue_turn_plan(TestTurnPlan(block_until_release=True))
    first_task = orchestrator.dispatch_inbound(make_message(seq=1))
    try:
        await runtime.turn_started.wait()
        first_runtime = orchestrator.runtime_session("bcn-1")
        assert first_runtime is not None

        runtime.emit_expire(first_runtime.id)
        await wait_until(lambda: first_runtime.id in orchestrator._expired_runtime_ids)
        assert runtime.stopped_sessions == []

        runtime.queue_turn_plan(TestTurnPlan())
        second_task = orchestrator.dispatch_inbound(make_message(seq=2))
        await wait_until(lambda: len(runtime.steered_turns) == 1)
        next(iter(runtime.active_streams)).release()
        first_turn, second_turn = await asyncio.gather(first_task, second_task)

        second_runtime = orchestrator.runtime_session("bcn-1")
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
        checked = await commands.check(session_id)
        await commands.send(
            session_id=session_id,
            command_id="terminal-wait",
            target=checked.messages[0].canonical_target,
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
        runtime_session = orchestrator.runtime_session(message.session_id)
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
        runtime_session = orchestrator.runtime_session("bcn-1")
        assert runtime_session is not None

        if context_first:
            runtime.emit_expire(runtime_session.id)
        else:
            await asyncio.sleep(0.06)
        await wait_until(lambda: orchestrator.runtime_session("bcn-1") is None)

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
async def test_quiet_inbound_refreshes_a_live_runtime_idle_deadline() -> None:
    orchestrator, runtime, _, wheel = await make_idle_timeout_node(80)
    try:
        await orchestrator.handle_inbound(make_message(seq=1))
        runtime_session = orchestrator.runtime_session("bcn-1")
        assert runtime_session is not None

        await asyncio.sleep(0.05)
        await orchestrator.handle_inbound(
            replace(make_message(seq=2), notifies_runtime=False)
        )
        await asyncio.sleep(0.05)

        assert orchestrator.runtime_session("bcn-1") is runtime_session
        async with asyncio.timeout(1):
            while orchestrator.runtime_session("bcn-1") is not None:
                await asyncio.sleep(0.01)
        assert runtime.stopped_sessions == [runtime_session]
    finally:
        await orchestrator.stop(timeout=1)
        await wheel.close()


@pytest.mark.asyncio
async def test_notifying_inbound_refreshes_the_current_runtime_deadline() -> None:
    orchestrator, runtime, _, wheel = await make_idle_timeout_node(80)
    try:
        await orchestrator.handle_inbound(make_message(seq=1))
        runtime_session = orchestrator.runtime_session("bcn-1")
        assert runtime_session is not None

        await asyncio.sleep(0.05)
        await orchestrator.handle_inbound(make_message(seq=2))
        await asyncio.sleep(0.05)

        assert orchestrator.runtime_session("bcn-1") is runtime_session
        assert len(runtime.started_sessions) == 1
    finally:
        await orchestrator.stop(timeout=1)
        await wheel.close()


@pytest.mark.asyncio
async def test_idle_expiry_replaces_the_runtime_on_next_notification() -> None:
    orchestrator, runtime, _, wheel = await make_idle_timeout_node(30)
    try:
        await orchestrator.handle_inbound(make_message(seq=1))
        first_runtime = orchestrator.runtime_session("bcn-1")
        assert first_runtime is not None
        async with asyncio.timeout(1):
            while orchestrator.runtime_session("bcn-1") is not None:
                await asyncio.sleep(0.01)

        await orchestrator.handle_inbound(make_message(seq=2))
        second_runtime = orchestrator.runtime_session("bcn-1")

        assert second_runtime is not None
        assert second_runtime.id != first_runtime.id
        assert runtime.stopped_sessions[0] is first_runtime
    finally:
        await orchestrator.stop(timeout=1)
        await wheel.close()


@pytest.mark.asyncio
async def test_expiry_waits_for_an_active_turn_to_return_idle() -> None:
    orchestrator, runtime, _, wheel = await make_idle_timeout_node(30)
    runtime.queue_turn_plan(TestTurnPlan(block_until_release=True))
    try:
        inbound_task = asyncio.create_task(
            orchestrator.handle_inbound(make_message(seq=1))
        )
        await runtime.turn_started.wait()
        runtime_session = orchestrator.runtime_session("bcn-1")
        assert runtime_session is not None
        await asyncio.sleep(0.06)

        assert orchestrator.runtime_session("bcn-1") is runtime_session
        assert runtime.stopped_sessions == []

        stream = next(iter(runtime.active_streams))
        stream.release()
        await inbound_task
        async with asyncio.timeout(1):
            while orchestrator.runtime_session("bcn-1") is not None:
                await asyncio.sleep(0.01)
        assert runtime.stopped_sessions == [runtime_session]
    finally:
        await orchestrator.stop(timeout=1)
        await wheel.close()


@pytest.mark.asyncio
async def test_independent_sessions_expire_without_cross_session_interference() -> None:
    orchestrator, runtime, _, wheel = await make_idle_timeout_node(50)
    try:
        await orchestrator.handle_inbound(make_message(session_id="bcn-a", seq=1))
        await asyncio.sleep(0.03)
        await orchestrator.handle_inbound(make_message(session_id="bcn-b", seq=1))
        async with asyncio.timeout(1):
            while orchestrator.runtime_session("bcn-a") is not None:
                await asyncio.sleep(0.01)

        assert orchestrator.runtime_session("bcn-b") is not None
        async with asyncio.timeout(1):
            while orchestrator.runtime_session("bcn-b") is not None:
                await asyncio.sleep(0.01)
        assert {session.bcn_session_id for session in runtime.stopped_sessions} == {
            "bcn-a",
            "bcn-b",
        }
    finally:
        await orchestrator.stop(timeout=1)
        await wheel.close()


@pytest.mark.asyncio
async def test_runtime_replacement_cancels_the_previous_timer_generation() -> None:
    orchestrator, _, _, wheel = await make_idle_timeout_node(1_000)
    try:
        await orchestrator.handle_inbound(make_message(seq=1))
        first_runtime = orchestrator.runtime_session("bcn-1")
        first_binding = orchestrator._runtime_timers["bcn-1"]
        assert first_runtime is not None
        orchestrator._state_machine.apply_reconciliation(
            "bcn-1",
            SessionRuntimeState.FAILED,
        )

        await orchestrator.handle_inbound(make_message(seq=2))

        second_runtime = orchestrator.runtime_session("bcn-1")
        second_binding = orchestrator._runtime_timers["bcn-1"]
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
        assert orchestrator.session_runtime_state("bcn-1") is SessionRuntimeState.IDLE
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
        assert orchestrator.session_runtime_state("bcn-1") is None
        assert orchestrator.runtime_session("bcn-1") is None
        assert len(runtime.reconciled_sessions) == 1
        assert len(runtime.started_turns) == 1
        assert len(storage.runtime_attempts) == 2
        assert len(channel.send_attempts) == 1
    finally:
        await orchestrator.stop(timeout=1)
