from __future__ import annotations

import pytest

from bazaar_compute_node.core.models import (
    AgentSignal,
    AgentState,
    AgentTick,
    AgentTickSource,
    BcnSession,
    ChannelSession,
    ChannelSessionState,
    FreshCheckState,
    OutboundDeliveryState,
    OutboundMessage,
    RuntimeProcessState,
    RuntimeSession,
    RuntimeTurn,
    RuntimeTurnState,
    StateTransitionError,
    reduce_agent_tick,
)


def make_channel_session() -> ChannelSession:
    return ChannelSession(
        channel_session_id="channel-1",
        channel_slug="test",
        provider_conversation_key="conversation-1",
        provider_thread_key="",
        state=ChannelSessionState.ACTIVE,
        created_at_ms=1,
        updated_at_ms=1,
    )


def make_bcn_session() -> BcnSession:
    return BcnSession(
        bcn_session_id="bcn-1",
        channel_session_id="channel-1",
        workspace_id="workspace-1",
        state=AgentState.CREATED,
        created_at_ms=1,
        updated_at_ms=1,
    )


def make_runtime_session() -> RuntimeSession:
    return RuntimeSession(
        agent_runtime_session_id="runtime-1",
        bcn_session_id="bcn-1",
        channel_session_id="channel-1",
        runtime_slug="test",
        workspace_id="workspace-1",
        process_state=RuntimeProcessState.STARTING,
        created_at_ms=1,
        updated_at_ms=1,
    )


def make_runtime_turn() -> RuntimeTurn:
    return RuntimeTurn(
        turn_id="turn-1",
        agent_runtime_session_id="runtime-1",
        state=RuntimeTurnState.STARTING,
        started_at_ms=1,
    )


def make_outbound_message() -> OutboundMessage:
    return OutboundMessage(
        outbound_message_id="outbound-1",
        command_id="command-1",
        bcn_session_id="bcn-1",
        channel_session_id="channel-1",
        target="#test:message-1",
        body="hello",
        state=OutboundDeliveryState.DRAFT,
        fresh_check_state=FreshCheckState.REQUIRED,
        created_at_ms=1,
    )


def test_session_state_transitions_are_explicit() -> None:
    channel_session = make_channel_session().transition_to(
        ChannelSessionState.CLOSED,
        updated_at_ms=2,
    )
    assert channel_session.state is ChannelSessionState.CLOSED

    session = make_bcn_session().apply_tick(
        AgentTick(
            source=AgentTickSource.SESSION,
            signal=AgentSignal.START_REQUESTED,
            observed_at_ms=2,
        )
    )
    session = session.apply_tick(
        AgentTick(
            source=AgentTickSource.RUNTIME,
            signal=AgentSignal.START_CONFIRMED,
            observed_at_ms=2,
        )
    )
    session = session.transition_to(AgentState.STOPPING, updated_at_ms=3)
    session = session.transition_to(AgentState.STOPPED, updated_at_ms=4)

    assert session.stopped_at_ms == 4
    assert session.transition_to(AgentState.STOPPED, updated_at_ms=5) is session
    with pytest.raises(StateTransitionError):
        session.transition_to(AgentState.WORKING, updated_at_ms=5)


def test_agent_ticks_are_idempotent_and_reject_invalid_order() -> None:
    session = make_bcn_session()
    start = AgentTick(
        source=AgentTickSource.SESSION,
        signal=AgentSignal.START_REQUESTED,
        observed_at_ms=2,
    )
    session = session.apply_tick(start)
    assert session.state is AgentState.STARTING
    assert session.apply_tick(start) is session

    session = session.apply_tick(
        AgentTick(
            source=AgentTickSource.RUNTIME,
            signal=AgentSignal.START_CONFIRMED,
            observed_at_ms=3,
        )
    )
    session = session.apply_tick(
        AgentTick(
            source=AgentTickSource.CHANNEL,
            signal=AgentSignal.TURN_STARTED,
            observed_at_ms=4,
        )
    )
    assert session.state is AgentState.WORKING
    session = session.apply_tick(
        AgentTick(
            source=AgentTickSource.RUNTIME,
            signal=AgentSignal.TURN_COMPLETED,
            observed_at_ms=5,
        )
    )
    assert session.state is AgentState.IDLE

    with pytest.raises(StateTransitionError):
        reduce_agent_tick(
            AgentState.IDLE,
            AgentTick(
                source=AgentTickSource.RUNTIME,
                signal=AgentSignal.START_REQUESTED,
                observed_at_ms=6,
            ),
        )


def test_agent_unknown_state_requires_reconciliation() -> None:
    session = make_bcn_session().apply_tick(
        AgentTick(
            source=AgentTickSource.RUNTIME,
            signal=AgentSignal.UNKNOWN,
            observed_at_ms=2,
        )
    )
    assert session.state is AgentState.UNKNOWN
    session = session.apply_tick(
        AgentTick(
            source=AgentTickSource.RECOVERY,
            signal=AgentSignal.RECONCILE_REQUESTED,
            observed_at_ms=3,
        )
    )
    session = session.apply_tick(
        AgentTick(
            source=AgentTickSource.RECOVERY,
            signal=AgentSignal.RECONCILE_CONFIRMED,
            observed_at_ms=4,
        )
    )
    assert session.state is AgentState.IDLE


def test_agent_compaction_has_explicit_start_progress_and_completion_states() -> None:
    session = make_bcn_session().apply_tick(
        AgentTick(
            source=AgentTickSource.SESSION,
            signal=AgentSignal.WORKING_OBSERVED,
            observed_at_ms=2,
        )
    )
    session = session.apply_tick(
        AgentTick(
            source=AgentTickSource.RUNTIME,
            signal=AgentSignal.COMPACTION_STARTED,
            observed_at_ms=3,
        )
    )
    assert session.state is AgentState.COMPACTION_STARTING
    session = session.apply_tick(
        AgentTick(
            source=AgentTickSource.RUNTIME,
            signal=AgentSignal.COMPACTION_IN_PROGRESS,
            observed_at_ms=4,
        )
    )
    assert session.state is AgentState.COMPACTING
    session = session.apply_tick(
        AgentTick(
            source=AgentTickSource.RUNTIME,
            signal=AgentSignal.COMPACTION_COMPLETED,
            observed_at_ms=5,
        )
    )
    assert session.state is AgentState.COMPACTION_COMPLETED

    fallback = make_bcn_session().apply_tick(
        AgentTick(
            source=AgentTickSource.CHANNEL,
            signal=AgentSignal.WORKING_OBSERVED,
            observed_at_ms=2,
        )
    )
    assert fallback.state is AgentState.WORKING


def test_runtime_turn_unknown_state_requires_reconciliation() -> None:
    turn = make_runtime_turn().transition_to(
        RuntimeTurnState.RUNNING,
        at_ms=2,
    )
    turn = turn.transition_to(RuntimeTurnState.UNKNOWN, at_ms=3)
    turn = turn.transition_to(RuntimeTurnState.RECONCILING, at_ms=4)
    turn = turn.transition_to(RuntimeTurnState.COMPLETED, at_ms=5)

    assert turn.completed_at_ms == 5
    with pytest.raises(StateTransitionError):
        turn.transition_to(RuntimeTurnState.FAILED, at_ms=6)


def test_outbound_delivery_requires_a_passed_fresh_check() -> None:
    outbound = make_outbound_message()
    with pytest.raises(ValueError, match="fresh check"):
        outbound.transition_to(OutboundDeliveryState.PENDING, at_ms=2)

    outbound = outbound.record_fresh_check(
        FreshCheckState.PASSED,
        snapshot_seq=10,
        current_inbound_seq=10,
    )
    outbound = outbound.transition_to(OutboundDeliveryState.PENDING, at_ms=3)
    outbound = outbound.transition_to(
        OutboundDeliveryState.QUEUED,
        at_ms=4,
        provider_receipt_ref="queue-receipt-1",
    )
    assert outbound.state is OutboundDeliveryState.QUEUED
    assert outbound.completed_at_ms is None
    outbound = outbound.transition_to(
        OutboundDeliveryState.SENT,
        at_ms=5,
        provider_message_id="provider-message-1",
    )

    assert outbound.state is OutboundDeliveryState.SENT
    assert outbound.completed_at_ms == 5


def test_runtime_session_reconciliation_records_the_observation_time() -> None:
    session = make_runtime_session().transition_process_to(
        RuntimeProcessState.RUNNING,
        updated_at_ms=2,
    )
    session = session.transition_process_to(
        RuntimeProcessState.UNKNOWN,
        updated_at_ms=3,
    )
    session = session.transition_process_to(
        RuntimeProcessState.RECONCILING,
        updated_at_ms=4,
    )

    assert session.last_reconciled_at_ms == 4
