from __future__ import annotations

import pytest

from bazaar_compute_node.core.models import (
    AgentSignal,
    AgentState,
    AgentTick,
    AgentTickSource,
    BcnSession,
    ChannelSession,
    FreshCheckState,
    OutboundDeliveryState,
    OutboundMessage,
    RuntimeSession,
    RuntimeTurn,
    RuntimeTurnState,
    StateTransitionError,
    reduce_agent_tick,
)


def make_channel_session() -> ChannelSession:
    return ChannelSession(
        id="channel-1",
        channel="test",
        provider_thread_id="thread-1",
        created_at_ms=1,
        updated_at_ms=1,
    )


def make_bcn_session() -> BcnSession:
    return BcnSession(
        id="bcn-1",
        channel_session_id="channel-1",
        workspace_id="workspace-1",
        created_at_ms=1,
        updated_at_ms=1,
    )


def make_runtime_session() -> RuntimeSession:
    return RuntimeSession(
        id="runtime-1",
        bcn_session_id="bcn-1",
        channel_session_id="channel-1",
        runtime="test",
        workspace_id="workspace-1",
        created_at_ms=1,
        updated_at_ms=1,
    )


def make_runtime_turn() -> RuntimeTurn:
    return RuntimeTurn(
        turn_id="turn-1",
        session_id="runtime-1",
        state=RuntimeTurnState.STARTING,
        started_at_ms=1,
    )


def make_outbound_message() -> OutboundMessage:
    return OutboundMessage(
        outbound_message_id="outbound-1",
        command_id="command-1",
        session_id="bcn-1",
        channel_session_id="channel-1",
        target="#test:message-1",
        body="hello",
        state=OutboundDeliveryState.DRAFT,
        fresh_check_state=FreshCheckState.REQUIRED,
        created_at_ms=1,
    )


def test_agent_ticks_are_idempotent_and_reject_invalid_order() -> None:
    state = AgentState.CREATED
    start = AgentTick(
        source=AgentTickSource.SESSION,
        signal=AgentSignal.START_REQUESTED,
        observed_at_ms=2,
    )
    state = reduce_agent_tick(state, start)
    assert state is AgentState.STARTING
    assert reduce_agent_tick(state, start) is state

    state = reduce_agent_tick(
        state,
        AgentTick(
            source=AgentTickSource.RUNTIME,
            signal=AgentSignal.START_CONFIRMED,
            observed_at_ms=3,
        ),
    )
    state = reduce_agent_tick(
        state,
        AgentTick(
            source=AgentTickSource.CHANNEL,
            signal=AgentSignal.TURN_STARTED,
            observed_at_ms=4,
        ),
    )
    assert state is AgentState.WORKING
    state = reduce_agent_tick(
        state,
        AgentTick(
            source=AgentTickSource.RUNTIME,
            signal=AgentSignal.TURN_COMPLETED,
            observed_at_ms=5,
        ),
    )
    assert state is AgentState.IDLE

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
    state = reduce_agent_tick(
        AgentState.CREATED,
        AgentTick(
            source=AgentTickSource.RUNTIME,
            signal=AgentSignal.UNKNOWN,
            observed_at_ms=2,
        ),
    )
    assert state is AgentState.UNKNOWN
    state = reduce_agent_tick(
        state,
        AgentTick(
            source=AgentTickSource.RECOVERY,
            signal=AgentSignal.RECONCILE_REQUESTED,
            observed_at_ms=3,
        ),
    )
    state = reduce_agent_tick(
        state,
        AgentTick(
            source=AgentTickSource.RECOVERY,
            signal=AgentSignal.RECONCILE_CONFIRMED,
            observed_at_ms=4,
        ),
    )
    assert state is AgentState.IDLE


def test_agent_compaction_has_explicit_start_progress_and_completion_states() -> None:
    state = reduce_agent_tick(
        AgentState.CREATED,
        AgentTick(
            source=AgentTickSource.SESSION,
            signal=AgentSignal.WORKING_OBSERVED,
            observed_at_ms=2,
        ),
    )
    state = reduce_agent_tick(
        state,
        AgentTick(
            source=AgentTickSource.RUNTIME,
            signal=AgentSignal.COMPACTION_STARTED,
            observed_at_ms=3,
        ),
    )
    assert state is AgentState.COMPACTION_STARTING
    state = reduce_agent_tick(
        state,
        AgentTick(
            source=AgentTickSource.RUNTIME,
            signal=AgentSignal.COMPACTION_IN_PROGRESS,
            observed_at_ms=4,
        ),
    )
    assert state is AgentState.COMPACTING
    state = reduce_agent_tick(
        state,
        AgentTick(
            source=AgentTickSource.RUNTIME,
            signal=AgentSignal.COMPACTION_COMPLETED,
            observed_at_ms=5,
        ),
    )
    assert state is AgentState.COMPACTION_COMPLETED

    fallback = reduce_agent_tick(
        AgentState.CREATED,
        AgentTick(
            source=AgentTickSource.CHANNEL,
            signal=AgentSignal.WORKING_OBSERVED,
            observed_at_ms=2,
        ),
    )
    assert fallback is AgentState.WORKING


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
