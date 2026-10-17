"""Agent-scoped message services and provider-owned persistence scopes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Protocol

from bazaar_contrib_channel import Channel
from bazaar_core import ServiceKey

from .actor import Actor, Actors, Agent, Thread
from .concurrency import ThreadConcurrency
from .models import (
    ChannelSession,
    ConsumerCursor,
    InboundAttachment,
    Message,
    OutboundAttachment,
)
from .models import Thread as Conversation
from .operations import KnownSender, RecordInboundResult, ResolvedInboxTarget
from .results import (
    InboxListResult,
    MessageCheckResult,
    MessageReadResult,
    MessageSearchRequest,
    MessageSearchResult,
    MessageSendResult,
    ThreadUnfollowResult,
    UnreadSummary,
)
from .states import MessageDirection, Review

SessionBindingValidator = Callable[[Actor, Mapping[str, object]], Awaitable[None]]


class MessageSession(Protocol):
    """Conversation operations valid within their provider's database scope."""

    async def get_thread(self, actor: Thread) -> Conversation | None: ...

    async def get_channel_session(
        self, channel_session_id: str
    ) -> ChannelSession | None: ...

    async def save_channel_session(self, session: ChannelSession) -> None: ...


class MessageStorage(MessageSession, Protocol):
    """Agent-owned message data shared with other contribs."""

    @property
    def owner(self) -> Agent: ...

    async def get_consumer_cursor(self, actor: Thread) -> ConsumerCursor | None: ...

    async def write[T](self, operation: Callable[[MessageSession], Awaitable[T]]) -> T:
        """Run the callback in one writer scope; its session stays in the callback."""
        ...

    async def resolve_inbox_target(self, raw_target: str) -> ResolvedInboxTarget: ...

    async def find_known_sender(self, token: str) -> KnownSender | None: ...

    async def find_message(
        self,
        channel: str,
        provider_thread_id: str,
        provider_message_id: str,
        *,
        direction: MessageDirection | None = None,
    ) -> Message[InboundAttachment | OutboundAttachment] | None: ...

    async def list_ready_attachment_paths(self) -> tuple[str, ...]: ...

    async def record_inbound(
        self,
        message: Message[InboundAttachment],
        *,
        now_ms: int,
        opening: Review,
    ) -> RecordInboundResult: ...


class MessageService(Protocol):
    """Messages and conversations available within one agent binding."""

    @property
    def storage(self) -> MessageStorage: ...

    async def read_unread_summary(self, actor: Actor, *, limit: int) -> UnreadSummary:
        """Read unread counts and the newest messages without advancing delivery."""
        ...

    async def check_inbox(
        self,
        actor: Actor,
        *,
        limit: int | None = None,
        offset: int = 0,
        pending_only: bool = True,
        review: Review | None = Review.APPROVED,
    ) -> InboxListResult: ...

    async def check_messages(self, actor: Actor) -> tuple[MessageCheckResult, ...]: ...

    async def read_messages(
        self,
        actor: Actor,
        *,
        raw_target: str,
        around_message_id: str | None = None,
        limit: int = 100,
        review: Review | None = Review.APPROVED,
    ) -> MessageReadResult: ...

    async def search_messages(
        self,
        actor: Actor,
        request: MessageSearchRequest,
        *,
        review: Review | None = Review.APPROVED,
        query_budget: float | None = None,
    ) -> MessageSearchResult: ...

    async def send_message(
        self,
        *,
        actor: Actor,
        raw_target: str,
        body: str,
        created_at_ms: int,
        attachment_paths: tuple[str, ...] = (),
        reply_to_message_id: str | None = None,
        send_draft: bool = False,
    ) -> MessageSendResult: ...

    async def unfollow_thread(
        self, actor: Actor, *, raw_target: str
    ) -> ThreadUnfollowResult: ...


class Messages(Protocol):
    """Bind each agent's message service and dispatch its command requests."""

    def bind(
        self,
        *,
        actors: Actors,
        name: str,
        channel: Channel,
        workspace: Path,
        concurrency: ThreadConcurrency,
        provider_timeout: float,
        shutdown_timeout: float,
        session_binding_validator: SessionBindingValidator | None = None,
    ) -> AbstractAsyncContextManager[MessageService]: ...

    async def request(self, request: Mapping[str, object]) -> Mapping[str, object]: ...


MESSAGES = ServiceKey[Messages]("messages")
