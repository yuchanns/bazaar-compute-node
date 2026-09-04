from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .actor import Actor
from .models import (
    InboundAttachment,
    InboxTargetSummary,
    Message,
    OutboundAttachment,
)
from .reminder import (
    ReminderCancelRequest,
    ReminderCancelResult,
    ReminderListRequest,
    ReminderListResult,
    ReminderScheduleRequest,
    ReminderScheduleResult,
    ReminderSnoozeRequest,
    ReminderSnoozeResult,
    ReminderUpdateRequest,
    ReminderUpdateResult,
)


@dataclass(frozen=True, slots=True)
class TargetProjection:
    canonical_target: str
    display_target: str


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
class InboxListResult:
    """A non-draining, paginated catalog of targets owned by one agent."""

    targets: tuple[InboxTargetSummary, ...]
    total: int
    shown: int
    offset: int
    has_more: bool

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
    target_id: str
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


type MessageSendResult = MessageSendSuccess | MessageSendFreshnessHold


@dataclass(frozen=True, slots=True)
class ThreadUnfollowResult:
    target: str
    changed: bool


class SessionNotFoundError(ValueError):
    """A command referenced a bcn session that is not persisted on this node."""


class ICommandService(Protocol):
    """Session-scoped command surface used by the local wrapper."""

    async def pending_targets(self, actor: Actor) -> InboxListResult:
        """List the conversations with unread messages, draining nothing."""
        ...

    async def check(self, actor: Actor) -> tuple[MessageCheckResult, ...]:
        """Read new messages and advance only the delivery cursor."""
        ...

    async def read(
        self,
        actor: Actor,
        *,
        raw_target: str,
        around_message_id: str | None = None,
        limit: int = 100,
    ) -> MessageReadResult:
        """Read history without advancing the delivery cursor."""
        ...

    async def send(
        self,
        *,
        actor: Actor,
        command_id: str,
        raw_target: str,
        body: str,
        created_at_ms: int,
        attachment_paths: tuple[str, ...] = (),
        reply_to_message_id: str | None = None,
        send_draft: bool = False,
    ) -> MessageSendResult:
        """Run the session fresh-check before calling the Channel port."""
        ...

    async def unfollow(self, actor: Actor, *, raw_target: str) -> ThreadUnfollowResult:
        """Disable future group notifications and report whether state changed."""
        ...


class IReminderService(Protocol):
    """Session-scoped Reminder command surface used by the local wrapper."""

    async def schedule(
        self,
        actor: Actor,
        request: ReminderScheduleRequest,
    ) -> ReminderScheduleResult: ...

    async def list(
        self,
        actor: Actor,
        request: ReminderListRequest,
    ) -> ReminderListResult: ...

    async def snooze(
        self,
        actor: Actor,
        request: ReminderSnoozeRequest,
    ) -> ReminderSnoozeResult: ...

    async def update(
        self,
        actor: Actor,
        request: ReminderUpdateRequest,
    ) -> ReminderUpdateResult: ...

    async def cancel(
        self,
        actor: Actor,
        request: ReminderCancelRequest,
    ) -> ReminderCancelResult: ...
