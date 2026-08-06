from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager
from copy import deepcopy
from types import TracebackType
from typing import Self

from ...core.lifecycle import IAsyncLifecycle
from ...core.models import (
    BcnSession,
    ChannelSession,
    ConsumerCursor,
    InboundMessage,
    OutboundMessage,
    RuntimeEvent,
    RuntimeSession,
    RuntimeTurn,
)
from ...core.storage import IStorage, IStorageTransaction


class DummyStorage(IStorage, IAsyncLifecycle):
    """Transactional in-memory storage for behavior-level integration tests."""

    def __init__(self) -> None:
        self.channel_sessions: dict[str, ChannelSession] = {}
        self.bcn_sessions: dict[str, BcnSession] = {}
        self.runtime_sessions: dict[str, RuntimeSession] = {}
        self.runtime_turns: dict[str, RuntimeTurn] = {}
        self.cursors: dict[str, ConsumerCursor] = {}
        self.inbound_messages: dict[str, list[InboundMessage]] = {}
        self.outbound_messages: dict[str, OutboundMessage] = {}
        self.runtime_events: list[RuntimeEvent] = []
        self.started = False
        self.stopped = False
        self._lock = asyncio.Lock()

    async def start(self, *, timeout: float) -> None:
        self.started = True
        self.stopped = False

    async def stop(self, *, timeout: float) -> None:
        self.stopped = True

    def transaction(self) -> AbstractAsyncContextManager[IStorageTransaction]:
        return _DummyStorageTransaction(self)


_Snapshot = tuple[
    dict[str, ChannelSession],
    dict[str, BcnSession],
    dict[str, RuntimeSession],
    dict[str, RuntimeTurn],
    dict[str, ConsumerCursor],
    dict[str, list[InboundMessage]],
    dict[str, OutboundMessage],
    list[RuntimeEvent],
]


class _DummyStorageTransaction(IStorageTransaction):
    def __init__(self, storage: DummyStorage) -> None:
        self._storage = storage
        self._snapshot: _Snapshot | None = None

    async def __aenter__(self) -> Self:
        await self._storage._lock.acquire()
        self._snapshot = (
            deepcopy(self._storage.channel_sessions),
            deepcopy(self._storage.bcn_sessions),
            deepcopy(self._storage.runtime_sessions),
            deepcopy(self._storage.runtime_turns),
            deepcopy(self._storage.cursors),
            deepcopy(self._storage.inbound_messages),
            deepcopy(self._storage.outbound_messages),
            deepcopy(self._storage.runtime_events),
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        if exc_type is not None and self._snapshot is not None:
            (
                self._storage.channel_sessions,
                self._storage.bcn_sessions,
                self._storage.runtime_sessions,
                self._storage.runtime_turns,
                self._storage.cursors,
                self._storage.inbound_messages,
                self._storage.outbound_messages,
                self._storage.runtime_events,
            ) = self._snapshot
        self._storage._lock.release()
        return False

    async def find_channel_session(
        self,
        *,
        channel_slug: str,
        provider_conversation_key: str,
        provider_thread_key: str,
    ) -> ChannelSession | None:
        for session in self._storage.channel_sessions.values():
            if (
                session.channel_slug == channel_slug
                and session.provider_conversation_key == provider_conversation_key
                and session.provider_thread_key == provider_thread_key
            ):
                return session
        return None

    async def get_channel_session(
        self, channel_session_id: str
    ) -> ChannelSession | None:
        return self._storage.channel_sessions.get(channel_session_id)

    async def get_bcn_session(self, bcn_session_id: str) -> BcnSession | None:
        return self._storage.bcn_sessions.get(bcn_session_id)

    async def get_runtime_session(
        self, agent_runtime_session_id: str
    ) -> RuntimeSession | None:
        return self._storage.runtime_sessions.get(agent_runtime_session_id)

    async def get_runtime_turn(self, turn_id: str) -> RuntimeTurn | None:
        return self._storage.runtime_turns.get(turn_id)

    async def get_consumer_cursor(self, bcn_session_id: str) -> ConsumerCursor | None:
        return self._storage.cursors.get(bcn_session_id)

    async def get_latest_inbound_seq(self, bcn_session_id: str) -> int:
        messages = self._storage.inbound_messages.get(bcn_session_id, [])
        return messages[-1].seq if messages else 0

    async def list_inbound_messages(
        self,
        bcn_session_id: str,
        *,
        after_seq: int | None = None,
        target: str | None = None,
        around_message_id: str | None = None,
        limit: int = 100,
    ) -> tuple[InboundMessage, ...]:
        messages = list(self._storage.inbound_messages.get(bcn_session_id, []))
        if after_seq is not None:
            messages = [message for message in messages if message.seq > after_seq]
        if target is not None:
            messages = [
                message for message in messages if message.canonical_target == target
            ]
        if around_message_id is not None:
            try:
                around_index = next(
                    index
                    for index, message in enumerate(messages)
                    if message.message_id == around_message_id
                )
            except StopIteration as error:
                raise ValueError(
                    f"message not found in requested history: {around_message_id}"
                ) from error
            half = max(limit // 2, 1)
            start = max(around_index - half, 0)
            messages = messages[start : start + limit]
        else:
            messages = messages[:limit]
        return tuple(messages)

    async def save_channel_session(self, session: ChannelSession) -> None:
        existing = self._storage.channel_sessions.get(session.channel_session_id)
        if existing is not None and existing.channel_slug != session.channel_slug:
            raise ValueError("channel session slug cannot change")
        self._storage.channel_sessions[session.channel_session_id] = session

    async def save_bcn_session(self, session: BcnSession) -> None:
        existing = self._storage.bcn_sessions.get(session.bcn_session_id)
        if (
            existing is not None
            and existing.channel_session_id != session.channel_session_id
        ):
            raise ValueError("bcn session channel binding cannot change")
        self._storage.bcn_sessions[session.bcn_session_id] = session

    async def save_runtime_session(self, session: RuntimeSession) -> None:
        existing = self._storage.runtime_sessions.get(session.agent_runtime_session_id)
        if existing is not None and (
            existing.bcn_session_id != session.bcn_session_id
            or existing.channel_session_id != session.channel_session_id
        ):
            raise ValueError("runtime session binding cannot change")
        self._storage.runtime_sessions[session.agent_runtime_session_id] = session

    async def save_runtime_turn(self, turn: RuntimeTurn) -> None:
        existing = self._storage.runtime_turns.get(turn.turn_id)
        if existing is not None and (
            existing.agent_runtime_session_id != turn.agent_runtime_session_id
        ):
            raise ValueError("runtime turn binding cannot change")
        self._storage.runtime_turns[turn.turn_id] = turn

    async def append_inbound_message(self, message: InboundMessage) -> None:
        messages = self._storage.inbound_messages.setdefault(message.bcn_session_id, [])
        for existing in messages:
            if existing.message_id == message.message_id:
                if existing != message:
                    raise ValueError("duplicate message id has different content")
                return
            if existing.provider_message_id == message.provider_message_id:
                raise ValueError("duplicate provider message id has different content")
        expected_seq = messages[-1].seq + 1 if messages else 1
        if message.seq != expected_seq:
            raise ValueError(
                f"inbound sequence must be contiguous: expected {expected_seq}, got {message.seq}"
            )
        messages.append(message)

    async def save_consumer_cursor(self, cursor: ConsumerCursor) -> None:
        self._storage.cursors[cursor.bcn_session_id] = cursor

    async def save_outbound_message(self, message: OutboundMessage) -> None:
        existing = self._storage.outbound_messages.get(message.outbound_message_id)
        if existing is not None and (
            existing.bcn_session_id != message.bcn_session_id
            or existing.channel_session_id != message.channel_session_id
        ):
            raise ValueError("outbound message binding cannot change")
        self._storage.outbound_messages[message.outbound_message_id] = message

    async def append_runtime_event(self, event: RuntimeEvent) -> None:
        for existing in self._storage.runtime_events:
            if existing.event_id == event.event_id:
                if existing != event:
                    raise ValueError("duplicate runtime event id has different content")
                return
        self._storage.runtime_events.append(event)
