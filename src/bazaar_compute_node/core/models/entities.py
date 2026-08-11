from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Self

from .states import (
    FRESH_CHECK_TRANSITIONS,
    OUTBOUND_DELIVERY_TRANSITIONS,
    RUNTIME_TURN_TRANSITIONS,
    ApprovalDecision,
    ChannelTargetKind,
    FreshCheckState,
    OutboundDeliveryState,
    RuntimeEventState,
    RuntimeTurnState,
    StreamEventKind,
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
    id: str
    channel: str
    provider_thread_id: str
    created_at_ms: int
    updated_at_ms: int
    target_kind: ChannelTargetKind = ChannelTargetKind.DM
    following: bool = True
    last_inbound_at_ms: int | None = None
    last_outbound_at_ms: int | None = None
    metadata: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_text(self.id, "id")
        _validate_text(self.channel, "channel")
        _validate_text(self.provider_thread_id, "provider_thread_id")
        _validate_non_negative(self.created_at_ms, "created_at_ms")
        _validate_non_negative(self.updated_at_ms, "updated_at_ms")
        if not isinstance(self.target_kind, ChannelTargetKind):
            raise TypeError("target_kind must be a ChannelTargetKind")
        if self.last_inbound_at_ms is not None:
            _validate_non_negative(self.last_inbound_at_ms, "last_inbound_at_ms")
        if self.last_outbound_at_ms is not None:
            _validate_non_negative(self.last_outbound_at_ms, "last_outbound_at_ms")


@dataclass(frozen=True, slots=True)
class BcnSession:
    id: str
    channel_session_id: str
    workspace_id: str
    created_at_ms: int
    updated_at_ms: int
    last_activity_at_ms: int | None = None
    metadata: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_text(self.id, "id")
        _validate_text(self.channel_session_id, "channel_session_id")
        _validate_text(self.workspace_id, "workspace_id")
        _validate_non_negative(self.created_at_ms, "created_at_ms")
        _validate_non_negative(self.updated_at_ms, "updated_at_ms")
        if self.last_activity_at_ms is not None:
            _validate_non_negative(self.last_activity_at_ms, "last_activity_at_ms")


@dataclass(frozen=True, slots=True)
class RuntimeSession:
    id: str
    bcn_session_id: str
    channel_session_id: str
    runtime: str
    workspace_id: str
    created_at_ms: int
    updated_at_ms: int
    provider_thread_id: str | None = None
    metadata: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_text(self.id, "id")
        _validate_text(self.bcn_session_id, "bcn_session_id")
        _validate_text(self.channel_session_id, "channel_session_id")
        _validate_text(self.runtime, "runtime")
        _validate_text(self.workspace_id, "workspace_id")
        _validate_non_negative(self.created_at_ms, "created_at_ms")
        _validate_non_negative(self.updated_at_ms, "updated_at_ms")
        if self.provider_thread_id is not None:
            _validate_text(self.provider_thread_id, "provider_thread_id")


@dataclass(frozen=True, slots=True)
class RuntimeAttempt:
    turn_id: str
    session_id: str
    client_user_message_id: str
    started_at_ms: int

    def __post_init__(self) -> None:
        _validate_text(self.turn_id, "turn_id")
        _validate_text(self.session_id, "session_id")
        _validate_text(self.client_user_message_id, "client_user_message_id")
        _validate_non_negative(self.started_at_ms, "started_at_ms")


@dataclass(frozen=True, slots=True)
class RuntimeTurn:
    turn_id: str
    session_id: str
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
        _validate_text(self.session_id, "session_id")
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
                RuntimeTurnState.UNKNOWN,
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
class InboundAttachment:
    attachment_id: str
    name: str
    kind: str
    state: str
    media_type: str | None = None
    relative_path: str | None = None
    size_bytes: int | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.attachment_id, "attachment_id"),
            (self.name, "name"),
            (self.kind, "kind"),
            (self.state, "state"),
        ):
            _validate_text(value, field_name)
        if self.state not in {"ready", "failed"}:
            raise ValueError("attachment state must be ready or failed")
        if self.state == "ready" and self.relative_path is None:
            raise ValueError("ready attachment must have a relative path")
        if self.state == "failed" and self.relative_path is not None:
            raise ValueError("failed attachment cannot have a relative path")
        if self.size_bytes is not None:
            _validate_non_negative(self.size_bytes, "size_bytes")


@dataclass(frozen=True, slots=True)
class InboundMessage:
    seq: int
    message_id: str
    session_id: str
    channel_session_id: str
    channel: str
    provider_thread_id: str
    provider_message_id: str
    received_at_ms: int
    sender: str | None
    message_type: str
    canonical_target: str
    body: str
    target_kind: ChannelTargetKind = ChannelTargetKind.DM
    mentions_agent: bool = False
    notifies_runtime: bool = True
    attachments: tuple[InboundAttachment, ...] = ()
    provider_time_ms: int | None = None
    reply_to_message_id: str | None = None
    provider_payload_ref: str | None = None
    metadata: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_non_negative(self.seq, "seq")
        for value, field_name in (
            (self.message_id, "message_id"),
            (self.session_id, "session_id"),
            (self.channel_session_id, "channel_session_id"),
            (self.channel, "channel"),
            (self.provider_thread_id, "provider_thread_id"),
            (self.provider_message_id, "provider_message_id"),
            (self.message_type, "message_type"),
            (self.canonical_target, "canonical_target"),
        ):
            _validate_text(value, field_name)
        _validate_non_negative(self.received_at_ms, "received_at_ms")
        if self.sender is not None:
            _validate_text(self.sender, "sender")
        if self.reply_to_message_id is not None:
            _validate_text(self.reply_to_message_id, "reply_to_message_id")
        if not isinstance(self.target_kind, ChannelTargetKind):
            raise TypeError("target_kind must be a ChannelTargetKind")
        if self.provider_time_ms is not None:
            _validate_non_negative(self.provider_time_ms, "provider_time_ms")


@dataclass(frozen=True, slots=True)
class OutboundMessage:
    outbound_message_id: str
    command_id: str
    session_id: str
    channel_session_id: str
    target: str
    body: str
    state: OutboundDeliveryState
    fresh_check_state: FreshCheckState
    created_at_ms: int
    reply_to_message_id: str | None = None
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
            (self.session_id, "session_id"),
            (self.channel_session_id, "channel_session_id"),
            (self.target, "target"),
        ):
            _validate_text(value, field_name)
        _validate_non_negative(self.created_at_ms, "created_at_ms")
        for value, field_name in ((self.reply_to_message_id, "reply_to_message_id"),):
            if value is not None:
                _validate_text(value, field_name)
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
        save_draft: bool = True,
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
                OutboundDeliveryState.QUEUED,
                OutboundDeliveryState.SENT,
                OutboundDeliveryState.PARTIAL,
            }
            and self.fresh_check_state is not FreshCheckState.PASSED
        ):
            raise ValueError("outbound delivery requires a passed fresh check")
        completed_at_ms = (
            at_ms
            if state
            in {
                OutboundDeliveryState.SENT,
                OutboundDeliveryState.PARTIAL,
                OutboundDeliveryState.FAILED,
                OutboundDeliveryState.UNKNOWN,
                OutboundDeliveryState.REJECTED,
            }
            else self.completed_at_ms
        )
        draft_saved_at_ms = (
            at_ms
            if state is OutboundDeliveryState.REJECTED and save_draft
            else self.draft_saved_at_ms
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
    session_id: str
    delivered_through_seq: int = 0
    inbox_snapshot_seq: int | None = None
    inbox_snapshot_source: str | None = None
    inbox_snapshot_at_ms: int | None = None
    last_check_at_ms: int | None = None
    last_read_at_ms: int | None = None
    updated_at_ms: int = 0

    def __post_init__(self) -> None:
        _validate_text(self.session_id, "session_id")
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
    session_id: str
    runtime_session_id: str
    action: str
    created_at_ms: int
    turn_id: str | None = None
    metadata: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.request_id, "request_id"),
            (self.session_id, "session_id"),
            (self.runtime_session_id, "runtime_session_id"),
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
class StreamEvent:
    kind: StreamEventKind
    created_at_ms: int
    session_id: str
    stream_id: str | None = None
    content: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, StreamEventKind):
            raise TypeError("kind must be a StreamEventKind")
        _validate_non_negative(self.created_at_ms, "created_at_ms")
        _validate_text(self.session_id, "session_id")
        if self.stream_id is not None:
            _validate_text(self.stream_id, "stream_id")
        if self.content is not None and not isinstance(self.content, str):
            raise TypeError("content must be a string when present")


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
    channel: str | None = None
    channel_session_id: str | None = None
    bcn_session_id: str | None = None
    runtime_session_id: str | None = None
    turn_id: str | None = None
    request_id: str | None = None
    command_id: str | None = None
    inbound_seq: int | None = None
    outbound_message_id: str | None = None
    error_kind: str | None = None
    error_type: str | None = None
    error_message: str | None = None
    traceback_ref: str | None = None
    runtime: str | None = None
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
