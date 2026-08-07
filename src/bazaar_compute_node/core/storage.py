from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Protocol

from .lifecycle import IAsyncLifecycle
from .models import (
    BcnSession,
    ChannelSession,
    ConsumerCursor,
    InboundMessage,
    OutboundMessage,
    RuntimeEvent,
    RuntimeSession,
    RuntimeTurn,
)


@dataclass(frozen=True, slots=True)
class NodeIdentity:
    """Stable node and shared workspace identity returned by storage."""

    node_id: str
    workspace_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.node_id, str) or not self.node_id:
            raise ValueError("node_id must be a non-empty string")
        if not isinstance(self.workspace_id, str) or not self.workspace_id:
            raise ValueError("workspace_id must be a non-empty string")


class IStorageTransaction(Protocol):
    """Explicit transaction boundary for repository operations.

    A normal context exit commits. An exception, including cancellation,
    rolls back. Implementations must not rely on implicit driver transactions
    for cursor, fresh-check, or lifecycle state transitions.
    """

    async def find_channel_session(
        self,
        *,
        channel_slug: str,
        provider_conversation_key: str,
        provider_thread_key: str,
    ) -> ChannelSession | None:
        """Find a channel session by provider-neutral identity fields."""
        ...

    async def get_channel_session(
        self, channel_session_id: str
    ) -> ChannelSession | None:
        """Load one channel session by local id."""
        ...

    async def get_bcn_session(self, bcn_session_id: str) -> BcnSession | None:
        """Load one bcn session by local id."""
        ...

    async def find_bcn_session(self, channel_session_id: str) -> BcnSession | None:
        """Find the bcn session bound to one channel session for recovery."""
        ...

    async def get_runtime_session(
        self, agent_runtime_session_id: str
    ) -> RuntimeSession | None:
        """Load one runtime process session by local id."""
        ...

    async def find_runtime_session(self, bcn_session_id: str) -> RuntimeSession | None:
        """Find the runtime session bound to one bcn session for recovery."""
        ...

    async def get_runtime_turn(self, turn_id: str) -> RuntimeTurn | None:
        """Load one runtime turn by local id."""
        ...

    async def get_consumer_cursor(self, bcn_session_id: str) -> ConsumerCursor | None:
        """Load the session-scoped delivery and inbox snapshot cursor."""
        ...

    async def get_latest_inbound_seq(self, bcn_session_id: str) -> int:
        """Return zero when the session has no inbound messages."""
        ...

    async def list_inbound_messages(
        self,
        bcn_session_id: str,
        *,
        after_seq: int | None = None,
        target: str | None = None,
        around_message_id: str | None = None,
        limit: int = 100,
    ) -> tuple[InboundMessage, ...]:
        """Read messages without advancing the consumer cursor."""
        ...

    async def save_channel_session(self, session: ChannelSession) -> None:
        """Persist a validated channel session state transition."""
        ...

    async def save_bcn_session(self, session: BcnSession) -> None:
        """Persist a validated bcn session state transition."""
        ...

    async def save_runtime_session(self, session: RuntimeSession) -> None:
        """Persist a validated runtime process state transition."""
        ...

    async def save_runtime_turn(self, turn: RuntimeTurn) -> None:
        """Persist a validated runtime turn state transition."""
        ...

    async def append_inbound_message(self, message: InboundMessage) -> InboundMessage:
        """Append or deduplicate one inbound message and return its canonical row."""
        ...

    async def save_consumer_cursor(self, cursor: ConsumerCursor) -> None:
        """Persist the session cursor and independent inbox snapshot."""
        ...

    async def get_outbound_message(
        self, outbound_message_id: str
    ) -> OutboundMessage | None:
        """Load one outbound command attempt by local id."""
        ...

    async def save_outbound_message(self, message: OutboundMessage) -> OutboundMessage:
        """Persist a draft or delivery transition and return its canonical row."""
        ...

    async def append_runtime_event(self, event: RuntimeEvent) -> RuntimeEvent:
        """Append an operational event and return its canonical local sequence."""
        ...


class IStorage(IAsyncLifecycle, Protocol):
    """Provider-neutral storage lifecycle and explicit transaction factory."""

    async def initialize(
        self,
        *,
        node_id: str | None = None,
        workspace_id: str | None = None,
    ) -> NodeIdentity:
        """Load or create node identity after storage startup."""
        ...

    def transaction(self) -> AbstractAsyncContextManager[IStorageTransaction]:
        """Open an explicit transaction owned by the caller."""
        ...
