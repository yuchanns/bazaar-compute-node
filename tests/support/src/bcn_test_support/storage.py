from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
from types import TracebackType
from typing import Self, cast
from uuid import uuid7

from bazaar_compute_node.core.inbox import InboxTargetPage
from bazaar_compute_node.core.models import (
    BcnSession,
    ChannelSession,
    ChannelTargetKind,
    ConsumerCursor,
    InboundAttachment,
    InboxTargetSummary,
    Message,
    MessageDirection,
    OutboundDeliveryState,
    RuntimeAttempt,
    SenderIdentity,
)
from bazaar_compute_node.core.storage import (
    InboxTargetResolutionError,
    IStorageScope,
    RecordInboundResult,
    StorageOperationMixin,
    UnreadMessageOwner,
)


class MemoryStorage:
    """Transactional in-memory storage for behavior-level integration tests."""

    @property
    def name(self) -> str:
        return "test"

    def __init__(self) -> None:
        self.channel_sessions: dict[str, ChannelSession] = {}
        self.bcn_sessions: dict[str, BcnSession] = {}
        self.runtime_attempts: dict[str, RuntimeAttempt] = {}
        self.cursors: dict[str, ConsumerCursor] = {}
        self.messages: dict[str, list[Message]] = {}
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
        return cast(IStorageScope, _MemoryStorageScope(self, agent_id, agent_name))

    def _operation_for_agent(
        self,
        agent_id: str | None,
        agent_name: str | None = None,
    ) -> _MemoryStorageTransaction:
        return _MemoryStorageTransaction(
            self,
            agent_id=agent_id,
            agent_name=agent_name,
        )

    async def _invoke(
        self,
        agent_id: str | None,
        agent_name: str | None,
        method_name: str,
        *args: object,
        **kwargs: object,
    ) -> object:
        async with self._operation_for_agent(agent_id, agent_name) as operation:
            method = getattr(operation, method_name)
            return await method(*args, **kwargs)

    def _has_operation(self, method_name: str) -> bool:
        return hasattr(type(self._operation_for_agent(None)), method_name)

    def __getattr__(self, method_name: str):
        if method_name.startswith("_") or not self._has_operation(method_name):
            raise AttributeError(method_name)

        async def invoke(*args: object, **kwargs: object) -> object:
            return await self._invoke(None, None, method_name, *args, **kwargs)

        return invoke


class _MemoryStorageScope:
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
        return cast(IStorageScope, self)

    def __getattr__(self, method_name: str):
        if method_name.startswith("_") or not self._storage._has_operation(method_name):
            raise AttributeError(method_name)

        async def invoke(*args: object, **kwargs: object) -> object:
            return await self._storage._invoke(
                self.agent_id,
                self.agent_name,
                method_name,
                *args,
                **kwargs,
            )

        return invoke


_Snapshot = tuple[
    dict[str, ChannelSession],
    dict[str, BcnSession],
    dict[str, RuntimeAttempt],
    dict[str, ConsumerCursor],
    dict[str, list[Message]],
]


class _MemoryStorageTransaction(StorageOperationMixin):
    def __init__(
        self,
        storage: MemoryStorage,
        *,
        agent_id: str | None = None,
        agent_name: str | None = None,
    ) -> None:
        self._storage = storage
        self._agent_id = agent_id
        self._agent_name = agent_name
        self._snapshot: _Snapshot | None = None

    async def __aenter__(self) -> Self:
        await self._storage._lock.acquire()
        self._snapshot = (
            deepcopy(self._storage.channel_sessions),
            deepcopy(self._storage.bcn_sessions),
            deepcopy(self._storage.runtime_attempts),
            deepcopy(self._storage.cursors),
            deepcopy(self._storage.messages),
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
                self._storage.messages,
            ) = self._snapshot
        self._storage._lock.release()
        return False

    async def record_inbound(
        self,
        message: Message,
        *,
        now_ms: int,
    ) -> RecordInboundResult:
        channel, provider_thread_id, provider_message_id = message.inbound_identity()
        existing_message = await self.find_message(
            channel,
            provider_thread_id,
            provider_message_id,
            direction=MessageDirection.INBOUND,
        )
        if existing_message is not None:
            message = cast(Message, existing_message)
        channel_session = await self.find_channel_session(
            channel=channel,
            provider_thread_id=provider_thread_id,
        )
        channel_session_created = channel_session is None
        if channel_session is None:
            channel_session = ChannelSession(
                id=message.channel_session_id,
                channel=channel,
                provider_thread_id=provider_thread_id,
                created_at_ms=now_ms,
                updated_at_ms=now_ms,
                target_kind=message.target_kind,
                following=(
                    message.target_kind is ChannelTargetKind.DM
                    or message.mentions_agent
                ),
            )
            await self.save_channel_session(channel_session)
        elif (
            existing_message is None
            and message.mentions_agent
            and not channel_session.following
        ):
            channel_session = replace(
                channel_session,
                following=True,
                updated_at_ms=now_ms,
            )
            await self.save_channel_session(channel_session)

        bcn_session = await self.find_bcn_session(channel_session.id)
        bcn_session_created = bcn_session is None
        if bcn_session is None:
            bcn_session = BcnSession(
                id=message.session_id,
                channel_session_id=channel_session.id,
                workspace_id=self._agent_id or message.session_id,
                created_at_ms=now_ms,
                updated_at_ms=now_ms,
            )
            await self.save_bcn_session(bcn_session)

        if existing_message is None:
            notifies_runtime = message.notifies_runtime and (
                message.target_kind is ChannelTargetKind.DM
                or channel_session.following
                or message.mentions_agent
            )
            canonical_target = message.target
            if channel_session.id != message.channel_session_id:
                canonical_target = (
                    f"{channel_session.target_kind.value}:{channel_session.id}"
                )
            message = replace(
                message,
                session_id=bcn_session.id,
                channel_session_id=channel_session.id,
                target=canonical_target,
                notifies_runtime=notifies_runtime,
            )

        if (
            message.notifies_runtime
            and await self.get_consumer_cursor(bcn_session.id) is None
        ):
            await self.save_consumer_cursor(ConsumerCursor(session_id=bcn_session.id))

        if existing_message is None:
            message = await self.save_message(message)
            channel_session = replace(
                channel_session,
                last_inbound_at_ms=message.received_at_ms,
                updated_at_ms=now_ms,
            )
            bcn_session = replace(
                bcn_session,
                last_activity_at_ms=message.received_at_ms,
                updated_at_ms=now_ms,
            )
            await self.save_channel_session(channel_session)
            await self.save_bcn_session(bcn_session)

        return RecordInboundResult(
            channel_session=channel_session,
            bcn_session=bcn_session,
            message=message,
            channel_session_created=channel_session_created,
            bcn_session_created=bcn_session_created,
            message_created=existing_message is None,
        )

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
        matches = []
        for session in self._scoped_bcn_sessions():
            channel_session = self._storage.channel_sessions.get(
                session.channel_session_id
            )
            if channel_session is None:
                continue
            derived_target = f"{channel_session.target_kind.value}:{channel_session.id}"
            messages = self._storage.messages.get(session.id, [])
            if derived_target == target or any(
                message.target == target for message in messages
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

    async def get_latest_message_seq(
        self,
        session_id: str,
        *,
        direction: MessageDirection | None = None,
        delivery_states: frozenset[OutboundDeliveryState] | None = None,
    ) -> int:
        messages = self._filtered_messages(
            session_id,
            direction=direction,
            delivery_states=delivery_states,
        )
        return messages[-1].seq if messages else 0

    async def get_latest_message(
        self,
        session_id: str,
        *,
        direction: MessageDirection | None = None,
        delivery_states: frozenset[OutboundDeliveryState] | None = None,
    ) -> Message | None:
        messages = self._filtered_messages(
            session_id,
            direction=direction,
            delivery_states=delivery_states,
        )
        return messages[-1] if messages else None

    async def count_messages(
        self,
        session_id: str,
        *,
        after_seq: int | None = None,
        target: str | None = None,
        direction: MessageDirection | None = None,
        delivery_states: frozenset[OutboundDeliveryState] | None = None,
        notifying_only: bool = False,
    ) -> int:
        messages = self._filtered_messages(
            session_id,
            direction=direction,
            delivery_states=delivery_states,
        )
        return sum(
            (after_seq is None or message.seq > after_seq)
            and (target is None or message.target == target)
            and (not notifying_only or message.notifies_runtime)
            for message in messages
        )

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
        messages = self._filtered_messages(
            session.id,
            delivery_states=frozenset(
                {OutboundDeliveryState.QUEUED, OutboundDeliveryState.SENT}
            ),
        )
        latest = max(
            messages,
            key=lambda message: (message.seq, message.message_id),
            default=None,
        )
        latest_activity_at_ms = (
            None
            if latest is None
            else (
                latest.received_at_ms
                if latest.received_at_ms is not None
                else latest.created_at_ms
            )
        )
        target = (
            latest.target
            if latest is not None
            else f"{channel_session.target_kind.value}:{channel_session.id}"
        )
        cursor = self._storage.cursors.get(session.id)
        delivered_through_seq = cursor.delivered_through_seq if cursor else 0
        pending_count = sum(
            message.direction is MessageDirection.INBOUND
            and message.notifies_runtime
            and message.seq > delivered_through_seq
            for message in messages
        )
        last_activity_at_ms = max(
            value
            for value in (
                session.last_activity_at_ms,
                latest_activity_at_ms,
                channel_session.last_inbound_at_ms,
                channel_session.last_outbound_at_ms,
                session.updated_at_ms,
                channel_session.updated_at_ms,
                session.created_at_ms,
                channel_session.created_at_ms,
                0,
            )
            if value is not None
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
            latest_received_at_ms=latest_activity_at_ms,
        )

    async def find_message(
        self,
        channel: str,
        provider_thread_id: str,
        provider_message_id: str,
        *,
        direction: MessageDirection | None = None,
    ) -> Message | None:
        matches = [
            message
            for messages in self._storage.messages.values()
            for message in messages
            if message.channel == channel
            and message.provider_thread_id == provider_thread_id
            and message.provider_message_id == provider_message_id
            and (direction is None or message.direction is direction)
        ]
        if len(matches) > 1:
            raise ValueError("multiple rows violate provider message identity")
        return matches[0] if matches else None

    async def list_ready_attachment_paths(self) -> tuple[str, ...]:
        return tuple(
            attachment.relative_path
            for messages in self._storage.messages.values()
            for message in messages
            for attachment in message.attachments
            if isinstance(attachment, InboundAttachment)
            and attachment.state == "ready"
            and attachment.relative_path is not None
        )

    async def list_unread_message_owners(self) -> tuple[UnreadMessageOwner, ...]:
        owners = []
        for session in self._scoped_bcn_sessions():
            cursor = self._storage.cursors.get(session.id)
            delivered_through_seq = cursor.delivered_through_seq if cursor else 0
            unread = [
                message
                for message in self._filtered_messages(
                    session.id,
                    direction=MessageDirection.INBOUND,
                )
                if message.notifies_runtime and message.seq > delivered_through_seq
            ]
            if unread:
                owners.append(
                    UnreadMessageOwner(
                        agent_id=session.workspace_id,
                        trigger_message=unread[-1],
                    )
                )
        return tuple(
            sorted(
                owners,
                key=lambda owner: (owner.agent_id, owner.owner_session_id),
            )
        )

    async def list_messages(
        self,
        session_id: str,
        *,
        after_seq: int | None = None,
        target: str | None = None,
        around_message_id: str | None = None,
        direction: MessageDirection | None = None,
        delivery_states: frozenset[OutboundDeliveryState] | None = None,
        notifying_only: bool = False,
        latest: bool = False,
        limit: int = 100,
    ) -> tuple[Message, ...]:
        messages = self._filtered_messages(
            session_id,
            direction=direction,
            delivery_states=delivery_states,
        )
        if after_seq is not None:
            messages = [message for message in messages if message.seq > after_seq]
        if target is not None:
            messages = [message for message in messages if message.target == target]
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
            messages = messages[-limit:] if latest else messages[:limit]
        return tuple(messages)

    def _filtered_messages(
        self,
        session_id: str,
        *,
        direction: MessageDirection | None = None,
        delivery_states: frozenset[OutboundDeliveryState] | None = None,
    ) -> list[Message]:
        messages = list(self._storage.messages.get(session_id, []))
        if direction is not None:
            messages = [
                message for message in messages if message.direction is direction
            ]
        if delivery_states is not None and direction is not MessageDirection.INBOUND:
            messages = [
                message
                for message in messages
                if message.direction is MessageDirection.INBOUND
                or (
                    message.direction is MessageDirection.OUTBOUND
                    and message.delivery_state in delivery_states
                )
            ]
        return messages

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

    async def save_message(self, message: Message) -> Message:
        if message.direction is MessageDirection.INBOUND:
            return await self._save_inbound_message(message)
        return await self._save_outbound_message(message)

    async def _save_inbound_message(self, message: Message) -> Message:
        if message.direction is not MessageDirection.INBOUND:
            raise ValueError("inbound persistence requires an inbound message")
        messages = self._storage.messages.setdefault(message.session_id, [])
        provider_matches = (
            [
                existing
                for session_messages in self._storage.messages.values()
                for existing in session_messages
                if existing.direction is MessageDirection.INBOUND
                and existing.channel == message.channel
                and existing.provider_thread_id == message.provider_thread_id
                and existing.provider_message_id == message.provider_message_id
            ]
            if message.provider_message_id is not None
            else []
        )
        if provider_matches:
            return provider_matches[0]
        for existing in (
            existing
            for session_messages in self._storage.messages.values()
            for existing in session_messages
        ):
            if existing.message_id == message.message_id:
                if existing != message:
                    raise ValueError("duplicate message id has different content")
                return existing
        message = replace(message, seq=self._next_message_seq())
        if message.reply_to_message_id is not None:
            referenced = next(
                (
                    existing
                    for session_messages in self._storage.messages.values()
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

    async def get_message(
        self,
        message_id: str,
        *,
        direction: MessageDirection | None = None,
    ) -> Message | None:
        matches = [
            message
            for messages in self._storage.messages.values()
            for message in messages
            if message.message_id == message_id
            and (direction is None or message.direction is direction)
        ]
        if len(matches) > 1:
            raise ValueError("multiple rows violate message identity")
        return matches[0] if matches else None

    async def resolve_message(
        self,
        session_id: str,
        message_id: str,
        *,
        direction: MessageDirection | None = None,
        delivery_states: frozenset[OutboundDeliveryState] | None = None,
    ) -> Message | None:
        return next(
            (
                message
                for message in self._filtered_messages(
                    session_id,
                    direction=direction,
                    delivery_states=delivery_states,
                )
                if message.message_id == message_id
            ),
            None,
        )

    async def get_owned_message(
        self,
        agent_id: str,
        session_id: str,
        message_id: str,
        *,
        direction: MessageDirection | None = None,
    ) -> Message | None:
        if self._agent_id is not None and self._agent_id != agent_id:
            return None
        session = self._storage.bcn_sessions.get(session_id)
        if session is None or session.workspace_id != agent_id:
            return None
        return await self.resolve_message(
            session_id,
            message_id,
            direction=direction,
        )

    async def _save_outbound_message(self, message: Message) -> Message:
        _validate_outbound_message_input(message)
        bcn_session = self._storage.bcn_sessions.get(message.session_id)
        if bcn_session is None:
            raise ValueError(f"unknown bcn session: {message.session_id}")
        if message.channel_session_id not in self._storage.channel_sessions:
            raise ValueError(f"unknown channel session: {message.channel_session_id}")
        if bcn_session.channel_session_id != message.channel_session_id:
            raise ValueError("outbound message binding does not match bcn session")

        existing = await self.get_message(
            message.message_id,
            direction=MessageDirection.OUTBOUND,
        )
        if existing is None:
            channel_session = self._storage.channel_sessions[message.channel_session_id]
            canonical = replace(
                message,
                message_id=str(uuid7()),
                seq=self._next_message_seq(),
                channel=channel_session.channel,
                provider_thread_id=channel_session.provider_thread_id,
                sender=message.sender
                or SenderIdentity(name=self._agent_name or "Test Agent"),
                target_kind=channel_session.target_kind,
            )
            _validate_outbound_insert(canonical)
            self._storage.messages.setdefault(canonical.session_id, []).append(
                canonical
            )
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
        messages = self._storage.messages[canonical.session_id]
        index = next(
            index
            for index, existing_message in enumerate(messages)
            if existing_message.message_id == canonical.message_id
        )
        messages[index] = canonical
        return canonical

    def _next_message_seq(self) -> int:
        return (
            max(
                (
                    message.seq
                    for messages in self._storage.messages.values()
                    for message in messages
                ),
                default=0,
            )
            + 1
        )


def _validate_updated_at(existing: int, incoming: int) -> None:
    if incoming < existing:
        raise ValueError("session updated_at_ms cannot move backwards")


def _validate_outbound_message_input(message: object) -> None:
    if not isinstance(message, Message):
        raise TypeError("message must be a Message")
    if message.direction is not MessageDirection.OUTBOUND:
        raise ValueError("outbound persistence requires an outbound message")
    if not isinstance(message.delivery_state, OutboundDeliveryState):
        raise TypeError("outbound message state is invalid")
    delivery_state = message.delivery_state
    if not isinstance(message.body, str):
        raise TypeError("outbound body must be a string")
    for value, field_name in (
        (message.reply_to_message_id, "reply_to_message_id"),
        (message.provider_message_id, "provider_message_id"),
        (message.provider_receipt_ref, "provider_receipt_ref"),
        (message.error_kind, "error_kind"),
        (message.error_message, "error_message"),
    ):
        _validate_optional_input_text(value, field_name)
    created_at_ms = message.created_at_ms
    provider_attempted_at_ms = message.provider_attempted_at_ms
    assert created_at_ms is not None
    assert provider_attempted_at_ms is not None
    if provider_attempted_at_ms < created_at_ms:
        raise ValueError("outbound provider attempt cannot precede creation")
    if (
        message.completed_at_ms is not None
        and message.completed_at_ms < provider_attempted_at_ms
    ):
        raise ValueError("outbound completion cannot precede provider attempt")
    if (
        delivery_state
        in {
            OutboundDeliveryState.PENDING,
            OutboundDeliveryState.QUEUED,
        }
        and message.completed_at_ms is not None
    ):
        raise ValueError("non-terminal outbound message cannot be terminal")
    if (
        delivery_state
        in {
            OutboundDeliveryState.SENT,
            OutboundDeliveryState.PARTIAL,
            OutboundDeliveryState.FAILED,
            OutboundDeliveryState.UNKNOWN,
        }
        and message.completed_at_ms is None
    ):
        raise ValueError("terminal outbound message requires completed_at_ms")
    if (
        delivery_state in {OutboundDeliveryState.SENT, OutboundDeliveryState.PARTIAL}
        and message.provider_message_id is None
        and message.provider_receipt_ref is None
    ):
        raise ValueError("delivered outbound message requires a provider receipt")


def _validate_outbound_insert(message: Message) -> None:
    if message.delivery_state is not OutboundDeliveryState.PENDING:
        raise ValueError("a new outbound message must start in pending state")
    if any(
        value is not None
        for value in (
            message.provider_message_id,
            message.provider_receipt_ref,
            message.completed_at_ms,
            message.error_kind,
            message.error_message,
        )
    ):
        raise ValueError(
            "a new pending outbound message cannot contain result evidence"
        )


def _validate_outbound_update(
    existing: Message,
    incoming: Message,
) -> Message:
    incoming_state = incoming.delivery_state
    existing_state = existing.delivery_state
    if incoming_state is None or existing_state is None:
        raise RuntimeError("outbound message has no delivery state")
    if existing_state is incoming_state:
        transitioned = existing
    else:
        transitioned = existing.transition_to(
            incoming_state,
            at_ms=_outbound_transition_time(incoming),
            provider_message_id=incoming.provider_message_id,
            provider_receipt_ref=incoming.provider_receipt_ref,
            error_kind=incoming.error_kind,
            error_message=incoming.error_message,
        )
    if (
        transitioned.completed_at_ms is not None
        and incoming.completed_at_ms is not None
        and transitioned.completed_at_ms != incoming.completed_at_ms
    ):
        raise ValueError("outbound completion time cannot change")
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
        error_kind=incoming.error_kind or transitioned.error_kind,
        error_message=incoming.error_message or transitioned.error_message,
        metadata=incoming.metadata,
    )


def _outbound_transition_time(message: Message) -> int:
    transition_time = message.completed_at_ms or message.provider_attempted_at_ms
    if transition_time is None:
        raise RuntimeError("outbound message has no transition time")
    return transition_time


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
