from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager
from copy import deepcopy
from dataclasses import replace
from types import TracebackType
from typing import Self
from uuid import uuid7

from bazaar_compute_node.core.lifecycle import IAsyncLifecycle
from bazaar_compute_node.core.models import (
    BcnSession,
    ChannelSession,
    ConsumerCursor,
    FreshCheckState,
    InboundMessage,
    OutboundDeliveryState,
    OutboundMessage,
    RuntimeEvent,
    RuntimeEventState,
    RuntimeSession,
    RuntimeTurn,
    RuntimeTurnState,
)
from bazaar_compute_node.core.storage import IStorage, IStorageTransaction, NodeIdentity


class MemoryStorage(IStorage, IAsyncLifecycle):
    """Transactional in-memory storage for behavior-level integration tests."""

    @property
    def name(self) -> str:
        return "test"

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
                node_id=node_id or "test-node",
                workspace_id=workspace_id or str(uuid7()),
            )
        elif node_id is not None and identity.node_id != node_id:
            raise ValueError("requested node_id does not match test identity")
        elif workspace_id is not None and identity.workspace_id != workspace_id:
            raise ValueError("requested workspace_id does not match test identity")
        self.node_identity = identity
        return identity

    def transaction(self) -> AbstractAsyncContextManager[IStorageTransaction]:
        return _MemoryStorageTransaction(self)


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


class _MemoryStorageTransaction(IStorageTransaction):
    def __init__(self, storage: MemoryStorage) -> None:
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
        channel: str,
        provider_thread_id: str,
    ) -> ChannelSession | None:
        matches = [
            session
            for session in self._storage.channel_sessions.values()
            if (
                session.channel == channel
                and session.provider_thread_id == provider_thread_id
            )
        ]
        if len(matches) > 1:
            raise ValueError("multiple rows violate channel provider identity")
        return matches[0] if matches else None

    async def get_channel_session(self, session_id: str) -> ChannelSession | None:
        return self._storage.channel_sessions.get(session_id)

    async def get_bcn_session(self, session_id: str) -> BcnSession | None:
        return self._storage.bcn_sessions.get(session_id)

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

    async def get_runtime_session(self, session_id: str) -> RuntimeSession | None:
        return self._storage.runtime_sessions.get(session_id)

    async def find_runtime_session(self, session_id: str) -> RuntimeSession | None:
        matches = [
            session
            for session in self._storage.runtime_sessions.values()
            if session.bcn_session_id == session_id
        ]
        if len(matches) > 1:
            raise ValueError(
                f"multiple runtime sessions are bound to the bcn session: {session_id}"
            )
        return matches[0] if matches else None

    async def get_runtime_turn(self, turn_id: str) -> RuntimeTurn | None:
        return self._storage.runtime_turns.get(turn_id)

    async def get_consumer_cursor(self, session_id: str) -> ConsumerCursor | None:
        return self._storage.cursors.get(session_id)

    async def get_latest_inbound_seq(self, session_id: str) -> int:
        messages = self._storage.inbound_messages.get(session_id, [])
        return messages[-1].seq if messages else 0

    async def inbound_message_exists(
        self, channel: str, provider_message_id: str
    ) -> bool:
        return await self.find_inbound_message(channel, provider_message_id) is not None

    async def find_inbound_message(
        self, channel: str, provider_message_id: str
    ) -> InboundMessage | None:
        matches = [
            message
            for messages in self._storage.inbound_messages.values()
            for message in messages
            if message.channel == channel
            and message.provider_message_id == provider_message_id
        ]
        if len(matches) > 1:
            raise ValueError("multiple rows violate provider inbound identity")
        return matches[0] if matches else None

    async def list_ready_attachment_paths(self) -> tuple[str, ...]:
        return tuple(
            attachment.relative_path
            for messages in self._storage.inbound_messages.values()
            for message in messages
            for attachment in message.attachments
            if attachment.state == "ready" and attachment.relative_path is not None
        )

    async def list_inbound_messages(
        self,
        session_id: str,
        *,
        after_seq: int | None = None,
        target: str | None = None,
        around_message_id: str | None = None,
        notifying_only: bool = False,
        limit: int = 100,
    ) -> tuple[InboundMessage, ...]:
        messages = list(self._storage.inbound_messages.get(session_id, []))
        if after_seq is not None:
            messages = [message for message in messages if message.seq > after_seq]
        if target is not None:
            messages = [
                message for message in messages if message.canonical_target == target
            ]
        if notifying_only:
            messages = [message for message in messages if message.notifies_runtime]
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
            before_count = limit // 2
            start = max(around_index - before_count, 0)
            messages = messages[start : start + limit]
        else:
            messages = messages[:limit]
        return tuple(messages)

    async def save_channel_session(self, session: ChannelSession) -> None:
        if not isinstance(session.following, bool):
            raise TypeError("channel session following must be a boolean")
        existing = self._storage.channel_sessions.get(session.id)
        if existing is not None:
            if (
                existing.channel != session.channel
                or existing.provider_thread_id != session.provider_thread_id
                or existing.created_at_ms != session.created_at_ms
            ):
                raise ValueError("channel session identity cannot change")
            session = _validate_channel_session_update(existing, session)
        else:
            duplicate = await self.find_channel_session(
                channel=session.channel,
                provider_thread_id=session.provider_thread_id,
            )
            if duplicate is not None:
                raise ValueError(
                    f"channel provider identity is already bound to {duplicate.id}"
                )
        self._storage.channel_sessions[session.id] = session

    async def save_bcn_session(self, session: BcnSession) -> None:
        self._require_workspace(session.workspace_id)
        if session.channel_session_id not in self._storage.channel_sessions:
            raise ValueError(f"unknown channel session: {session.channel_session_id}")
        existing = self._storage.bcn_sessions.get(session.id)
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
                raise ValueError(f"channel session is already bound to {duplicate.id}")
        self._storage.bcn_sessions[session.id] = session

    async def save_runtime_session(self, session: RuntimeSession) -> None:
        self._require_workspace(session.workspace_id)
        existing = self._storage.runtime_sessions.get(session.id)
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
                or existing.runtime != session.runtime
                or existing.workspace_id != session.workspace_id
                or existing.created_at_ms != session.created_at_ms
            ):
                raise ValueError("runtime session binding cannot change")
            session = _validate_runtime_session_update(existing, session)
        else:
            duplicate = await self.find_runtime_session(session.bcn_session_id)
            if duplicate is not None:
                raise ValueError(f"bcn session is already bound to {duplicate.id}")
        self._storage.runtime_sessions[session.id] = session

    def _require_workspace(self, workspace_id: str) -> None:
        identity = self._storage.node_identity
        if identity is None:
            raise RuntimeError("memory storage identity has not been initialized")
        if workspace_id != identity.workspace_id:
            raise ValueError(
                "session workspace does not match the persisted node workspace"
            )

    async def save_runtime_turn(self, turn: RuntimeTurn) -> None:
        _validate_runtime_turn_input(turn)
        if turn.session_id not in self._storage.runtime_sessions:
            raise ValueError(f"unknown runtime session: {turn.session_id}")
        existing = self._storage.runtime_turns.get(turn.turn_id)
        if existing is None:
            if turn.state is not RuntimeTurnState.STARTING:
                raise ValueError("a new runtime turn must start in starting state")
            _validate_active_runtime_turn(self._storage, turn)
            self._storage.runtime_turns[turn.turn_id] = turn
            return
        if existing.session_id != turn.session_id:
            raise ValueError("runtime turn binding cannot change")
        if existing.started_at_ms != turn.started_at_ms:
            raise ValueError("runtime turn start time cannot change")
        canonical = _validate_runtime_turn_update(existing, turn)
        _validate_active_runtime_turn(self._storage, canonical)
        self._storage.runtime_turns[turn.turn_id] = canonical

    async def append_inbound_message(self, message: InboundMessage) -> InboundMessage:
        messages = self._storage.inbound_messages.setdefault(message.session_id, [])
        provider_matches = [
            existing
            for session_messages in self._storage.inbound_messages.values()
            for existing in session_messages
            if (
                existing.channel == message.channel
                and existing.provider_message_id == message.provider_message_id
            )
        ]
        if provider_matches:
            existing = provider_matches[0]
            if (
                existing.session_id != message.session_id
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
        self._storage.cursors[cursor.session_id] = cursor

    async def get_outbound_message(
        self, outbound_message_id: str
    ) -> OutboundMessage | None:
        return self._storage.outbound_messages.get(outbound_message_id)

    async def save_outbound_message(self, message: OutboundMessage) -> OutboundMessage:
        _validate_outbound_message_input(message)
        bcn_session = self._storage.bcn_sessions.get(message.session_id)
        if bcn_session is None:
            raise ValueError(f"unknown bcn session: {message.session_id}")
        if message.channel_session_id not in self._storage.channel_sessions:
            raise ValueError(f"unknown channel session: {message.channel_session_id}")
        if bcn_session.channel_session_id != message.channel_session_id:
            raise ValueError("outbound message binding does not match bcn session")

        existing = self._storage.outbound_messages.get(message.outbound_message_id)
        if existing is None:
            canonical = replace(message, outbound_message_id=str(uuid7()))
            _validate_outbound_insert(canonical)
            self._storage.outbound_messages[canonical.outbound_message_id] = canonical
            return canonical
        if (
            existing.command_id != message.command_id
            or existing.session_id != message.session_id
            or existing.channel_session_id != message.channel_session_id
            or existing.target != message.target
            or existing.reply_to_message_id != message.reply_to_message_id
            or existing.body != message.body
            or existing.created_at_ms != message.created_at_ms
        ):
            raise ValueError("outbound message identity cannot change")
        canonical = _validate_outbound_update(existing, message)
        self._storage.outbound_messages[message.outbound_message_id] = canonical
        return canonical

    async def append_runtime_event(self, event: RuntimeEvent) -> RuntimeEvent:
        _validate_runtime_event_input(event)
        for existing in self._storage.runtime_events:
            if existing.event_id == event.event_id:
                if not _same_runtime_event_payload(existing, event):
                    raise ValueError("duplicate runtime event id has different content")
                return existing
        _validate_runtime_event_references(self._storage, event)
        next_event_seq = (
            max(
                (existing.event_seq for existing in self._storage.runtime_events),
                default=0,
            )
            + 1
        )
        canonical = replace(event, event_seq=next_event_seq)
        self._storage.runtime_events.append(canonical)
        return canonical


def _validate_updated_at(existing: int, incoming: int) -> None:
    if incoming < existing:
        raise ValueError("session updated_at_ms cannot move backwards")


def _validate_runtime_turn_input(turn: RuntimeTurn) -> None:
    if not isinstance(turn, RuntimeTurn):
        raise TypeError("turn must be a RuntimeTurn")
    if not isinstance(turn.state, RuntimeTurnState):
        raise TypeError("runtime turn state is invalid")
    for value, field_name in (
        (turn.latest_event_name, "latest_event_name"),
        (turn.error_kind, "error_kind"),
        (turn.error_message, "error_message"),
    ):
        _validate_optional_input_text(value, field_name)
    terminal_states = {
        RuntimeTurnState.COMPLETED,
        RuntimeTurnState.FAILED,
        RuntimeTurnState.CANCELLED,
    }
    if turn.state in terminal_states:
        if turn.completed_at_ms is None:
            raise ValueError("terminal runtime turn requires completed_at_ms")
        if turn.completed_at_ms < turn.started_at_ms:
            raise ValueError("runtime turn completion cannot precede start")
    elif turn.completed_at_ms is not None:
        raise ValueError("non-terminal runtime turn cannot have completed_at_ms")


def _validate_active_runtime_turn(
    storage: MemoryStorage,
    turn: RuntimeTurn,
) -> None:
    active_states = {
        RuntimeTurnState.STARTING,
        RuntimeTurnState.RUNNING,
        RuntimeTurnState.UNKNOWN,
        RuntimeTurnState.RECONCILING,
    }
    for existing in storage.runtime_turns.values():
        if (
            existing.turn_id != turn.turn_id
            and existing.session_id == turn.session_id
            and existing.state in active_states
            and turn.state in active_states
        ):
            raise ValueError(
                f"runtime session already has an active turn: {existing.turn_id}"
            )


def _validate_runtime_turn_update(
    existing: RuntimeTurn,
    incoming: RuntimeTurn,
) -> RuntimeTurn:
    for existing_value, incoming_value, field_name in (
        (
            existing.provider_turn_id,
            incoming.provider_turn_id,
            "provider_turn_id",
        ),
        (
            existing.client_user_message_id,
            incoming.client_user_message_id,
            "client_user_message_id",
        ),
    ):
        if (
            existing_value is not None
            and incoming_value is not None
            and existing_value != incoming_value
        ):
            raise ValueError(f"runtime turn {field_name} cannot change")
    if existing.state is incoming.state:
        if existing.completed_at_ms != incoming.completed_at_ms:
            raise ValueError("runtime turn completion time cannot change")
        transitioned = existing
    else:
        at_ms = (
            incoming.completed_at_ms
            if incoming.completed_at_ms is not None
            else incoming.started_at_ms
        )
        transitioned = existing.transition_to(
            incoming.state,
            at_ms=at_ms,
            error_kind=incoming.error_kind,
            error_message=incoming.error_message,
            latest_event_name=incoming.latest_event_name,
        )
    return replace(
        transitioned,
        provider_turn_id=incoming.provider_turn_id or existing.provider_turn_id,
        client_user_message_id=incoming.client_user_message_id
        or existing.client_user_message_id,
        latest_event_name=incoming.latest_event_name or transitioned.latest_event_name,
        error_kind=incoming.error_kind or transitioned.error_kind,
        error_message=incoming.error_message or transitioned.error_message,
        metadata=incoming.metadata,
    )


def _validate_outbound_message_input(message: OutboundMessage) -> None:
    if not isinstance(message, OutboundMessage):
        raise TypeError("message must be an OutboundMessage")
    if not isinstance(message.state, OutboundDeliveryState):
        raise TypeError("outbound message state is invalid")
    if not isinstance(message.fresh_check_state, FreshCheckState):
        raise TypeError("outbound fresh-check state is invalid")
    if not isinstance(message.body, str):
        raise TypeError("outbound body must be a string")
    for value, field_name in (
        (message.reply_to_message_id, "reply_to_message_id"),
        (message.provider_message_id, "provider_message_id"),
        (message.provider_receipt_ref, "provider_receipt_ref"),
        (message.error_kind, "error_kind"),
        (message.error_message, "error_message"),
        (message.next_action, "next_action"),
    ):
        _validate_optional_input_text(value, field_name)
    if message.fresh_check_state is FreshCheckState.REQUIRED and (
        message.snapshot_seq is not None or message.current_inbound_seq is not None
    ):
        raise ValueError("a required outbound fresh check cannot contain evidence")
    if message.fresh_check_state is FreshCheckState.PASSED:
        if message.snapshot_seq is None or message.current_inbound_seq is None:
            raise ValueError("a passed outbound fresh check requires sequence bounds")
        if message.current_inbound_seq > message.snapshot_seq:
            raise ValueError(
                "outbound current inbound sequence exceeds snapshot sequence"
            )
    if (
        message.state
        in {
            OutboundDeliveryState.PENDING,
            OutboundDeliveryState.QUEUED,
            OutboundDeliveryState.SENT,
            OutboundDeliveryState.PARTIAL,
            OutboundDeliveryState.FAILED,
            OutboundDeliveryState.UNKNOWN,
        }
        and message.fresh_check_state is not FreshCheckState.PASSED
    ):
        raise ValueError("outbound delivery state requires a passed fresh check")
    if (
        message.state is OutboundDeliveryState.REJECTED
        and message.fresh_check_state is FreshCheckState.PASSED
    ):
        raise ValueError("rejected outbound message cannot have a passed fresh check")
    for value, field_name in (
        (message.provider_attempted_at_ms, "provider_attempted_at_ms"),
        (message.completed_at_ms, "completed_at_ms"),
        (message.draft_saved_at_ms, "draft_saved_at_ms"),
    ):
        if value is not None and value < message.created_at_ms:
            raise ValueError(f"outbound {field_name} cannot precede creation")
    if message.state is OutboundDeliveryState.DRAFT and any(
        value is not None
        for value in (
            message.provider_message_id,
            message.provider_receipt_ref,
            message.provider_attempted_at_ms,
            message.completed_at_ms,
            message.draft_saved_at_ms,
        )
    ):
        raise ValueError("draft outbound message cannot contain delivery evidence")
    if message.state in {
        OutboundDeliveryState.PENDING,
        OutboundDeliveryState.QUEUED,
    } and (
        message.completed_at_ms is not None or message.draft_saved_at_ms is not None
    ):
        raise ValueError("non-terminal outbound message cannot be terminal")
    if message.state is OutboundDeliveryState.REJECTED and any(
        value is not None
        for value in (
            message.provider_message_id,
            message.provider_receipt_ref,
            message.provider_attempted_at_ms,
        )
    ):
        raise ValueError("rejected outbound message cannot contain provider evidence")
    if (
        message.state
        in {
            OutboundDeliveryState.SENT,
            OutboundDeliveryState.PARTIAL,
            OutboundDeliveryState.FAILED,
            OutboundDeliveryState.UNKNOWN,
            OutboundDeliveryState.REJECTED,
        }
        and message.completed_at_ms is None
    ):
        raise ValueError("terminal outbound message requires completed_at_ms")
    if (
        message.state is OutboundDeliveryState.REJECTED
        and message.draft_saved_at_ms is None
    ):
        raise ValueError("rejected outbound message requires draft_saved_at_ms")
    if (
        message.state in {OutboundDeliveryState.SENT, OutboundDeliveryState.PARTIAL}
        and message.provider_message_id is None
        and message.provider_receipt_ref is None
    ):
        raise ValueError("delivered outbound message requires a provider receipt")


def _validate_outbound_insert(message: OutboundMessage) -> None:
    if message.state is not OutboundDeliveryState.DRAFT:
        raise ValueError("a new outbound message must start in draft state")
    if message.fresh_check_state is not FreshCheckState.REQUIRED:
        raise ValueError("a new outbound draft requires a required fresh check")


def _validate_outbound_update(
    existing: OutboundMessage,
    incoming: OutboundMessage,
) -> OutboundMessage:
    candidate = existing
    sequence_changed = (
        incoming.snapshot_seq != existing.snapshot_seq
        or incoming.current_inbound_seq != existing.current_inbound_seq
    )
    fresh_state_changed = incoming.fresh_check_state is not existing.fresh_check_state
    if existing.fresh_check_state is FreshCheckState.REQUIRED:
        if fresh_state_changed or sequence_changed:
            candidate = existing.record_fresh_check(
                incoming.fresh_check_state,
                snapshot_seq=incoming.snapshot_seq,
                current_inbound_seq=incoming.current_inbound_seq,
            )
    elif fresh_state_changed or sequence_changed:
        raise ValueError("outbound fresh-check evidence cannot change")

    if candidate.state is incoming.state:
        transitioned = candidate
    else:
        transitioned = candidate.transition_to(
            incoming.state,
            at_ms=_outbound_transition_time(incoming),
            provider_message_id=incoming.provider_message_id,
            provider_receipt_ref=incoming.provider_receipt_ref,
            error_kind=incoming.error_kind,
            error_message=incoming.error_message,
            next_action=incoming.next_action,
        )
    if (
        transitioned.completed_at_ms is not None
        and incoming.completed_at_ms is not None
        and transitioned.completed_at_ms != incoming.completed_at_ms
    ):
        raise ValueError("outbound completion time cannot change")
    if (
        transitioned.draft_saved_at_ms is not None
        and incoming.draft_saved_at_ms is not None
        and transitioned.draft_saved_at_ms != incoming.draft_saved_at_ms
    ):
        raise ValueError("outbound draft time cannot change")
    return replace(
        transitioned,
        provider_message_id=_merge_optional_text(
            transitioned.provider_message_id,
            incoming.provider_message_id,
            "provider_message_id",
        ),
        provider_receipt_ref=_merge_optional_text(
            transitioned.provider_receipt_ref,
            incoming.provider_receipt_ref,
            "provider_receipt_ref",
        ),
        provider_attempted_at_ms=_merge_timestamp(
            transitioned.provider_attempted_at_ms,
            incoming.provider_attempted_at_ms,
            "provider_attempted_at_ms",
        ),
        completed_at_ms=transitioned.completed_at_ms
        if transitioned.completed_at_ms is not None
        else incoming.completed_at_ms,
        draft_saved_at_ms=transitioned.draft_saved_at_ms
        if transitioned.draft_saved_at_ms is not None
        else incoming.draft_saved_at_ms,
        error_kind=incoming.error_kind or transitioned.error_kind,
        error_message=incoming.error_message or transitioned.error_message,
        next_action=incoming.next_action or transitioned.next_action,
        metadata=incoming.metadata,
    )


def _outbound_transition_time(message: OutboundMessage) -> int:
    return (
        message.completed_at_ms
        or message.draft_saved_at_ms
        or message.provider_attempted_at_ms
        or message.created_at_ms
    )


def _merge_optional_text(
    existing: str | None,
    incoming: str | None,
    field_name: str,
) -> str | None:
    if existing is not None and incoming is not None and existing != incoming:
        raise ValueError(f"outbound {field_name} cannot change")
    return incoming or existing


def _merge_timestamp(
    existing: int | None,
    incoming: int | None,
    field_name: str,
) -> int | None:
    if existing is not None and incoming is not None and existing != incoming:
        raise ValueError(f"outbound {field_name} cannot change")
    return incoming if incoming is not None else existing


def _validate_runtime_event_input(event: RuntimeEvent) -> None:
    if not isinstance(event, RuntimeEvent):
        raise TypeError("event must be a RuntimeEvent")
    if not isinstance(event.state, RuntimeEventState):
        raise TypeError("runtime event state is invalid")
    for value, field_name in (
        (event.node_id, "node_id"),
        (event.channel, "channel"),
        (event.runtime, "runtime"),
        (event.channel_session_id, "channel_session_id"),
        (event.bcn_session_id, "bcn_session_id"),
        (event.runtime_session_id, "runtime_session_id"),
        (event.turn_id, "turn_id"),
        (event.request_id, "request_id"),
        (event.command_id, "command_id"),
        (event.outbound_message_id, "outbound_message_id"),
        (event.error_kind, "error_kind"),
        (event.error_type, "error_type"),
        (event.error_message, "error_message"),
        (event.traceback_ref, "traceback_ref"),
    ):
        _validate_optional_input_text(value, field_name)


def _same_runtime_event_payload(
    existing: RuntimeEvent,
    incoming: RuntimeEvent,
) -> bool:
    return replace(existing, event_seq=incoming.event_seq) == incoming


def _validate_optional_input_text(value: object, field_name: str) -> None:
    if value is not None and (not isinstance(value, str) or not value):
        raise ValueError(f"{field_name} must be a non-empty string when present")


def _validate_runtime_event_references(
    storage: MemoryStorage,
    event: RuntimeEvent,
) -> None:
    channel_session = None
    if event.channel_session_id is not None:
        channel_session = storage.channel_sessions.get(event.channel_session_id)
        if channel_session is None:
            raise ValueError(f"unknown channel session: {event.channel_session_id}")
        if event.channel is not None and event.channel != channel_session.channel:
            raise ValueError("runtime event channel binding does not match")
    bcn_session = None
    if event.bcn_session_id is not None:
        bcn_session = storage.bcn_sessions.get(event.bcn_session_id)
        if bcn_session is None:
            raise ValueError(f"unknown bcn session: {event.bcn_session_id}")
        if (
            event.channel_session_id is not None
            and bcn_session.channel_session_id != event.channel_session_id
        ):
            raise ValueError("runtime event bcn/channel binding does not match")
        if event.channel is not None:
            channel_session = channel_session or storage.channel_sessions.get(
                bcn_session.channel_session_id
            )
            if channel_session is not None and channel_session.channel != event.channel:
                raise ValueError("runtime event channel binding does not match")
    runtime_session = None
    if event.runtime_session_id is not None:
        runtime_session = storage.runtime_sessions.get(event.runtime_session_id)
        if runtime_session is None:
            raise ValueError(f"unknown runtime session: {event.runtime_session_id}")
        if (
            event.bcn_session_id is not None
            and runtime_session.bcn_session_id != event.bcn_session_id
        ):
            raise ValueError("runtime event runtime/bcn binding does not match")
        if (
            event.channel_session_id is not None
            and runtime_session.channel_session_id != event.channel_session_id
        ):
            raise ValueError("runtime event runtime/channel binding does not match")
        if event.runtime is not None and event.runtime != runtime_session.runtime:
            raise ValueError("runtime event runtime name does not match")
        if (
            event.channel is not None
            and storage.channel_sessions[runtime_session.channel_session_id].channel
            != event.channel
        ):
            raise ValueError("runtime event channel binding does not match")
    if event.turn_id is not None:
        turn = storage.runtime_turns.get(event.turn_id)
        if turn is None:
            raise ValueError(f"unknown runtime turn: {event.turn_id}")
        if (
            event.runtime_session_id is not None
            and turn.session_id != event.runtime_session_id
        ):
            raise ValueError("runtime event turn/runtime binding does not match")
        if runtime_session is None:
            runtime_session = storage.runtime_sessions.get(turn.session_id)
        if (
            event.bcn_session_id is not None
            and runtime_session is not None
            and runtime_session.bcn_session_id != event.bcn_session_id
        ):
            raise ValueError("runtime event turn/bcn binding does not match")
        if runtime_session is not None:
            if (
                event.channel_session_id is not None
                and runtime_session.channel_session_id != event.channel_session_id
            ):
                raise ValueError("runtime event turn/channel binding does not match")
            if event.runtime is not None and runtime_session.runtime != event.runtime:
                raise ValueError("runtime event turn/runtime name does not match")
            if (
                event.channel is not None
                and storage.channel_sessions[runtime_session.channel_session_id].channel
                != event.channel
            ):
                raise ValueError("runtime event turn/channel binding does not match")
    if event.outbound_message_id is not None:
        outbound = storage.outbound_messages.get(event.outbound_message_id)
        if outbound is None:
            raise ValueError(f"unknown outbound message: {event.outbound_message_id}")
        if (
            event.bcn_session_id is not None
            and outbound.session_id != event.bcn_session_id
        ):
            raise ValueError("runtime event outbound/bcn binding does not match")
        if (
            event.channel_session_id is not None
            and outbound.channel_session_id != event.channel_session_id
        ):
            raise ValueError("runtime event outbound/channel binding does not match")
        if (
            event.channel is not None
            and storage.channel_sessions[outbound.channel_session_id].channel
            != event.channel
        ):
            raise ValueError("runtime event outbound/channel binding does not match")
    if (
        event.inbound_seq is not None
        and event.bcn_session_id is not None
        and not any(
            message.seq == event.inbound_seq
            for message in storage.inbound_messages.get(event.bcn_session_id, [])
        )
    ):
        raise ValueError(
            f"unknown inbound sequence for bcn session: {event.inbound_seq}"
        )


def _validate_channel_session_update(
    existing: ChannelSession,
    incoming: ChannelSession,
) -> ChannelSession:
    if (
        existing.channel != incoming.channel
        or existing.provider_thread_id != incoming.provider_thread_id
        or existing.created_at_ms != incoming.created_at_ms
    ):
        raise ValueError("channel session identity cannot change")
    _validate_updated_at(existing.updated_at_ms, incoming.updated_at_ms)
    return replace(
        existing,
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
    return replace(
        existing,
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
        or existing.runtime != incoming.runtime
        or existing.workspace_id != incoming.workspace_id
        or existing.created_at_ms != incoming.created_at_ms
    ):
        raise ValueError("runtime session binding cannot change")
    _validate_updated_at(existing.updated_at_ms, incoming.updated_at_ms)
    return replace(
        existing,
        updated_at_ms=incoming.updated_at_ms,
        provider_thread_id=incoming.provider_thread_id,
        metadata=incoming.metadata,
    )
