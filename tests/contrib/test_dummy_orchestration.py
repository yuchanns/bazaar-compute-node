from __future__ import annotations

import asyncio

import pytest

from bazaar_compute_node.contrib.dummy import (
    DummyAudit,
    DummyChannel,
    DummyRuntime,
    DummyStorage,
    DummyTurnPlan,
)
from bazaar_compute_node.core.command import ICommandService
from bazaar_compute_node.core.lifecycle import TimeoutBudget
from bazaar_compute_node.core.models import (
    ApprovalRequest,
    BcnSessionState,
    InboundMessage,
    OutboundDeliveryState,
    RuntimeEventState,
    RuntimeProcessState,
    RuntimeTurnState,
)
from bazaar_compute_node.core.orchestration import SessionOrchestrator
from bazaar_compute_node.core.outcomes import ProviderCallResult, ProviderCallStatus
from bazaar_compute_node.core.storage import NodeIdentity


def make_message(
    *,
    bcn_session_id: str = "bcn-1",
    seq: int = 1,
    message_id: str | None = None,
) -> InboundMessage:
    channel_session_id = f"channel-{bcn_session_id}"
    return InboundMessage(
        seq=seq,
        message_id=message_id or f"message-{bcn_session_id}-{seq}",
        bcn_session_id=bcn_session_id,
        channel_session_id=channel_session_id,
        channel_slug="dummy",
        provider_message_id=f"provider-{bcn_session_id}-{seq}",
        received_at_ms=seq,
        sender_id="sender-1",
        sender_display_name="Sender",
        message_type="text",
        canonical_target=f"#dummy:{bcn_session_id}",
        body=f"inbound-{seq}",
        provider_thread_id=f"thread-{bcn_session_id}",
    )


def make_budget() -> TimeoutBudget:
    return TimeoutBudget(
        startup_seconds=1,
        provider_call_seconds=1,
        command_seconds=1,
        shutdown_seconds=1,
    )


async def make_node() -> tuple[
    SessionOrchestrator,
    DummyChannel,
    DummyRuntime,
    DummyStorage,
    DummyAudit,
]:
    channel = DummyChannel()
    runtime = DummyRuntime()
    storage = DummyStorage()
    audit = DummyAudit()
    orchestrator = SessionOrchestrator(
        node_id="node-1",
        workspace_id="workspace-1",
        channel=channel,
        runtime=runtime,
        storage=storage,
        audit=audit,
        timeout_budget=make_budget(),
        runtime_slug="dummy",
    )
    runtime.command_service = orchestrator
    await orchestrator.start(timeout=1)
    return orchestrator, channel, runtime, storage, audit


@pytest.mark.asyncio
async def test_orchestrator_initializes_storage_identity_before_runtime() -> None:
    channel = DummyChannel()
    runtime = DummyRuntime()
    storage = DummyStorage()
    audit = DummyAudit()
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


@pytest.mark.asyncio
async def test_dummy_channel_storage_runtime_turn_path() -> None:
    orchestrator, channel, runtime, storage, audit = await make_node()
    try:
        message = make_message()
        await channel.inject(message)
        await wait_until(
            lambda: (
                storage.runtime_turns.get("turn-message-bcn-1-1")
                and storage.runtime_turns["turn-message-bcn-1-1"].state
                is RuntimeTurnState.COMPLETED
            )
        )

        assert storage.inbound_messages["bcn-1"] == [message]
        assert storage.bcn_sessions["bcn-1"].state is BcnSessionState.RUNNING
        assert (
            storage.runtime_sessions["runtime-bcn-1"].process_state
            is RuntimeProcessState.RUNNING
        )
        assert runtime.started_turns
        assert any(
            event.correlation.bcn_session_id == "bcn-1"
            and event.correlation.turn_id == "turn-message-bcn-1-1"
            for event in audit.events
        )
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_dummy_runtime_can_run_real_command_service_behavior() -> None:
    orchestrator, channel, runtime, storage, _audit = await make_node()

    async def command_script(commands: ICommandService, bcn_session_id: str) -> None:
        checked = await commands.check(bcn_session_id, timeout=1)
        if not checked.messages:
            raise AssertionError("command did not observe the inbound message")
        history = await commands.read(
            bcn_session_id,
            target=checked.messages[0].canonical_target,
            timeout=1,
        )
        if not history.messages:
            raise AssertionError("history command did not observe the inbound message")
        outbound = await commands.send(
            bcn_session_id=bcn_session_id,
            command_id="command-1",
            target=checked.messages[0].canonical_target,
            body="runtime-generated reply",
            created_at_ms=2,
            timeout=1,
        )
        if outbound.state is not OutboundDeliveryState.SENT:
            raise AssertionError("command did not deliver the outbound message")

    try:
        runtime.queue_turn_plan(DummyTurnPlan(command_script=command_script))
        await channel.inject(make_message())
        await wait_until(
            lambda: (
                storage.runtime_turns.get("turn-message-bcn-1-1")
                and storage.runtime_turns["turn-message-bcn-1-1"].state
                is RuntimeTurnState.COMPLETED
            )
        )
        assert len(channel.sent_messages) == 1
        assert storage.cursors["bcn-1"].delivered_through_seq == 1
        assert storage.outbound_messages
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_runtime_failure_and_unknown_stream_are_persisted() -> None:
    orchestrator, channel, runtime, storage, _ = await make_node()
    try:
        runtime.queue_turn_plan(
            DummyTurnPlan(
                states=(RuntimeEventState.STARTED, RuntimeEventState.FAILED),
            )
        )
        runtime.queue_turn_plan(DummyTurnPlan(states=(RuntimeEventState.STARTED,)))
        await channel.inject(make_message(seq=1))
        await wait_until(
            lambda: (
                storage.runtime_turns.get("turn-message-bcn-1-1")
                and storage.runtime_turns["turn-message-bcn-1-1"].state
                is RuntimeTurnState.FAILED
            )
        )
        await channel.inject(make_message(seq=2))
        await wait_until(
            lambda: (
                storage.runtime_turns.get("turn-message-bcn-1-2")
                and storage.runtime_turns["turn-message-bcn-1-2"].state
                is RuntimeTurnState.UNKNOWN
            )
        )
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_approval_is_routed_to_the_current_channel_session() -> None:
    orchestrator, channel, runtime, storage, audit = await make_node()
    try:
        request = ApprovalRequest(
            request_id="approval-1",
            bcn_session_id="bcn-1",
            agent_runtime_session_id="runtime-bcn-1",
            action="dummy-action",
            created_at_ms=1,
            turn_id="turn-message-bcn-1-1",
        )
        runtime.queue_turn_plan(DummyTurnPlan(approval_request=request))
        await channel.inject(make_message())
        await wait_until(
            lambda: (
                storage.runtime_turns.get("turn-message-bcn-1-1")
                and storage.runtime_turns["turn-message-bcn-1-1"].state
                is RuntimeTurnState.COMPLETED
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

        rejected_without_snapshot = await orchestrator.send(
            bcn_session_id="bcn-1",
            command_id="command-before-check",
            target="#dummy:bcn-1",
            body="reply",
            created_at_ms=2,
            timeout=1,
        )
        assert rejected_without_snapshot.state is OutboundDeliveryState.REJECTED
        assert not channel.send_attempts

        checked = await orchestrator.check("bcn-1", timeout=1)
        assert checked.messages
        delivered = await orchestrator.send(
            bcn_session_id="bcn-1",
            command_id="command-after-check",
            target="#dummy:bcn-1",
            body="reply",
            created_at_ms=3,
            timeout=1,
        )
        assert delivered.state is OutboundDeliveryState.SENT
        assert len(channel.send_attempts) == 1

        await channel.inject(make_message(seq=2))
        await wait_until(lambda: len(storage.inbound_messages["bcn-1"]) == 2)
        stale = await orchestrator.send(
            bcn_session_id="bcn-1",
            command_id="command-stale",
            target="#dummy:bcn-1",
            body="reply",
            created_at_ms=4,
            timeout=1,
        )
        assert stale.state is OutboundDeliveryState.REJECTED
        assert len(channel.send_attempts) == 1
    finally:
        await orchestrator.stop(timeout=1)


@pytest.mark.asyncio
async def test_graceful_stop_cancels_turn_and_closes_runtime_stream() -> None:
    orchestrator, channel, runtime, storage, _ = await make_node()
    runtime.queue_turn_plan(DummyTurnPlan(block_until_release=True))
    task = orchestrator.dispatch_inbound(make_message())
    await wait_until(lambda: bool(runtime.active_streams))

    await orchestrator.stop(timeout=1)
    result = await asyncio.gather(task, return_exceptions=True)

    assert isinstance(result[0], asyncio.CancelledError)
    assert (
        storage.runtime_turns["turn-message-bcn-1-1"].state
        is RuntimeTurnState.CANCELLED
    )
    assert runtime.closed_streams
    assert runtime.stopped
    assert channel.stopped
    assert storage.stopped


@pytest.mark.asyncio
async def test_multiple_sessions_keep_workspace_and_correlation_isolated() -> None:
    orchestrator, _, runtime, storage, audit = await make_node()
    try:
        first, second = await asyncio.gather(
            orchestrator.handle_inbound(make_message(bcn_session_id="bcn-a")),
            orchestrator.handle_inbound(make_message(bcn_session_id="bcn-b")),
        )

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
async def test_runtime_start_failure_does_not_claim_a_running_session() -> None:
    orchestrator, channel, runtime, storage, _ = await make_node()
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

        assert result.state is RuntimeTurnState.FAILED
        assert (
            storage.runtime_sessions["runtime-bcn-1"].process_state
            is RuntimeProcessState.FAILED
        )
        assert not channel.sent_messages
    finally:
        await orchestrator.stop(timeout=1)
