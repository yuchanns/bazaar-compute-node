from __future__ import annotations

import pytest

from bazaar_compute_node.core.command import InboxListResult
from bazaar_compute_node.core.models import (
    BcnSession,
    ChannelSession,
    ChannelTargetKind,
    InboxTargetSummary,
    Message,
    MessageDirection,
    OutboundAttachment,
    OutboundDeliveryState,
    RuntimeSession,
    SenderIdentity,
    SenderKind,
    SessionRuntimeObservation,
    SessionRuntimeObservationSource,
    SessionRuntimeSignal,
    SessionRuntimeState,
    StateTransitionError,
    reduce_session_runtime_state,
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
        runtime_index=0,
        workspace_id="workspace-1",
        created_at_ms=1,
        updated_at_ms=1,
    )


def make_outbound_message() -> Message:
    return Message(
        direction=MessageDirection.OUTBOUND,
        seq=0,
        message_id="outbound-1",
        command_id="command-1",
        session_id="bcn-1",
        channel_session_id="channel-1",
        target="#test:message-1",
        body="hello",
        delivery_state=OutboundDeliveryState.PENDING,
        created_at_ms=1,
        provider_attempted_at_ms=2,
    )


def make_inbox_target() -> InboxTargetSummary:
    return InboxTargetSummary(
        target="dm:user-1",
        session_id="bcn-1",
        target_kind=ChannelTargetKind.DM,
        current=True,
        pending_count=2,
        last_activity_at_ms=100,
        latest_message_id="message-1",
        latest_sender=SenderIdentity(id="user-1", name="Test User"),
        latest_provider_time_ms=99,
        latest_received_at_ms=100,
    )


def test_sender_identity_separates_stable_id_from_handle() -> None:
    named = SenderIdentity(id="test-user-id", name="test-user")
    unnamed = SenderIdentity(id="test-user-id")
    full = SenderIdentity(
        id="test-user-id",
        name="test-user",
        display_name="Test User",
    )

    # case: the handle is what a sender is addressed by
    assert named.handle == "test-user"
    assert full.handle == "test-user"

    # case: a provider that offers no handle falls back to the id
    assert unnamed.handle == "test-user-id"

    # case: the human name stays separate from both
    assert full.display_name == "Test User"
    assert named.display_name is None


def test_inbox_list_result_enforces_pagination_invariant() -> None:
    result = InboxListResult(
        targets=(make_inbox_target(),),
        total=3,
        shown=1,
        offset=1,
        has_more=True,
    )

    assert result.shown == len(result.targets)
    assert result.offset + result.shown < result.total

    with pytest.raises(ValueError, match="has_more"):
        InboxListResult(
            targets=result.targets,
            total=result.total,
            shown=result.shown,
            offset=result.offset,
            has_more=False,
        )


def test_outbound_attachment_requires_a_safe_relative_path_and_digest() -> None:
    attachment = OutboundAttachment(
        name="report.txt",
        relative_path="reports/report.txt",
        media_type="text/plain",
        size_bytes=7,
        sha256="a" * 64,
    )

    assert attachment.relative_path == "reports/report.txt"
    with pytest.raises(ValueError, match="workspace"):
        OutboundAttachment(
            name="report.txt",
            relative_path="../report.txt",
            media_type=None,
            size_bytes=7,
            sha256="a" * 64,
        )
    with pytest.raises(ValueError, match="workspace"):
        OutboundAttachment(
            name="report.txt",
            relative_path="reports\\report.txt",
            media_type=None,
            size_bytes=7,
            sha256="a" * 64,
        )
    with pytest.raises(ValueError, match="sha256"):
        OutboundAttachment(
            name="report.txt",
            relative_path="report.txt",
            media_type=None,
            size_bytes=7,
            sha256="invalid",
        )


def test_session_runtime_observations_are_idempotent_and_reject_invalid_order() -> None:
    state = SessionRuntimeState.CREATED
    start = SessionRuntimeObservation(
        source=SessionRuntimeObservationSource.SESSION,
        signal=SessionRuntimeSignal.START_REQUESTED,
        observed_at_ms=2,
    )
    state = reduce_session_runtime_state(state, start)
    assert state is SessionRuntimeState.STARTING
    assert reduce_session_runtime_state(state, start) is state

    state = reduce_session_runtime_state(
        state,
        SessionRuntimeObservation(
            source=SessionRuntimeObservationSource.RUNTIME,
            signal=SessionRuntimeSignal.START_CONFIRMED,
            observed_at_ms=3,
        ),
    )
    state = reduce_session_runtime_state(
        state,
        SessionRuntimeObservation(
            source=SessionRuntimeObservationSource.CHANNEL,
            signal=SessionRuntimeSignal.TURN_STARTED,
            observed_at_ms=4,
        ),
    )
    assert state is SessionRuntimeState.WORKING
    state = reduce_session_runtime_state(
        state,
        SessionRuntimeObservation(
            source=SessionRuntimeObservationSource.RUNTIME,
            signal=SessionRuntimeSignal.TURN_COMPLETED,
            observed_at_ms=5,
        ),
    )
    assert state is SessionRuntimeState.IDLE

    with pytest.raises(StateTransitionError):
        reduce_session_runtime_state(
            SessionRuntimeState.IDLE,
            SessionRuntimeObservation(
                source=SessionRuntimeObservationSource.RUNTIME,
                signal=SessionRuntimeSignal.START_REQUESTED,
                observed_at_ms=6,
            ),
        )


def test_session_runtime_unknown_state_requires_reconciliation() -> None:
    state = reduce_session_runtime_state(
        SessionRuntimeState.CREATED,
        SessionRuntimeObservation(
            source=SessionRuntimeObservationSource.RUNTIME,
            signal=SessionRuntimeSignal.UNKNOWN,
            observed_at_ms=2,
        ),
    )
    assert state is SessionRuntimeState.UNKNOWN
    state = reduce_session_runtime_state(
        state,
        SessionRuntimeObservation(
            source=SessionRuntimeObservationSource.RECOVERY,
            signal=SessionRuntimeSignal.RECONCILE_REQUESTED,
            observed_at_ms=3,
        ),
    )
    state = reduce_session_runtime_state(
        state,
        SessionRuntimeObservation(
            source=SessionRuntimeObservationSource.RECOVERY,
            signal=SessionRuntimeSignal.RECONCILE_CONFIRMED,
            observed_at_ms=4,
        ),
    )
    assert state is SessionRuntimeState.IDLE


def test_session_runtime_compaction_has_explicit_start_progress_and_completion_states() -> (
    None
):
    state = reduce_session_runtime_state(
        SessionRuntimeState.CREATED,
        SessionRuntimeObservation(
            source=SessionRuntimeObservationSource.SESSION,
            signal=SessionRuntimeSignal.WORKING_OBSERVED,
            observed_at_ms=2,
        ),
    )
    state = reduce_session_runtime_state(
        state,
        SessionRuntimeObservation(
            source=SessionRuntimeObservationSource.RUNTIME,
            signal=SessionRuntimeSignal.COMPACTION_STARTED,
            observed_at_ms=3,
        ),
    )
    assert state is SessionRuntimeState.COMPACTION_STARTING
    state = reduce_session_runtime_state(
        state,
        SessionRuntimeObservation(
            source=SessionRuntimeObservationSource.RUNTIME,
            signal=SessionRuntimeSignal.COMPACTION_IN_PROGRESS,
            observed_at_ms=4,
        ),
    )
    assert state is SessionRuntimeState.COMPACTING
    state = reduce_session_runtime_state(
        state,
        SessionRuntimeObservation(
            source=SessionRuntimeObservationSource.RUNTIME,
            signal=SessionRuntimeSignal.COMPACTION_COMPLETED,
            observed_at_ms=5,
        ),
    )
    assert state is SessionRuntimeState.COMPACTION_COMPLETED

    fallback = reduce_session_runtime_state(
        SessionRuntimeState.CREATED,
        SessionRuntimeObservation(
            source=SessionRuntimeObservationSource.CHANNEL,
            signal=SessionRuntimeSignal.WORKING_OBSERVED,
            observed_at_ms=2,
        ),
    )
    assert fallback is SessionRuntimeState.WORKING


def test_outbound_delivery_tracks_only_provider_attempt_states() -> None:
    outbound = make_outbound_message()
    assert outbound.sender_kind is SenderKind.AGENT
    outbound = outbound.transition_to(
        OutboundDeliveryState.QUEUED,
        at_ms=3,
        provider_receipt_ref="queue-receipt-1",
    )
    assert outbound.delivery_state is OutboundDeliveryState.QUEUED
    assert outbound.completed_at_ms is None
    outbound = outbound.transition_to(
        OutboundDeliveryState.SENT,
        at_ms=4,
        provider_message_id="provider-message-1",
    )

    assert outbound.delivery_state is OutboundDeliveryState.SENT
    assert outbound.completed_at_ms == 4
