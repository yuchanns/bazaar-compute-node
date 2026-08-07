from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Self

from .states import (
    BCN_SESSION_TRANSITIONS,
    CHANNEL_SESSION_TRANSITIONS,
    FRESH_CHECK_TRANSITIONS,
    OUTBOUND_DELIVERY_TRANSITIONS,
    RUNTIME_PROCESS_TRANSITIONS,
    RUNTIME_TURN_TRANSITIONS,
    ApprovalDecision,
    BcnSessionState,
    ChannelSessionState,
    FreshCheckState,
    OutboundDeliveryState,
    RuntimeEventState,
    RuntimeProcessState,
    RuntimeTurnState,
    ensure_transition,
)

Metadata = Mapping[str, object]


def _validate_text(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")


def _validate_non_negative(value: int, field_name: str) -> None:
    if not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class ChannelSession:
    channel_session_id: str
    channel_slug: str
    provider_conversation_key: str
    provider_thread_key: str
    state: ChannelSessionState
    created_at_ms: int
    updated_at_ms: int
    following: bool = True
    last_inbound_at_ms: int | None = None
    last_outbound_at_ms: int | None = None
    metadata: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_text(self.channel_session_id, "channel_session_id")
        _validate_text(self.channel_slug, "channel_slug")
        _validate_text(self.provider_conversation_key, "provider_conversation_key")
        if not isinstance(self.provider_thread_key, str):
            raise TypeError("provider_thread_key must be a string")
        _validate_non_negative(self.created_at_ms, "created_at_ms")
        _validate_non_negative(self.updated_at_ms, "updated_at_ms")
        if self.last_inbound_at_ms is not None:
            _validate_non_negative(self.last_inbound_at_ms, "last_inbound_at_ms")
        if self.last_outbound_at_ms is not None:
            _validate_non_negative(self.last_outbound_at_ms, "last_outbound_at_ms")

    def transition_to(self, state: ChannelSessionState, *, updated_at_ms: int) -> Self:
        _validate_non_negative(updated_at_ms, "updated_at_ms")
        ensure_transition(
            "channel_session", self.state, state, CHANNEL_SESSION_TRANSITIONS
        )
        if state is self.state:
            return self
        return replace(self, state=state, updated_at_ms=updated_at_ms)


@dataclass(frozen=True, slots=True)
class BcnSession:
    bcn_session_id: str
    channel_session_id: str
    workspace_id: str
    state: BcnSessionState
    created_at_ms: int
    updated_at_ms: int
    last_activity_at_ms: int | None = None
    stopped_at_ms: int | None = None
    metadata: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_text(self.bcn_session_id, "bcn_session_id")
        _validate_text(self.channel_session_id, "channel_session_id")
        _validate_text(self.workspace_id, "workspace_id")
        _validate_non_negative(self.created_at_ms, "created_at_ms")
        _validate_non_negative(self.updated_at_ms, "updated_at_ms")
        if self.last_activity_at_ms is not None:
            _validate_non_negative(self.last_activity_at_ms, "last_activity_at_ms")
        if self.stopped_at_ms is not None:
            _validate_non_negative(self.stopped_at_ms, "stopped_at_ms")

    def transition_to(self, state: BcnSessionState, *, updated_at_ms: int) -> Self:
        _validate_non_negative(updated_at_ms, "updated_at_ms")
        ensure_transition("bcn_session", self.state, state, BCN_SESSION_TRANSITIONS)
        if state is self.state:
            return self
        stopped_at_ms = (
            updated_at_ms if state is BcnSessionState.STOPPED else self.stopped_at_ms
        )
        return replace(
            self,
            state=state,
            updated_at_ms=updated_at_ms,
            stopped_at_ms=stopped_at_ms,
        )


@dataclass(frozen=True, slots=True)
class RuntimeSession:
    agent_runtime_session_id: str
    bcn_session_id: str
    channel_session_id: str
    runtime_slug: str
    workspace_id: str
    process_state: RuntimeProcessState
    created_at_ms: int
    updated_at_ms: int
    provider_thread_id: str | None = None
    process_id: int | None = None
    started_at_ms: int | None = None
    stopped_at_ms: int | None = None
    last_reconciled_at_ms: int | None = None
    last_error_kind: str | None = None
    last_error_message: str | None = None
    metadata: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_text(self.agent_runtime_session_id, "agent_runtime_session_id")
        _validate_text(self.bcn_session_id, "bcn_session_id")
        _validate_text(self.channel_session_id, "channel_session_id")
        _validate_text(self.runtime_slug, "runtime_slug")
        _validate_text(self.workspace_id, "workspace_id")
        _validate_non_negative(self.created_at_ms, "created_at_ms")
        _validate_non_negative(self.updated_at_ms, "updated_at_ms")
        if self.process_id is not None and self.process_id < 0:
            raise ValueError("process_id must be non-negative")
        for value, field_name in (
            (self.started_at_ms, "started_at_ms"),
            (self.stopped_at_ms, "stopped_at_ms"),
            (self.last_reconciled_at_ms, "last_reconciled_at_ms"),
        ):
            if value is not None:
                _validate_non_negative(value, field_name)
        if self.provider_thread_id is not None:
            _validate_text(self.provider_thread_id, "provider_thread_id")

    def transition_process_to(
        self,
        state: RuntimeProcessState,
        *,
        updated_at_ms: int,
        error_kind: str | None = None,
        error_message: str | None = None,
    ) -> Self:
        _validate_non_negative(updated_at_ms, "updated_at_ms")
        ensure_transition(
            "runtime_process", self.process_state, state, RUNTIME_PROCESS_TRANSITIONS
        )
        if state is self.process_state:
            return self
        started_at_ms = (
            updated_at_ms
            if state is RuntimeProcessState.RUNNING and self.started_at_ms is None
            else self.started_at_ms
        )
        stopped_at_ms = (
            updated_at_ms
            if state is RuntimeProcessState.STOPPED
            else self.stopped_at_ms
        )
        last_reconciled_at_ms = (
            updated_at_ms
            if state is RuntimeProcessState.RECONCILING
            else self.last_reconciled_at_ms
        )
        return replace(
            self,
            process_state=state,
            updated_at_ms=updated_at_ms,
            started_at_ms=started_at_ms,
            stopped_at_ms=stopped_at_ms,
            last_reconciled_at_ms=last_reconciled_at_ms,
            last_error_kind=error_kind or self.last_error_kind,
            last_error_message=error_message or self.last_error_message,
        )


@dataclass(frozen=True, slots=True)
class RuntimeTurn:
    turn_id: str
    agent_runtime_session_id: str
    state: RuntimeTurnState
    started_at_ms: int
    provider_turn_id: str | None = None
    client_user_message_id: str | None = None
    completed_at_ms: int | None = None
    latest_event_name: str | None = None
    error_kind: str | None = None
    error_message: str | None = None
    metadata: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_text(self.turn_id, "turn_id")
        _validate_text(self.agent_runtime_session_id, "agent_runtime_session_id")
        _validate_non_negative(self.started_at_ms, "started_at_ms")
        if self.provider_turn_id is not None:
            _validate_text(self.provider_turn_id, "provider_turn_id")
        if self.client_user_message_id is not None:
            _validate_text(self.client_user_message_id, "client_user_message_id")
        if self.completed_at_ms is not None:
            _validate_non_negative(self.completed_at_ms, "completed_at_ms")

    def transition_to(
        self,
        state: RuntimeTurnState,
        *,
        at_ms: int,
        error_kind: str | None = None,
        error_message: str | None = None,
        latest_event_name: str | None = None,
    ) -> Self:
        _validate_non_negative(at_ms, "at_ms")
        ensure_transition("runtime_turn", self.state, state, RUNTIME_TURN_TRANSITIONS)
        if state is self.state:
            return self
        completed_at_ms = (
            at_ms
            if state
            in {
                RuntimeTurnState.COMPLETED,
                RuntimeTurnState.FAILED,
                RuntimeTurnState.CANCELLED,
            }
            else self.completed_at_ms
        )
        return replace(
            self,
            state=state,
            completed_at_ms=completed_at_ms,
            latest_event_name=latest_event_name or self.latest_event_name,
            error_kind=error_kind or self.error_kind,
            error_message=error_message or self.error_message,
        )


@dataclass(frozen=True, slots=True)
class InboundMessage:
    seq: int
    message_id: str
    bcn_session_id: str
    channel_session_id: str
    channel_slug: str
    provider_message_id: str
    received_at_ms: int
    sender_id: str
    sender_display_name: str
    message_type: str
    canonical_target: str
    body: str
    provider_time_ms: int | None = None
    provider_thread_id: str | None = None
    reply_to_provider_message_id: str | None = None
    provider_payload_ref: str | None = None
    metadata: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_non_negative(self.seq, "seq")
        for value, field_name in (
            (self.message_id, "message_id"),
            (self.bcn_session_id, "bcn_session_id"),
            (self.channel_session_id, "channel_session_id"),
            (self.channel_slug, "channel_slug"),
            (self.provider_message_id, "provider_message_id"),
            (self.sender_id, "sender_id"),
            (self.sender_display_name, "sender_display_name"),
            (self.message_type, "message_type"),
            (self.canonical_target, "canonical_target"),
        ):
            _validate_text(value, field_name)
        _validate_non_negative(self.received_at_ms, "received_at_ms")
        if self.provider_time_ms is not None:
            _validate_non_negative(self.provider_time_ms, "provider_time_ms")


@dataclass(frozen=True, slots=True)
class OutboundMessage:
    outbound_message_id: str
    command_id: str
    bcn_session_id: str
    channel_session_id: str
    target: str
    body: str
    state: OutboundDeliveryState
    fresh_check_state: FreshCheckState
    created_at_ms: int
    snapshot_seq: int | None = None
    current_inbound_seq: int | None = None
    provider_message_id: str | None = None
    provider_receipt_ref: str | None = None
    provider_attempted_at_ms: int | None = None
    completed_at_ms: int | None = None
    draft_saved_at_ms: int | None = None
    error_kind: str | None = None
    error_message: str | None = None
    next_action: str | None = None
    metadata: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.outbound_message_id, "outbound_message_id"),
            (self.command_id, "command_id"),
            (self.bcn_session_id, "bcn_session_id"),
            (self.channel_session_id, "channel_session_id"),
            (self.target, "target"),
        ):
            _validate_text(value, field_name)
        _validate_non_negative(self.created_at_ms, "created_at_ms")
        for value, field_name in (
            (self.snapshot_seq, "snapshot_seq"),
            (self.current_inbound_seq, "current_inbound_seq"),
            (self.provider_attempted_at_ms, "provider_attempted_at_ms"),
            (self.completed_at_ms, "completed_at_ms"),
            (self.draft_saved_at_ms, "draft_saved_at_ms"),
        ):
            if value is not None:
                _validate_non_negative(value, field_name)

    def record_fresh_check(
        self,
        state: FreshCheckState,
        *,
        snapshot_seq: int | None,
        current_inbound_seq: int | None,
    ) -> Self:
        if snapshot_seq is not None:
            _validate_non_negative(snapshot_seq, "snapshot_seq")
        if current_inbound_seq is not None:
            _validate_non_negative(current_inbound_seq, "current_inbound_seq")
        if state is FreshCheckState.PASSED:
            if snapshot_seq is None or current_inbound_seq is None:
                raise ValueError(
                    "a passed fresh check requires both sequence boundaries"
                )
            if current_inbound_seq > snapshot_seq:
                raise ValueError(
                    "a passed fresh check cannot observe a newer inbound sequence"
                )
        ensure_transition(
            "fresh_check",
            self.fresh_check_state,
            state,
            FRESH_CHECK_TRANSITIONS,
        )
        return replace(
            self,
            fresh_check_state=state,
            snapshot_seq=snapshot_seq,
            current_inbound_seq=current_inbound_seq,
        )

    def transition_to(
        self,
        state: OutboundDeliveryState,
        *,
        at_ms: int,
        provider_message_id: str | None = None,
        provider_receipt_ref: str | None = None,
        error_kind: str | None = None,
        error_message: str | None = None,
        next_action: str | None = None,
    ) -> Self:
        _validate_non_negative(at_ms, "at_ms")
        ensure_transition(
            "outbound_delivery", self.state, state, OUTBOUND_DELIVERY_TRANSITIONS
        )
        if state is self.state:
            return self
        if (
            state
            in {
                OutboundDeliveryState.PENDING,
                OutboundDeliveryState.SENT,
            }
            and self.fresh_check_state is not FreshCheckState.PASSED
        ):
            raise ValueError("outbound delivery requires a passed fresh check")
        completed_at_ms = (
            at_ms
            if state
            in {
                OutboundDeliveryState.SENT,
                OutboundDeliveryState.FAILED,
                OutboundDeliveryState.UNKNOWN,
                OutboundDeliveryState.REJECTED,
            }
            else self.completed_at_ms
        )
        draft_saved_at_ms = (
            at_ms if state is OutboundDeliveryState.REJECTED else self.draft_saved_at_ms
        )
        return replace(
            self,
            state=state,
            provider_message_id=provider_message_id or self.provider_message_id,
            provider_receipt_ref=provider_receipt_ref or self.provider_receipt_ref,
            completed_at_ms=completed_at_ms,
            draft_saved_at_ms=draft_saved_at_ms,
            error_kind=error_kind or self.error_kind,
            error_message=error_message or self.error_message,
            next_action=next_action or self.next_action,
        )


@dataclass(frozen=True, slots=True)
class ConsumerCursor:
    bcn_session_id: str
    delivered_through_seq: int = 0
    inbox_snapshot_seq: int | None = None
    inbox_snapshot_source: str | None = None
    inbox_snapshot_at_ms: int | None = None
    last_check_at_ms: int | None = None
    last_read_at_ms: int | None = None
    updated_at_ms: int = 0

    def __post_init__(self) -> None:
        _validate_text(self.bcn_session_id, "bcn_session_id")
        _validate_non_negative(self.delivered_through_seq, "delivered_through_seq")
        _validate_non_negative(self.updated_at_ms, "updated_at_ms")
        for value, field_name in (
            (self.inbox_snapshot_seq, "inbox_snapshot_seq"),
            (self.inbox_snapshot_at_ms, "inbox_snapshot_at_ms"),
            (self.last_check_at_ms, "last_check_at_ms"),
            (self.last_read_at_ms, "last_read_at_ms"),
        ):
            if value is not None:
                _validate_non_negative(value, field_name)
        if (
            self.inbox_snapshot_seq is not None
            and self.inbox_snapshot_seq < self.delivered_through_seq
        ):
            raise ValueError("inbox_snapshot_seq cannot precede delivered_through_seq")


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    request_id: str
    bcn_session_id: str
    agent_runtime_session_id: str
    action: str
    created_at_ms: int
    turn_id: str | None = None
    metadata: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.request_id, "request_id"),
            (self.bcn_session_id, "bcn_session_id"),
            (self.agent_runtime_session_id, "agent_runtime_session_id"),
            (self.action, "action"),
        ):
            _validate_text(value, field_name)
        _validate_non_negative(self.created_at_ms, "created_at_ms")
        if self.turn_id is not None:
            _validate_text(self.turn_id, "turn_id")


@dataclass(frozen=True, slots=True)
class ApprovalResult:
    request_id: str
    decision: ApprovalDecision
    decided_at_ms: int
    reason: str | None = None

    def __post_init__(self) -> None:
        _validate_text(self.request_id, "request_id")
        _validate_non_negative(self.decided_at_ms, "decided_at_ms")


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    event_seq: int
    event_id: str
    created_at_ms: int
    level: str
    event_name: str
    state: RuntimeEventState
    duration_ms: int | None = None
    node_id: str | None = None
    channel_slug: str | None = None
    channel_session_id: str | None = None
    bcn_session_id: str | None = None
    agent_runtime_session_id: str | None = None
    turn_id: str | None = None
    request_id: str | None = None
    command_id: str | None = None
    inbound_seq: int | None = None
    outbound_message_id: str | None = None
    error_kind: str | None = None
    error_type: str | None = None
    error_message: str | None = None
    traceback_ref: str | None = None
    runtime_slug: str | None = None
    metadata: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_non_negative(self.event_seq, "event_seq")
        _validate_text(self.event_id, "event_id")
        _validate_non_negative(self.created_at_ms, "created_at_ms")
        _validate_text(self.level, "level")
        _validate_text(self.event_name, "event_name")
        for value, field_name in (
            (self.duration_ms, "duration_ms"),
            (self.inbound_seq, "inbound_seq"),
        ):
            if value is not None:
                _validate_non_negative(value, field_name)
