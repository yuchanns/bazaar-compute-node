from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import PurePosixPath
from string import hexdigits
from typing import Self

from .states import (
    OUTBOUND_DELIVERY_TRANSITIONS,
    RUNTIME_TURN_TRANSITIONS,
    ApprovalDecision,
    ChannelTargetKind,
    OutboundDeliveryState,
    RuntimeEventState,
    RuntimeTurnState,
    SenderKind,
    StreamEventKind,
    ensure_transition,
)

Metadata = Mapping[str, object]


def _validate_text(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")


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
        if not isinstance(self.target_kind, ChannelTargetKind):
            raise TypeError("target_kind must be a ChannelTargetKind")


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
        if self.provider_turn_id is not None:
            _validate_text(self.provider_turn_id, "provider_turn_id")
        if self.client_user_message_id is not None:
            _validate_text(self.client_user_message_id, "client_user_message_id")

    def transition_to(
        self,
        state: RuntimeTurnState,
        *,
        at_ms: int,
        error_kind: str | None = None,
        error_message: str | None = None,
        latest_event_name: str | None = None,
    ) -> Self:
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


@dataclass(frozen=True, slots=True)
class OutboundAttachment:
    name: str
    relative_path: str
    media_type: str | None
    size_bytes: int
    sha256: str

    def __post_init__(self) -> None:
        _validate_text(self.name, "name")
        _validate_text(self.relative_path, "relative_path")
        path = PurePosixPath(self.relative_path)
        if (
            path.is_absolute()
            or "\\" in self.relative_path
            or ".." in path.parts
            or path == PurePosixPath(".")
        ):
            raise ValueError("relative_path must stay within the workspace")
        if path.name != self.name:
            raise ValueError("name must match the relative path basename")
        if self.media_type is not None:
            _validate_text(self.media_type, "media_type")
        if (
            len(self.sha256) != 64
            or self.sha256 != self.sha256.lower()
            or any(character not in hexdigits for character in self.sha256)
        ):
            raise ValueError("sha256 must be a lowercase hexadecimal digest")


@dataclass(frozen=True, slots=True)
class SenderIdentity:
    id: str | None = None
    name: str | None = None

    def __post_init__(self) -> None:
        if self.id is None and self.name is None:
            raise ValueError("sender identity requires an id or name")
        if self.id is not None:
            _validate_text(self.id, "sender.id")
        if self.name is not None:
            _validate_text(self.name, "sender.name")

    @property
    def display_name(self) -> str:
        if self.name is not None:
            return self.name
        if self.id is None:
            raise RuntimeError("sender identity has no display value")
        return self.id


@dataclass(frozen=True, slots=True)
class InboxTargetSummary:
    target: str
    session_id: str
    target_kind: ChannelTargetKind
    current: bool
    pending_count: int
    last_activity_at_ms: int
    latest_message_id: str | None = None
    latest_sender: SenderIdentity | None = None
    latest_provider_time_ms: int | None = None
    latest_received_at_ms: int | None = None

    def __post_init__(self) -> None:
        _validate_text(self.target, "target")
        _validate_text(self.session_id, "session_id")
        if not isinstance(self.target_kind, ChannelTargetKind):
            raise TypeError("target_kind must be a ChannelTargetKind")
        if not isinstance(self.current, bool):
            raise TypeError("current must be a bool")
        latest_fields = (
            self.latest_sender,
            self.latest_provider_time_ms,
            self.latest_received_at_ms,
        )
        if self.latest_message_id is None:
            if any(value is not None for value in latest_fields):
                raise ValueError("latest message fields require latest_message_id")
            return

        _validate_text(self.latest_message_id, "latest_message_id")
        if self.latest_sender is not None and not isinstance(
            self.latest_sender, SenderIdentity
        ):
            raise TypeError("latest_sender must be a SenderIdentity")
        if self.latest_received_at_ms is None:
            raise ValueError("latest_received_at_ms is required with latest_message_id")


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
    sender: SenderIdentity | None
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
        if self.sender is not None and not isinstance(self.sender, SenderIdentity):
            raise TypeError("sender must be a SenderIdentity")
        if self.reply_to_message_id is not None:
            _validate_text(self.reply_to_message_id, "reply_to_message_id")
        if not isinstance(self.target_kind, ChannelTargetKind):
            raise TypeError("target_kind must be a ChannelTargetKind")

    @property
    def sender_kind(self) -> SenderKind:
        value = self.metadata.get("sender_kind", SenderKind.UNKNOWN.value)
        if not isinstance(value, str):
            raise TypeError("metadata sender_kind must be a string")
        try:
            return SenderKind(value)
        except ValueError as error:
            raise ValueError(
                "metadata sender_kind must be human, agent, or unknown"
            ) from error


@dataclass(frozen=True, slots=True)
class OutboundMessage:
    outbound_message_id: str
    command_id: str
    session_id: str
    channel_session_id: str
    target: str
    body: str
    state: OutboundDeliveryState
    created_at_ms: int
    snapshot_seq: int
    current_inbound_seq: int
    provider_attempted_at_ms: int
    attachments: tuple[OutboundAttachment, ...] = ()
    reply_to_message_id: str | None = None
    provider_message_id: str | None = None
    provider_receipt_ref: str | None = None
    completed_at_ms: int | None = None
    error_kind: str | None = None
    error_message: str | None = None
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
        if not isinstance(self.attachments, tuple) or not all(
            isinstance(attachment, OutboundAttachment)
            for attachment in self.attachments
        ):
            raise TypeError("attachments must be a tuple of OutboundAttachment values")
        for value, field_name in ((self.reply_to_message_id, "reply_to_message_id"),):
            if value is not None:
                _validate_text(value, field_name)
        if self.current_inbound_seq > self.snapshot_seq:
            raise ValueError("current inbound sequence exceeds snapshot sequence")
        if self.provider_attempted_at_ms < self.created_at_ms:
            raise ValueError("provider attempt cannot precede creation")

    def transition_to(
        self,
        state: OutboundDeliveryState,
        *,
        at_ms: int,
        provider_message_id: str | None = None,
        provider_receipt_ref: str | None = None,
        error_kind: str | None = None,
        error_message: str | None = None,
    ) -> Self:
        ensure_transition(
            "outbound_delivery", self.state, state, OUTBOUND_DELIVERY_TRANSITIONS
        )
        if state is self.state:
            return self
        completed_at_ms = (
            at_ms
            if state
            in {
                OutboundDeliveryState.SENT,
                OutboundDeliveryState.PARTIAL,
                OutboundDeliveryState.FAILED,
                OutboundDeliveryState.UNKNOWN,
            }
            else self.completed_at_ms
        )
        return replace(
            self,
            state=state,
            provider_message_id=provider_message_id or self.provider_message_id,
            provider_receipt_ref=provider_receipt_ref or self.provider_receipt_ref,
            completed_at_ms=completed_at_ms,
            error_kind=error_kind or self.error_kind,
            error_message=error_message or self.error_message,
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
    description: str | None = None
    metadata: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.request_id, "request_id"),
            (self.session_id, "session_id"),
            (self.runtime_session_id, "runtime_session_id"),
            (self.action, "action"),
        ):
            _validate_text(value, field_name)
        if self.turn_id is not None:
            _validate_text(self.turn_id, "turn_id")
        if self.description is not None:
            _validate_text(self.description, "description")


@dataclass(frozen=True, slots=True)
class ApprovalResult:
    request_id: str
    decision: ApprovalDecision
    decided_at_ms: int
    reason: str | None = None

    def __post_init__(self) -> None:
        _validate_text(self.request_id, "request_id")


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
        _validate_text(self.session_id, "session_id")
        if self.stream_id is not None:
            _validate_text(self.stream_id, "stream_id")
        if self.content is not None and not isinstance(self.content, str):
            raise TypeError("content must be a string when present")


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    created_at_ms: int
    event_name: str
    state: RuntimeEventState
    turn_id: str | None = None
    error_kind: str | None = None
    error_message: str | None = None
    metadata: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_text(self.event_name, "event_name")
        if not isinstance(self.state, RuntimeEventState):
            raise TypeError("state must be a RuntimeEventState")
        for value, field_name in (
            (self.turn_id, "turn_id"),
            (self.error_kind, "error_kind"),
            (self.error_message, "error_message"),
        ):
            if value is not None:
                _validate_text(value, field_name)
