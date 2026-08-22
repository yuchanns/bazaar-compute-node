from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager
from copy import deepcopy
from dataclasses import replace
from types import TracebackType
from typing import Self, cast
from uuid import uuid7

from bazaar_compute_node.core.inbox import InboxTargetPage
from bazaar_compute_node.core.models import (
    BcnSession,
    ChannelSession,
    ConsumerCursor,
    FreshCheckState,
    InboundMessage,
    InboxTargetSummary,
    OutboundDeliveryState,
    OutboundMessage,
    RuntimeAttempt,
)
from bazaar_compute_node.core.storage import (
    InboxTargetResolutionError,
    IStorage,
    IStorageScope,
    IStorageTransaction,
)


class MemoryStorage(IStorage):
    """Transactional in-memory storage for behavior-level integration tests."""

    @property
    def name(self) -> str:
        return "test"

    def __init__(self) -> None:
        self.channel_sessions: dict[str, ChannelSession] = {}
        self.bcn_sessions: dict[str, BcnSession] = {}
        self.runtime_attempts: dict[str, RuntimeAttempt] = {}
        self.cursors: dict[str, ConsumerCursor] = {}
        self.inbound_messages: dict[str, list[InboundMessage]] = {}
        self.outbound_messages: dict[str, OutboundMessage] = {}
        self.started = False
        self.stopped = False
        self._lock = asyncio.Lock()

    async def start(self, *, timeout: float) -> None:
        del timeout
        self.started = True
        self.stopped = False

    async def stop(self, *, timeout: float) -> None:
        del timeout
        self.stopped = True

    def scope(self, agent_id: str, agent_name: str) -> IStorageScope:
        return _MemoryStorageScope(self, agent_id, agent_name)

    def transaction(self) -> AbstractAsyncContextManager[IStorageTransaction]:
        return self._transaction_for_agent(None)

    def _transaction_for_agent(
        self, agent_id: str | None
    ) -> AbstractAsyncContextManager[IStorageTransaction]:
        return cast(
            AbstractAsyncContextManager[IStorageTransaction],
            _MemoryStorageTransaction(self, agent_id=agent_id),
        )


class _MemoryStorageScope(IStorageScope):
    def __init__(self, storage: MemoryStorage, agent_id: str, agent_name: str) -> None:
        if not isinstance(agent_id, str) or not agent_id:
            raise ValueError("agent_id must be a non-empty string")
        if not isinstance(agent_name, str) or not agent_name:
            raise ValueError("agent_name must be a non-empty string")
        self._storage = storage
        self._agent_id = agent_id
        self._agent_name = agent_name

    @property
    def name(self) -> str:
        return self._storage.name

    @property
    def agent_id(self) -> str:
        return self._agent_id

    @property
    def agent_name(self) -> str:
        return self._agent_name

    async def start(self, *, timeout: float) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if not self._storage.started:
            raise RuntimeError("shared memory storage is not started")

    async def stop(self, *, timeout: float) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")

    def scope(self, agent_id: str, agent_name: str) -> IStorageScope:
        if agent_id != self.agent_id or agent_name != self.agent_name:
            raise ValueError("a test storage scope cannot be rebound")
        return self

    def transaction(self) -> AbstractAsyncContextManager[IStorageTransaction]:
        return self._storage._transaction_for_agent(self.agent_id)


_Snapshot = tuple[
    dict[str, ChannelSession],
    dict[str, BcnSession],
    dict[str, RuntimeAttempt],
    dict[str, ConsumerCursor],
    dict[str, list[InboundMessage]],
    dict[str, OutboundMessage],
]


class _MemoryStorageTransaction:
    def __init__(self, storage: MemoryStorage, *, agent_id: str | None = None) -> None:
        self._storage = storage
        self._agent_id = agent_id
        self._snapshot: _Snapshot | None = None

    async def __aenter__(self) -> Self:
        await self._storage._lock.acquire()
        self._snapshot = (
            deepcopy(self._storage.channel_sessions),
            deepcopy(self._storage.bcn_sessions),
            deepcopy(self._storage.runtime_attempts),
            deepcopy(self._storage.cursors),
            deepcopy(self._storage.inbound_messages),
            deepcopy(self._storage.outbound_messages),
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        del exc_value, traceback
        if exc_type is not None and self._snapshot is not None:
            (
                self._storage.channel_sessions,
                self._storage.bcn_sessions,
                self._storage.runtime_attempts,
                self._storage.cursors,
                self._storage.inbound_messages,
                self._storage.outbound_messages,
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
        session = self._storage.bcn_sessions.get(session_id)
        if session is None or not self._in_scope(session):
            return None
        return session

    async def list_inbox_targets(
        self, *, limit: int = 100, offset: int = 0
    ) -> InboxTargetPage:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be a positive integer")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("offset must be a non-negative integer")

        summaries = tuple(
            sorted(
                (
                    self._inbox_target_summary(session)
                    for session in self._scoped_bcn_sessions()
                ),
                key=lambda summary: (-summary.last_activity_at_ms, summary.session_id),
            )
        )
        targets = summaries[offset : offset + limit]
        return InboxTargetPage(
            targets=targets,
            total=len(summaries),
            offset=offset,
        )

    async def resolve_inbox_target(self, target: str) -> BcnSession:
        if not isinstance(target, str) or not target:
            raise ValueError("target must be a non-empty string")
        matches = []
        for session in self._scoped_bcn_sessions():
            channel_session = self._storage.channel_sessions.get(
                session.channel_session_id
            )
            if channel_session is None:
                continue
            derived_target = f"{channel_session.target_kind.value}:{channel_session.id}"
            messages = self._storage.inbound_messages.get(session.id, [])
            if derived_target == target or any(
                message.canonical_target == target for message in messages
            ):
                matches.append(session)
        if len(matches) != 1:
            raise InboxTargetResolutionError(
                "inbox target does not resolve to exactly one owned session"
            )
        return matches[0]

    async def find_bcn_session(self, channel_session_id: str) -> BcnSession | None:
        matches = [
            session
            for session in self._storage.bcn_sessions.values()
            if session.channel_session_id == channel_session_id
            and self._in_scope(session)
        ]
        if len(matches) > 1:
            raise ValueError(
                "multiple bcn sessions are bound to the channel session: "
                f"{channel_session_id}"
            )
        return matches[0] if matches else None

    async def get_runtime_attempt(self, turn_id: str) -> RuntimeAttempt | None:
        return self._storage.runtime_attempts.get(turn_id)

    async def get_consumer_cursor(self, session_id: str) -> ConsumerCursor | None:
        return self._storage.cursors.get(session_id)

    async def get_latest_inbound_seq(self, session_id: str) -> int:
        messages = self._storage.inbound_messages.get(session_id, [])
        return messages[-1].seq if messages else 0

    def _in_scope(self, session: BcnSession) -> bool:
        return self._agent_id is None or session.workspace_id == self._agent_id

    def _scoped_bcn_sessions(self) -> tuple[BcnSession, ...]:
        return tuple(
            session
            for session in self._storage.bcn_sessions.values()
            if self._in_scope(session)
        )

    def _inbox_target_summary(self, session: BcnSession) -> InboxTargetSummary:
        channel_session = self._storage.channel_sessions.get(session.channel_session_id)
        if channel_session is None:
            raise ValueError(f"unknown channel session: {session.channel_session_id}")
        messages = self._storage.inbound_messages.get(session.id, [])
        latest = max(
            messages,
            key=lambda message: (message.seq, message.message_id),
            default=None,
        )
        target = (
            latest.canonical_target
            if latest is not None
            else f"{channel_session.target_kind.value}:{channel_session.id}"
        )
        cursor = self._storage.cursors.get(session.id)
        delivered_through_seq = cursor.delivered_through_seq if cursor else 0
        pending_count = sum(
            message.notifies_runtime and message.seq > delivered_through_seq
            for message in messages
        )
        last_activity_at_ms = next(
            (
                value
                for value in (
                    session.last_activity_at_ms,
                    latest.received_at_ms if latest is not None else None,
                    channel_session.last_inbound_at_ms,
                    channel_session.last_outbound_at_ms,
                    session.updated_at_ms,
                    channel_session.updated_at_ms,
                    session.created_at_ms,
                    channel_session.created_at_ms,
                    0,
                )
                if value is not None
            ),
            0,
        )
        return InboxTargetSummary(
            target=target,
            session_id=session.id,
            target_kind=channel_session.target_kind,
            current=False,
            pending_count=pending_count,
            last_activity_at_ms=last_activity_at_ms,
            latest_message_id=latest.message_id if latest is not None else None,
            latest_sender=latest.sender if latest is not None else None,
            latest_provider_time_ms=(
                latest.provider_time_ms if latest is not None else None
            ),
            latest_received_at_ms=(
                latest.received_at_ms if latest is not None else None
            ),
        )

    async def find_inbound_message(
        self,
        channel: str,
        provider_thread_id: str,
        provider_message_id: str,
    ) -> InboundMessage | None:
        matches = [
            message
            for messages in self._storage.inbound_messages.values()
            for message in messages
            if message.channel == channel
            and message.provider_thread_id == provider_thread_id
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

    def _require_workspace(self, workspace_id: str) -> None:
        if self._agent_id is not None and workspace_id != self._agent_id:
            raise ValueError(
                "session workspace does not match the scoped Agent workspace"
            )

    async def save_runtime_attempt(self, attempt: object) -> None:
        if not isinstance(attempt, RuntimeAttempt):
            raise TypeError("attempt must be a RuntimeAttempt")
        existing = self._storage.runtime_attempts.get(attempt.turn_id)
        if existing is not None and existing != attempt:
            raise ValueError("runtime attempt is immutable")
        self._storage.runtime_attempts[attempt.turn_id] = attempt

    async def append_inbound_message(self, message: InboundMessage) -> InboundMessage:
        messages = self._storage.inbound_messages.setdefault(message.session_id, [])
        provider_matches = [
            existing
            for session_messages in self._storage.inbound_messages.values()
            for existing in session_messages
            if (
                existing.channel == message.channel
                and existing.provider_thread_id == message.provider_thread_id
                and existing.provider_message_id == message.provider_message_id
            )
        ]
        if provider_matches:
            return provider_matches[0]
        for existing in (
            existing
            for session_messages in self._storage.inbound_messages.values()
            for existing in session_messages
        ):
            if existing.message_id == message.message_id:
                if existing != message:
                    raise ValueError("duplicate message id has different content")
                return existing
        expected_seq = messages[-1].seq + 1 if messages else 1
        if message.seq != expected_seq:
            raise ValueError(
                f"inbound sequence must be contiguous: expected {expected_seq}, got {message.seq}"
            )
        if message.reply_to_message_id is not None:
            referenced = next(
                (
                    existing
                    for session_messages in self._storage.inbound_messages.values()
                    for existing in session_messages
                    if existing.message_id == message.reply_to_message_id
                ),
                None,
            )
            if referenced is None:
                raise ValueError("reply_to_message_id does not reference a message")
            if referenced.session_id != message.session_id:
                raise ValueError("reply_to_message_id must belong to the same session")
            if referenced.seq >= message.seq:
                raise ValueError(
                    "reply_to_message_id must reference an earlier message"
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


def _validate_updated_at(existing: int, incoming: int) -> None:
    if incoming < existing:
        raise ValueError("session updated_at_ms cannot move backwards")


def _validate_outbound_message_input(message: object) -> None:
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


def _validate_optional_input_text(value: object, field_name: str) -> None:
    if value is not None and (not isinstance(value, str) or not value):
        raise ValueError(f"{field_name} must be a non-empty string when present")


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
