from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import PurePosixPath
from string import hexdigits
from typing import Self, cast

from .states import (
    OUTBOUND_DELIVERY_TRANSITIONS,
    RUNTIME_TURN_TRANSITIONS,
    ApprovalDecision,
    ChannelTargetKind,
    MessageDirection,
    OutboundDeliveryState,
    RuntimeTurnState,
    SenderKind,
    SystemMessageKind,
    ensure_transition,
)

Metadata = Mapping[str, object]


@dataclass(frozen=True, slots=True)
class ChannelTargetPresentation:
    display_name: str | None = None
    handle: str | None = None


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
    target_display_name: str | None = None
    target_handle: str | None = None
    target_handle_key: str | None = None
    metadata: Metadata = field(default_factory=dict)

    @property
    def canonical_target(self) -> str:
        return f"{self.target_kind.value}:{self.id}"

    def with_target_presentation(
        self,
        presentation: ChannelTargetPresentation,
        *,
        updated_at_ms: int,
    ) -> Self:
        if self.target_kind is ChannelTargetKind.GROUP:
            return replace(
                self,
                target_display_name=presentation.display_name,
                target_handle=None,
                target_handle_key=None,
                updated_at_ms=updated_at_ms,
            )
        handle = presentation.handle
        return replace(
            self,
            target_display_name=None,
            target_handle=handle,
            target_handle_key=handle.casefold() if handle is not None else None,
            updated_at_ms=updated_at_ms,
        )

    def display_target(self, *, handle_is_unique: bool) -> str:
        if (
            self.target_kind is ChannelTargetKind.GROUP
            and self.target_display_name is not None
        ):
            return f"#{self.target_display_name}:{self.id}"
        if (
            self.target_kind is ChannelTargetKind.DM
            and self.target_handle is not None
            and handle_is_unique
        ):
            return f"dm:@{self.target_handle}"
        return self.canonical_target


@dataclass(frozen=True, slots=True)
class BcnSession:
    id: str
    channel_session_id: str
    workspace_id: str
    created_at_ms: int
    updated_at_ms: int
    last_activity_at_ms: int | None = None
    metadata: Metadata = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RuntimeSession:
    id: str
    bcn_session_id: str
    channel_session_id: str
    runtime: str
    runtime_index: int
    workspace_id: str
    created_at_ms: int
    updated_at_ms: int
    provider_thread_id: str | None = None
    metadata: Metadata = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RuntimeAttempt:
    turn_id: str
    session_id: str
    client_user_message_id: str
    started_at_ms: int


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
    display_name: str | None = None

    def __post_init__(self) -> None:
        if self.id is None and self.name is None:
            raise ValueError("sender identity requires an id or name")

    @property
    def handle(self) -> str:
        """Return what this sender is addressed by, falling back to the id."""

        if self.name is not None:
            return self.name
        if self.id is None:
            raise RuntimeError("sender identity has no display value")
        return self.id

    @property
    def label(self) -> str:
        """Return the shortest thing a reader would recognise this sender by.

        A provider that offers no handle can still offer a human name, and a
        name says more than the opaque id it would otherwise fall back to.
        """

        if self.name is not None:
            return self.name
        if self.display_name is not None:
            return self.display_name
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
        latest_fields = (
            self.latest_sender,
            self.latest_provider_time_ms,
            self.latest_received_at_ms,
        )
        if self.latest_message_id is None:
            if any(value is not None for value in latest_fields):
                raise ValueError("latest message fields require latest_message_id")
            return

        if self.latest_received_at_ms is None:
            raise ValueError("latest_received_at_ms is required with latest_message_id")


@dataclass(frozen=True, slots=True)
class Message[AttachmentT: InboundAttachment | OutboundAttachment]:
    direction: MessageDirection
    seq: int
    message_id: str
    session_id: str
    channel_session_id: str
    target: str
    body: str
    sender: SenderIdentity | None = None
    message_type: str = "text"
    target_kind: ChannelTargetKind = ChannelTargetKind.DM
    target_presentation: ChannelTargetPresentation | None = None
    attachments: tuple[AttachmentT, ...] = ()
    reply_to_message_id: str | None = None
    channel: str | None = None
    provider_thread_id: str | None = None
    provider_message_id: str | None = None
    provider_time_ms: int | None = None
    received_at_ms: int | None = None
    mentions_agent: bool = False
    notifies_runtime: bool = True
    provider_payload_ref: str | None = None
    command_id: str | None = None
    delivery_state: OutboundDeliveryState | None = None
    created_at_ms: int | None = None
    provider_attempted_at_ms: int | None = None
    provider_receipt_ref: str | None = None
    completed_at_ms: int | None = None
    error_kind: str | None = None
    error_message: str | None = None
    metadata: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.direction is MessageDirection.INBOUND:
            self._validate_inbound()
        else:
            self._validate_outbound()

    def _validate_inbound(self) -> None:
        sender_kind = self.sender_kind
        _ = self.system_message_kind
        if sender_kind is not SenderKind.SYSTEM and self.provider_message_id is None:
            raise ValueError(
                "provider_message_id is required for provider inbound messages"
            )
        if self.received_at_ms is None:
            raise ValueError("received_at_ms is required for inbound messages")
        outbound_fields = (
            self.command_id,
            self.delivery_state,
            self.created_at_ms,
            self.provider_attempted_at_ms,
            self.provider_receipt_ref,
            self.completed_at_ms,
            self.error_kind,
            self.error_message,
        )
        if any(value is not None for value in outbound_fields):
            raise ValueError("inbound messages cannot contain outbound delivery fields")

    def _validate_outbound(self) -> None:
        _ = self.system_message_kind
        for value, field_name in (
            (self.command_id, "command_id"),
            (self.delivery_state, "delivery_state"),
            (self.created_at_ms, "created_at_ms"),
            (self.provider_attempted_at_ms, "provider_attempted_at_ms"),
        ):
            if value is None:
                raise ValueError(f"{field_name} is required for outbound messages")
        created_at_ms = cast(int, self.created_at_ms)
        provider_attempted_at_ms = cast(int, self.provider_attempted_at_ms)
        if provider_attempted_at_ms < created_at_ms:
            raise ValueError("provider attempt cannot precede creation")

    @property
    def sender_kind(self) -> SenderKind:
        if self.direction is MessageDirection.OUTBOUND:
            return SenderKind.AGENT
        value = self.metadata.get("sender_kind", SenderKind.UNKNOWN.value)
        try:
            return SenderKind(cast(str, value))
        except ValueError as error:
            raise ValueError(
                "metadata sender_kind must be human, agent, system, or unknown"
            ) from error

    @property
    def system_message_kind(self) -> SystemMessageKind | None:
        value = self.metadata.get("system_message_kind")
        if self.sender_kind is not SenderKind.SYSTEM:
            if value is not None:
                raise ValueError(
                    "only system messages can have metadata system_message_kind"
                )
            return None
        try:
            return SystemMessageKind(cast(str, value))
        except ValueError as error:
            raise ValueError(
                "metadata system_message_kind must be reminder or handoff"
            ) from error

    def inbound_identity(self) -> tuple[str, str, str]:
        if self.direction is not MessageDirection.INBOUND:
            raise ValueError("only inbound messages have provider identity")
        if self.sender_kind is SenderKind.SYSTEM:
            raise ValueError("system messages do not have provider identity")
        channel = self.channel
        provider_thread_id = self.provider_thread_id
        provider_message_id = self.provider_message_id
        if channel is None or provider_thread_id is None or provider_message_id is None:
            raise RuntimeError("inbound message identity is incomplete")
        return channel, provider_thread_id, provider_message_id

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
        if self.direction is not MessageDirection.OUTBOUND:
            raise ValueError("only outbound messages have delivery transitions")
        if self.delivery_state is None:
            raise RuntimeError("outbound message has no delivery state")
        ensure_transition(
            "outbound_delivery",
            self.delivery_state,
            state,
            OUTBOUND_DELIVERY_TRANSITIONS,
        )
        if state is self.delivery_state:
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
            delivery_state=state,
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
    last_check_at_ms: int | None = None
    last_read_at_ms: int | None = None
    updated_at_ms: int = 0


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    request_id: str
    session_id: str
    runtime_session_id: str
    action: str
    created_at_ms: int
    turn_id: str | None = None
    details: Mapping[str, str] = field(default_factory=dict)
    metadata: Metadata = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ApprovalResult:
    request_id: str
    decision: ApprovalDecision
    decided_at_ms: int
    reason: str | None = None
