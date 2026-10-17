from __future__ import annotations

from dataclasses import dataclass

from .actor import Agent
from .actor import Thread as ThreadActor
from .models import (
    ChannelSession,
    InboundAttachment,
    Message,
    OutboundAttachment,
    SenderIdentity,
    Thread,
)
from .results import MessageReadResult, MessageSendFreshnessHold
from .states import MessageDirection, SenderKind


class InboxTargetResolutionError(ValueError):
    """A target does not resolve to exactly one Agent-owned BCN session."""


class AmbiguousInboxTargetError(InboxTargetResolutionError):
    """Several Agent-owned conversations answer to this target.

    Distinct from finding nothing: the caller must name which one it means
    rather than have one picked for it.
    """


@dataclass(frozen=True, slots=True)
class RecordInboundResult:
    channel_session: ChannelSession
    thread: Thread
    message: Message[InboundAttachment]
    channel_session_created: bool
    thread_created: bool
    message_created: bool


@dataclass(frozen=True, slots=True)
class ResolvedInboxTarget:
    thread: Thread
    channel_session: ChannelSession
    handle_is_unique: bool

    @property
    def canonical_target(self) -> str:
        return self.channel_session.canonical_target

    @property
    def display_target(self) -> str:
        return self.channel_session.display_target(
            handle_is_unique=self.handle_is_unique
        )


@dataclass(frozen=True, slots=True)
class KnownSender:
    """A sender this Agent has already received a message from."""

    sender: SenderIdentity
    channel: str
    channel_identity: str | None
    sender_kind: SenderKind


@dataclass(frozen=True, slots=True)
class ReadMessageHistoryResult:
    source_thread: Thread
    history: MessageReadResult


@dataclass(frozen=True, slots=True)
class MaterializeOutboundResult:
    channel_session: ChannelSession
    target_thread: Thread
    reply_to_provider_message_id: str | None
    outcome: Message[OutboundAttachment] | MessageSendFreshnessHold


@dataclass(frozen=True, slots=True)
class UnreadMessageOwner:
    agent: Agent
    trigger_message: Message[InboundAttachment]

    def __post_init__(self) -> None:
        if self.trigger_message.direction is not MessageDirection.INBOUND:
            raise ValueError("trigger_message must be inbound")
        if not self.trigger_message.notifies_runtime:
            raise ValueError("trigger_message must notify the runtime")

    @property
    def owner(self) -> ThreadActor:
        return ThreadActor(self.trigger_message.thread_id)
