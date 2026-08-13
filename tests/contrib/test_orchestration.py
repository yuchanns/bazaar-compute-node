from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Protocol, cast
from uuid import uuid7

import pytest
from bcn_test_support import (
    MemoryStorage,
    RecordingAudit,
    TestChannel,
    TestRuntime,
    TestTurnPlan,
)

from bazaar_compute_node.app.application import NodeApplication
from bazaar_compute_node.app.registry import AdapterFactories
from bazaar_compute_node.contrib.sqlite import SqliteDatabase
from bazaar_compute_node.core.audit import AuditEvent
from bazaar_compute_node.core.channel import ChannelDeliveryReceipt, IChannel
from bazaar_compute_node.core.command import ICommandService
from bazaar_compute_node.core.lifecycle import TimeoutBudget
from bazaar_compute_node.core.models import (
    AgentSignal,
    AgentState,
    AgentTick,
    AgentTickSource,
    ApprovalRequest,
    ChannelTargetKind,
    InboundMessage,
    OutboundDeliveryState,
    OutboundMessage,
    RuntimeTurnState,
)
from bazaar_compute_node.core.orchestration import SessionOrchestrator
from bazaar_compute_node.core.outcomes import ProviderCallResult, ProviderCallStatus
from bazaar_compute_node.core.paths import resolve_data_dir
from bazaar_compute_node.core.runtime import IRuntime, RuntimeCommandContext
from bazaar_compute_node.core.storage import IStorage, NodeIdentity


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
        sender="Sender",
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
    *, workspace: Callable[[], Path] = Path.cwd
) -> tuple[
    SessionOrchestrator,
    TestChannel,
    TestRuntime,
    MemoryStorage,
    RecordingAudit,
]:
    channel = TestChannel()
    runtime = TestRuntime()
    storage = MemoryStorage()
    audit = RecordingAudit()
    orchestrator = SessionOrchestrator(
        node_id="node-1",
        workspace_id="workspace-1",
        channel=channel,
        runtime=runtime,
        storage=storage,
        audit=audit,
        timeout_budget=make_budget(),
        workspace=workspace,
    )
    runtime.command_service = orchestrator.command_service
    await orchestrator.start(timeout=1)
    return orchestrator, channel, runtime, storage, audit


@pytest.mark.asyncio
async def test_orchestrator_initializes_storage_identity_before_runtime() -> None:
    channel = TestChannel()
    runtime = TestRuntime()
    storage = MemoryStorage()
    audit = RecordingAudit()
    seen: list[NodeIdentity] = []

    async def on_node_initialized(identity: NodeIdentity) -> None:
        assert not runtime.started
        seen.append(identity)

    orchestrator = SessionOrchestrator(
        channel=channel,
        runtime=runtime,
        storage=storage,
        audit=audit,
        timeout_budget=make_budget(),
        workspace=Path.cwd,
        on_node_initialized=on_node_initialized,
    )
    await orchestrator.start(timeout=1)
    try:
        assert storage.node_identity is not None
        assert seen == [storage.node_identity]
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
    sent_messages: list[OutboundMessage]

    async def inject(self, message: InboundMessage) -> None: ...


class _AcceptanceAudit(RecordingAudit):
    def __init__(self) -> None:
        super().__init__()
        self.first_check_completed = asyncio.Event()
        self.release_first_check = asyncio.Event()
        self._first_check_blocked = False

    async def append(self, event: AuditEvent, *, timeout: float) -> None:
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
        storage = SqliteDatabase()
        await storage.start(timeout=30)
        identity = await storage.initialize()
        audit = _AcceptanceAudit()
        node = NodeApplication(
            factories=AdapterFactories(
                channel=lambda _context, channel_instance=channel_instance: cast(
                    IChannel, channel_instance
                ),
                runtime=runtime,
                storage=lambda storage=storage: storage,
                audit=lambda audit=audit: audit,
            ),
            endpoint_path=resolve_data_dir() / f"natural-{uuid7()}.sock",
            node_id=identity.node_id,
            workspace_id=identity.workspace_id,
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
                storage,
                session_id,
                1,
            )
            first_row = persisted[0]
            await _wait_for_audit_event(
                audit,
                session_id=session_id,
                event_suffix="turn.started",
                turn_id=f"turn-{first_row.message_id}",
            )
            async with asyncio.timeout(180):
                await audit.first_check_completed.wait()
            await channel_instance.inject(second)
            persisted = await _wait_for_inbound_messages(
                storage,
                session_id,
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
                session_id=session_id,
                event_suffix="turn.completed",
                turn_id=f"turn-{first_row.message_id}",
            )
            async with storage.transaction() as transaction:
                cursor = await transaction.get_consumer_cursor(session_id)
            if cursor is None or cursor.delivered_through_seq < second_row.seq:
                await _wait_for_audit_event(
                    audit,
                    session_id=session_id,
                    event_suffix="turn.completed",
                    turn_id=f"turn-{second_row.message_id}",
                )

            await channel_instance.inject(third)
            persisted = await _wait_for_inbound_messages(
                storage,
                session_id,
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
                session_id=session_id,
                event_suffix="turn.completed",
                turn_id=f"turn-{third_row.message_id}",
            )
            for inbound in (second_row, third_row):
                delivery_ids = {
                    event.correlation.outbound_message_id
                    for event in audit.events
                    if (
                        event.correlation.bcn_session_id == session_id
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
                            event.correlation.bcn_session_id == session_id
                            and event.event_name == "bcc.send.fresh_check.passed"
                            and event.correlation.inbound_seq == inbound.seq
                            and event.correlation.outbound_message_id is not None
                        )
                    }
                )
                assert any(
                    message.outbound_message_id in delivery_ids
                    and message.target == inbound.canonical_target
                    and bool(message.body.strip())
                    for message in channel_instance.sent_messages
                )
            rejected_delivery_ids = {
                event.correlation.outbound_message_id
                for event in audit.events
                if (
                    event.correlation.bcn_session_id == session_id
                    and event.event_name == "bcc.send.fresh_check.failed"
                    and event.correlation.outbound_message_id is not None
                )
            }
            assert rejected_delivery_ids.isdisjoint(
                {
                    message.outbound_message_id
                    for message in channel_instance.sent_messages
                }
            )
        finally:
            audit.release_first_check.set()
            await node.stop()
            if storage.is_started:
                await storage.stop(timeout=30)


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
        assert orchestrator.agent_state("bcn-1") is AgentState.IDLE
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
    orchestrator, channel, runtime, _storage, audit = await make_node()
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
    orchestrator, channel, runtime, _storage, audit = await make_node()
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
        assert orchestrator.agent_state("bcn-1") is AgentState.IDLE
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_stream_event_for_another_session_is_discarded() -> None:
    orchestrator, channel, runtime, _storage, audit = await make_node()
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
        await channel.inject(make_message())
        await wait_until(
            lambda: any(
                event.event_name == "runtime.turn.completed"
                and event.correlation.turn_id == "turn-message-bcn-1-1"
                for event in audit.events
            )
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
    orchestrator, channel, _runtime, storage, _audit = await make_node()
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
    orchestrator, channel, runtime, _storage, audit = await make_node()
    try:
        request = ApprovalRequest(
            request_id="approval-1",
            session_id="bcn-1",
            runtime_session_id="runtime-bcn-1",
            action="test-action",
            created_at_ms=1,
            turn_id="turn-message-bcn-1-1",
        )
        runtime.queue_turn_plan(TestTurnPlan(approval_request=request))
        await channel.inject(make_message())
        await wait_until(
            lambda: any(
                event.event_name == "runtime.turn.completed"
                and event.correlation.turn_id == "turn-message-bcn-1-1"
                for event in audit.events
            )
        )

        assert channel.approval_requests == [request]
        assert runtime.approval_results
        assert runtime.approval_results[0].request_id == request.request_id
        assert any(event.event_name == "approval.decided" for event in audit.events)
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_fresh_check_rejects_stale_send_before_channel_call() -> None:
    orchestrator, channel, _runtime, storage, _audit = await make_node()
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
    orchestrator, channel, _runtime, storage, _audit = await make_node(
        workspace=lambda: tmp_path
    )
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
        assert channel.send_requests[0].outbound.attachments == delivered.attachments
        assert [attachment.relative_path for attachment in delivered.attachments] == [
            "first.txt",
            "second.json",
        ]
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_send_validates_target_and_preserves_provider_delivery_states() -> None:
    orchestrator, channel, _runtime, storage, audit = await make_node()
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
    assert orchestrator.agent_state("bcn-1") is AgentState.WORKING

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
    assert storage.stopped
    assert orchestrator.agent_state("bcn-1") is None


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
        assert second_sender not in steer_input

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
async def test_agent_tick_api_serializes_duplicate_runtime_and_channel_ticks() -> None:
    orchestrator, channel, _runtime, storage, _audit = await make_node()
    try:
        await channel.inject(make_message())
        await wait_until(
            lambda: (
                storage.bcn_sessions.get("bcn-1") is not None
                and orchestrator.agent_state("bcn-1") is AgentState.IDLE
            )
        )

        started_tick = AgentTick(
            source=AgentTickSource.CHANNEL,
            signal=AgentSignal.TURN_STARTED,
            observed_at_ms=storage.bcn_sessions["bcn-1"].updated_at_ms,
        )
        started = await asyncio.gather(
            orchestrator.tick("bcn-1", started_tick),
            orchestrator.tick("bcn-1", started_tick),
        )
        assert set(started) == {AgentState.WORKING}

        completed_tick = AgentTick(
            source=AgentTickSource.RUNTIME,
            signal=AgentSignal.TURN_COMPLETED,
            observed_at_ms=storage.bcn_sessions["bcn-1"].updated_at_ms,
        )
        completed = await asyncio.gather(
            orchestrator.tick("bcn-1", completed_tick),
            orchestrator.tick("bcn-1", completed_tick),
        )
        assert set(completed) == {AgentState.IDLE}

        for signal, expected in (
            (AgentSignal.COMPACTION_STARTED, AgentState.COMPACTION_STARTING),
            (AgentSignal.COMPACTION_IN_PROGRESS, AgentState.COMPACTING),
            (AgentSignal.COMPACTION_COMPLETED, AgentState.COMPACTION_COMPLETED),
            (AgentSignal.WORKING_OBSERVED, AgentState.WORKING),
        ):
            current = storage.bcn_sessions["bcn-1"]
            updated = await orchestrator.tick(
                "bcn-1",
                AgentTick(
                    source=AgentTickSource.RUNTIME,
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
        assert storage.runtime_sessions == {}
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
        assert "runtime-invalid" not in storage.runtime_sessions
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_runtime_start_failure_does_not_claim_a_running_session() -> None:
    orchestrator, channel, runtime, _, _ = await make_node()
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
        assert result.state is RuntimeTurnState.FAILED
        assert orchestrator.agent_state("bcn-1") is AgentState.FAILED
        assert not channel.sent_messages
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_pre_start_failure_resumes_and_retries_the_current_inbound() -> None:
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
        assert orchestrator.agent_state("bcn-1") is AgentState.IDLE
        assert len(runtime.resumed_sessions) == 1
        assert runtime.resumed_sessions[0].provider_thread_id is not None
        assert len(runtime.started_turns) == 2
        assert len(storage.runtime_attempts) == 2
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_repeated_pre_start_failure_stops_after_one_retry() -> None:
    orchestrator, _, runtime, storage, _ = await make_node()
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
        assert orchestrator.agent_state("bcn-1") is AgentState.FAILED
        assert len(runtime.resumed_sessions) == 1
        assert len(runtime.started_turns) == 1
        assert len(storage.runtime_attempts) == 2
    finally:
        await orchestrator.stop(timeout=1)
