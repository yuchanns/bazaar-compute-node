from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager
from copy import deepcopy
from dataclasses import replace
from types import TracebackType
from typing import Self
from uuid import uuid7

from ...core.lifecycle import IAsyncLifecycle
from ...core.models import (
    BcnSession,
    BcnSessionState,
    ChannelSession,
    ChannelSessionState,
    ConsumerCursor,
    InboundMessage,
    OutboundMessage,
    RuntimeEvent,
    RuntimeProcessState,
    RuntimeSession,
    RuntimeTurn,
)
from ...core.storage import IStorage, IStorageTransaction, NodeIdentity


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
        self.node_identity: NodeIdentity | None = None
        self.started = False
        self.stopped = False
        self._lock = asyncio.Lock()

    async def start(self, *, timeout: float) -> None:
        self.started = True
        self.stopped = False

    async def stop(self, *, timeout: float) -> None:
        self.stopped = True

    async def initialize(
        self,
        *,
        node_id: str | None = None,
        workspace_id: str | None = None,
    ) -> NodeIdentity:
        identity = self.node_identity
        if identity is None:
            identity = NodeIdentity(
                node_id=node_id or "dummy-node",
                workspace_id=workspace_id or str(uuid7()),
            )
        elif node_id is not None and identity.node_id != node_id:
            raise ValueError("requested node_id does not match dummy identity")
        elif workspace_id is not None and identity.workspace_id != workspace_id:
            raise ValueError("requested workspace_id does not match dummy identity")
        self.node_identity = identity
        return identity

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
        matches = [
            session
            for session in self._storage.channel_sessions.values()
            if (
                session.channel_slug == channel_slug
                and session.provider_conversation_key == provider_conversation_key
                and session.provider_thread_key == provider_thread_key
            )
        ]
        if len(matches) > 1:
            raise ValueError("multiple rows violate channel provider identity")
        return matches[0] if matches else None

    async def get_channel_session(
        self, channel_session_id: str
    ) -> ChannelSession | None:
        return self._storage.channel_sessions.get(channel_session_id)

    async def get_bcn_session(self, bcn_session_id: str) -> BcnSession | None:
        return self._storage.bcn_sessions.get(bcn_session_id)

    async def find_bcn_session(self, channel_session_id: str) -> BcnSession | None:
        matches = [
            session
            for session in self._storage.bcn_sessions.values()
            if session.channel_session_id == channel_session_id
        ]
        if len(matches) > 1:
            raise ValueError(
                "multiple bcn sessions are bound to the channel session: "
                f"{channel_session_id}"
            )
        return matches[0] if matches else None

    async def get_runtime_session(
        self, agent_runtime_session_id: str
    ) -> RuntimeSession | None:
        return self._storage.runtime_sessions.get(agent_runtime_session_id)

    async def find_runtime_session(self, bcn_session_id: str) -> RuntimeSession | None:
        matches = [
            session
            for session in self._storage.runtime_sessions.values()
            if session.bcn_session_id == bcn_session_id
        ]
        if len(matches) > 1:
            raise ValueError(
                "multiple runtime sessions are bound to the bcn session: "
                f"{bcn_session_id}"
            )
        return matches[0] if matches else None

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
        if not isinstance(session.state, ChannelSessionState):
            raise TypeError("channel session state is invalid")
        if not isinstance(session.following, bool):
            raise TypeError("channel session following must be a boolean")
        existing = self._storage.channel_sessions.get(session.channel_session_id)
        if existing is not None:
            if (
                existing.channel_slug != session.channel_slug
                or existing.provider_conversation_key
                != session.provider_conversation_key
                or existing.provider_thread_key != session.provider_thread_key
                or existing.created_at_ms != session.created_at_ms
            ):
                raise ValueError("channel session identity cannot change")
            session = _validate_channel_session_update(existing, session)
        else:
            duplicate = await self.find_channel_session(
                channel_slug=session.channel_slug,
                provider_conversation_key=session.provider_conversation_key,
                provider_thread_key=session.provider_thread_key,
            )
            if duplicate is not None:
                raise ValueError(
                    "channel provider identity is already bound to "
                    f"{duplicate.channel_session_id}"
                )
        self._storage.channel_sessions[session.channel_session_id] = session

    async def save_bcn_session(self, session: BcnSession) -> None:
        if not isinstance(session.state, BcnSessionState):
            raise TypeError("bcn session state is invalid")
        self._require_workspace(session.workspace_id)
        if session.channel_session_id not in self._storage.channel_sessions:
            raise ValueError(f"unknown channel session: {session.channel_session_id}")
        existing = self._storage.bcn_sessions.get(session.bcn_session_id)
        if existing is not None:
            if (
                existing.channel_session_id != session.channel_session_id
                or existing.workspace_id != session.workspace_id
                or existing.created_at_ms != session.created_at_ms
            ):
                raise ValueError("bcn session binding cannot change")
            session = _validate_bcn_session_update(existing, session)
        else:
            duplicate = await self.find_bcn_session(session.channel_session_id)
            if duplicate is not None:
                raise ValueError(
                    f"channel session is already bound to {duplicate.bcn_session_id}"
                )
        self._storage.bcn_sessions[session.bcn_session_id] = session

    async def save_runtime_session(self, session: RuntimeSession) -> None:
        if not isinstance(session.process_state, RuntimeProcessState):
            raise TypeError("runtime session process state is invalid")
        self._require_workspace(session.workspace_id)
        existing = self._storage.runtime_sessions.get(session.agent_runtime_session_id)
        bcn_session = self._storage.bcn_sessions.get(session.bcn_session_id)
        if bcn_session is None:
            raise ValueError(f"unknown bcn session: {session.bcn_session_id}")
        if bcn_session.channel_session_id not in self._storage.channel_sessions:
            raise ValueError(
                f"unknown channel session: {bcn_session.channel_session_id}"
            )
        if (
            bcn_session.channel_session_id != session.channel_session_id
            or bcn_session.workspace_id != session.workspace_id
        ):
            raise ValueError("runtime session binding does not match bcn session")
        if existing is not None:
            if (
                existing.bcn_session_id != session.bcn_session_id
                or existing.channel_session_id != session.channel_session_id
                or existing.runtime_slug != session.runtime_slug
                or existing.workspace_id != session.workspace_id
                or existing.created_at_ms != session.created_at_ms
            ):
                raise ValueError("runtime session binding cannot change")
            session = _validate_runtime_session_update(existing, session)
        else:
            duplicate = await self.find_runtime_session(session.bcn_session_id)
            if duplicate is not None:
                raise ValueError(
                    "bcn session is already bound to "
                    f"{duplicate.agent_runtime_session_id}"
                )
        self._storage.runtime_sessions[session.agent_runtime_session_id] = session

    def _require_workspace(self, workspace_id: str) -> None:
        identity = self._storage.node_identity
        if identity is None:
            raise RuntimeError("dummy storage identity has not been initialized")
        if workspace_id != identity.workspace_id:
            raise ValueError(
                "session workspace does not match the persisted node workspace"
            )

    async def save_runtime_turn(self, turn: RuntimeTurn) -> None:
        existing = self._storage.runtime_turns.get(turn.turn_id)
        if existing is not None and (
            existing.agent_runtime_session_id != turn.agent_runtime_session_id
        ):
            raise ValueError("runtime turn binding cannot change")
        self._storage.runtime_turns[turn.turn_id] = turn

    async def append_inbound_message(self, message: InboundMessage) -> InboundMessage:
        messages = self._storage.inbound_messages.setdefault(message.bcn_session_id, [])
        provider_matches = [
            existing
            for session_messages in self._storage.inbound_messages.values()
            for existing in session_messages
            if (
                existing.channel_slug == message.channel_slug
                and existing.provider_message_id == message.provider_message_id
            )
        ]
        if provider_matches:
            existing = provider_matches[0]
            if (
                existing.bcn_session_id != message.bcn_session_id
                or existing.channel_session_id != message.channel_session_id
            ):
                raise ValueError(
                    "provider message id is already bound to another session"
                )
            if existing == message:
                return existing
            raise ValueError("duplicate provider message id has different content")
        for existing in messages:
            if existing.message_id == message.message_id:
                if existing != message:
                    raise ValueError("duplicate message id has different content")
                return existing
        expected_seq = messages[-1].seq + 1 if messages else 1
        if message.seq != expected_seq:
            raise ValueError(
                f"inbound sequence must be contiguous: expected {expected_seq}, got {message.seq}"
            )
        messages.append(message)
        return message

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


def _validate_updated_at(existing: int, incoming: int) -> None:
    if incoming < existing:
        raise ValueError("session updated_at_ms cannot move backwards")


def _validate_channel_session_update(
    existing: ChannelSession,
    incoming: ChannelSession,
) -> ChannelSession:
    if (
        existing.channel_slug != incoming.channel_slug
        or existing.provider_conversation_key != incoming.provider_conversation_key
        or existing.provider_thread_key != incoming.provider_thread_key
        or existing.created_at_ms != incoming.created_at_ms
    ):
        raise ValueError("channel session identity cannot change")
    _validate_updated_at(existing.updated_at_ms, incoming.updated_at_ms)
    transitioned = existing.transition_to(
        incoming.state,
        updated_at_ms=incoming.updated_at_ms,
    )
    return replace(
        transitioned,
        updated_at_ms=incoming.updated_at_ms,
        following=incoming.following,
        last_inbound_at_ms=incoming.last_inbound_at_ms,
        last_outbound_at_ms=incoming.last_outbound_at_ms,
        metadata=incoming.metadata,
    )


def _validate_bcn_session_update(
    existing: BcnSession,
    incoming: BcnSession,
) -> BcnSession:
    if (
        existing.channel_session_id != incoming.channel_session_id
        or existing.workspace_id != incoming.workspace_id
        or existing.created_at_ms != incoming.created_at_ms
    ):
        raise ValueError("bcn session binding cannot change")
    _validate_updated_at(existing.updated_at_ms, incoming.updated_at_ms)
    transitioned = existing.transition_to(
        incoming.state,
        updated_at_ms=incoming.updated_at_ms,
    )
    return replace(
        transitioned,
        updated_at_ms=incoming.updated_at_ms,
        last_activity_at_ms=incoming.last_activity_at_ms,
        metadata=incoming.metadata,
    )


def _validate_runtime_session_update(
    existing: RuntimeSession,
    incoming: RuntimeSession,
) -> RuntimeSession:
    if (
        existing.bcn_session_id != incoming.bcn_session_id
        or existing.channel_session_id != incoming.channel_session_id
        or existing.runtime_slug != incoming.runtime_slug
        or existing.workspace_id != incoming.workspace_id
        or existing.created_at_ms != incoming.created_at_ms
    ):
        raise ValueError("runtime session binding cannot change")
    _validate_updated_at(existing.updated_at_ms, incoming.updated_at_ms)
    transitioned = existing.transition_process_to(
        incoming.process_state,
        updated_at_ms=incoming.updated_at_ms,
        error_kind=incoming.last_error_kind,
        error_message=incoming.last_error_message,
    )
    return replace(
        transitioned,
        updated_at_ms=incoming.updated_at_ms,
        provider_thread_id=incoming.provider_thread_id,
        process_id=incoming.process_id,
        last_error_kind=incoming.last_error_kind or transitioned.last_error_kind,
        last_error_message=incoming.last_error_message
        or transitioned.last_error_message,
        metadata=incoming.metadata,
    )
