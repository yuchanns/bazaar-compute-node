from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .actor import Thread
from .models import (
    InboundAttachment,
    InboxTargetSummary,
    Message,
    OutboundAttachment,
    SenderIdentity,
)
from .states import ChannelTargetKind, MessageDirection, SenderKind


@dataclass(frozen=True, slots=True)
class TargetProjection:
    canonical_target: str
    display_target: str


@dataclass(frozen=True, slots=True)
class UnreadSummary:
    """How much an actor has unread, and the newest end of it."""

    total: int
    messages: tuple[Message[InboundAttachment], ...] = ()


@dataclass(frozen=True, slots=True)
class MessageCheckResult:
    """Drain result with a snapshot independent from the delivery cursor."""

    messages: tuple[Message[InboundAttachment], ...]
    snapshot_seq: int
    delivered_through_seq: int
    referenced_messages: tuple[
        Message[InboundAttachment | OutboundAttachment], ...
    ] = ()
    target_projections: tuple[TargetProjection, ...] = ()

    def __post_init__(self) -> None:
        if self.delivered_through_seq > self.snapshot_seq:
            raise ValueError("delivered_through_seq cannot exceed snapshot_seq")


@dataclass(frozen=True, slots=True)
class MessageReadResult:
    """Non-draining history result with the observed inbox snapshot."""

    messages: tuple[Message[InboundAttachment | OutboundAttachment], ...]
    snapshot_seq: int
    first_seq: int | None = None
    last_seq: int | None = None
    # whether the conversation goes on before the first message read
    has_before: bool = False
    referenced_messages: tuple[
        Message[InboundAttachment | OutboundAttachment], ...
    ] = ()
    target_projections: tuple[TargetProjection, ...] = ()

    def __post_init__(self) -> None:
        if (self.first_seq is None) != (self.last_seq is None):
            raise ValueError("first_seq and last_seq must be provided together")
        if (
            self.first_seq is not None
            and self.last_seq is not None
            and self.last_seq < self.first_seq
        ):
            raise ValueError("history sequence bounds are invalid")


@dataclass(frozen=True, slots=True)
class MessageSearchRequest:
    query: str = ""
    raw_target: str | None = None
    sender: str | None = None
    after_ms: int | None = None
    before_ms: int | None = None
    sort: Literal["time", "relevance"] = "time"
    limit: int = 20
    offset: int = 0


@dataclass(frozen=True, slots=True)
class MessageSearchHit:
    message_id: str
    thread_id: str
    target: str
    canonical_target: str
    channel: str
    target_kind: ChannelTargetKind
    direction: MessageDirection
    sender: SenderIdentity | None
    sender_kind: SenderKind
    at_ms: int
    body: str
    snippet: str


@dataclass(frozen=True, slots=True)
class MessageSearchResult:
    query: str
    sort: Literal["time", "relevance"]
    messages: tuple[MessageSearchHit, ...]
    offset: int
    has_more: bool
    next_offset: int | None

    @property
    def shown(self) -> int:
        return len(self.messages)


class MessageSearchTimeoutError(TimeoutError):
    code = "SEARCH_TIMEOUT"


@dataclass(frozen=True, slots=True)
class InboxListResult:
    """A non-draining, paginated catalog of targets owned by one agent."""

    targets: tuple[InboxTargetSummary, ...]
    total: int
    shown: int
    offset: int
    has_more: bool
    # how many conversations wait to be looked at, whichever were listed
    pending_review: int = 0

    def __post_init__(self) -> None:
        if self.shown != len(self.targets):
            raise ValueError("shown must equal the number of targets")
        if self.shown > self.total:
            raise ValueError("shown cannot exceed total")
        expected_has_more = self.offset + self.shown < self.total
        if self.has_more != expected_has_more:
            raise ValueError("has_more does not match pagination bounds")


@dataclass(frozen=True, slots=True)
class MessageDraft:
    """One process-local outbound payload owned by the target it addresses."""

    target: str
    actor: Thread
    body: str
    attachments: tuple[OutboundAttachment, ...]
    reply_to_message_id: str | None
    created_at_ms: int


@dataclass(frozen=True, slots=True)
class OutboundFreshnessPass:
    """Stable source boundary used by target-owned outbound materialization."""

    current_inbound_seq: int


@dataclass(frozen=True, slots=True)
class MessageSendFreshnessHold:
    """Bounded context returned before an outbound provider attempt."""

    target: str
    messages: tuple[Message[InboundAttachment], ...]
    referenced_messages: tuple[Message[InboundAttachment | OutboundAttachment], ...]
    newer_message_total: int
    snapshot_seq: int | None
    current_inbound_seq: int
    draft_replaced: bool
    target_projections: tuple[TargetProjection, ...] = ()

    def __post_init__(self) -> None:
        if self.newer_message_total < len(self.messages):
            raise ValueError("newer_message_total cannot be smaller than messages")
        if (
            self.snapshot_seq is not None
            and self.snapshot_seq >= self.current_inbound_seq
        ):
            raise ValueError("freshness hold requires a stale snapshot boundary")


@dataclass(frozen=True, slots=True)
class MessageSendSuccess:
    message: Message[OutboundAttachment]
    target: str


@dataclass(frozen=True, slots=True)
class MessageBroadcast:
    """One send that named several conversations, with what became of each."""

    deliveries: tuple[MessageSendSuccess, ...]

    def __post_init__(self) -> None:
        if len(self.deliveries) < 2:
            raise ValueError("a broadcast names at least two conversations")


type MessageSendResult = (
    MessageSendSuccess | MessageSendFreshnessHold | MessageBroadcast
)


@dataclass(frozen=True, slots=True)
class ThreadUnfollowResult:
    target: str
    changed: bool


class ThreadNotFoundError(ValueError):
    """A command referenced a bcn session that is not persisted on this node."""
