from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
from types import TracebackType
from typing import Self, cast
from uuid import uuid7

from bazaar_compute_node.core.inbox import InboxTargetPage
from bazaar_compute_node.core.models import (
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
    Thread,
)
from bazaar_compute_node.core.storage import (
    InboxTargetResolutionError,
    IStorageScope,
    RecordInboundResult,
    ResolvedInboxTarget,
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
        self.threads: dict[str, Thread] = {}
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
    dict[str, Thread],
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
            deepcopy(self._storage.threads),
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
                self._storage.threads,
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
            if message.target_presentation is not None:
                channel_session = channel_session.with_target_presentation(
                    message.target_presentation,
                    updated_at_ms=now_ms,
                )
            await self.save_channel_session(channel_session)
        elif existing_message is None:
            if message.target_presentation is not None:
                channel_session = channel_session.with_target_presentation(
                    message.target_presentation,
                    updated_at_ms=now_ms,
                )
            if message.mentions_agent and not channel_session.following:
                channel_session = replace(
                    channel_session,
                    following=True,
                    updated_at_ms=now_ms,
                )

        thread = await self.find_thread(channel_session.id)
        thread_created = thread is None
        if thread is None:
            thread = Thread(
                id=message.thread_id,
                channel_session_id=channel_session.id,
                workspace_id=self._agent_id or message.thread_id,
                created_at_ms=now_ms,
                updated_at_ms=now_ms,
            )
            await self.save_thread(thread)

        if existing_message is None:
            notifies_runtime = message.notifies_runtime and (
                message.target_kind is ChannelTargetKind.DM
                or channel_session.following
                or message.mentions_agent
            )
            message = replace(
                message,
                thread_id=thread.id,
                channel_session_id=channel_session.id,
                target=channel_session.canonical_target,
                target_presentation=None,
                notifies_runtime=notifies_runtime,
            )

        if (
            message.notifies_runtime
            and await self.get_consumer_cursor(thread.id) is None
        ):
            await self.save_consumer_cursor(ConsumerCursor(thread_id=thread.id))

        if existing_message is None:
            message = await self.save_message(message)
            channel_session = replace(
                channel_session,
                last_inbound_at_ms=message.received_at_ms,
                updated_at_ms=now_ms,
            )
            thread = replace(
                thread,
                last_activity_at_ms=message.received_at_ms,
                updated_at_ms=now_ms,
            )
            await self.save_channel_session(channel_session)
            await self.save_thread(thread)

        return RecordInboundResult(
            channel_session=channel_session,
            thread=thread,
            message=message,
            channel_session_created=channel_session_created,
            thread_created=thread_created,
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

    async def get_channel_session(
        self, channel_session_id: str
    ) -> ChannelSession | None:
        return self._storage.channel_sessions.get(channel_session_id)

    async def get_thread(self, thread_id: str) -> Thread | None:
        session = self._storage.threads.get(thread_id)
        if session is None or not self._in_scope(session):
            return None
        return session

    async def list_thread_ids(self) -> tuple[str, ...]:
        return tuple(sorted(thread.id for thread in self._scoped_threads()))

    async def list_inbox_targets(
        self, *, limit: int | None = 100, offset: int = 0
    ) -> InboxTargetPage:
        summaries = tuple(
            sorted(
                (
                    self._inbox_target_summary(session)
                    for session in self._scoped_threads()
                ),
                key=lambda summary: (-summary.last_activity_at_ms, summary.thread_id),
            )
        )
        targets = (
            summaries[offset:] if limit is None else summaries[offset : offset + limit]
        )
        return InboxTargetPage(
            targets=targets,
            total=len(summaries),
            offset=offset,
        )

    async def list_unread_messages(self, *, limit: int) -> tuple[Message, ...]:
        return tuple((await self._unread_in_scope())[-limit:])

    async def _unread_in_scope(self) -> list[Message]:
        unread: list[Message] = []
        for session in self._scoped_threads():
            cursor = self._storage.cursors.get(session.id)
            delivered_through_seq = cursor.delivered_through_seq if cursor else 0
            unread.extend(
                message
                for message in self._filtered_messages(
                    session.id,
                    direction=MessageDirection.INBOUND,
                )
                if message.notifies_runtime and message.seq > delivered_through_seq
            )
        unread.sort(key=lambda message: message.seq)
        return unread

    async def count_unread_messages(self) -> int:
        return len(await self._unread_in_scope())

    async def resolve_inbox_target(self, raw_target: str) -> ResolvedInboxTarget:
        matches: list[tuple[Thread, ChannelSession]] = []
        for session in self._scoped_threads():
            channel_session = self._storage.channel_sessions.get(
                session.channel_session_id
            )
            if channel_session is None:
                continue
            matched = channel_session.canonical_target == raw_target
            if raw_target.startswith("#"):
                label, separator, channel_session_id = raw_target.rpartition(":")
                matched = (
                    bool(separator)
                    and len(label) > 1
                    and channel_session.target_kind is ChannelTargetKind.GROUP
                    and channel_session.id == channel_session_id
                )
            elif raw_target.startswith("dm:@") and len(raw_target) > 4:
                matched = (
                    channel_session.target_kind is ChannelTargetKind.DM
                    and channel_session.target_handle_key == raw_target[4:].casefold()
                )
            if matched:
                matches.append((session, channel_session))
        if len(matches) != 1:
            raise InboxTargetResolutionError(
                "inbox target does not resolve to exactly one owned session"
            )
        target, channel_session = matches[0]
        handle_is_unique = True
        if channel_session.target_handle_key is not None:
            handle_is_unique = (
                sum(
                    candidate.target_kind is ChannelTargetKind.DM
                    and candidate.target_handle_key == channel_session.target_handle_key
                    for candidate_session in self._scoped_threads()
                    if (
                        candidate := self._storage.channel_sessions.get(
                            candidate_session.channel_session_id
                        )
                    )
                    is not None
                )
                == 1
            )
        return ResolvedInboxTarget(
            thread=target,
            channel_session=channel_session,
            handle_is_unique=handle_is_unique,
        )

    async def find_thread(self, channel_session_id: str) -> Thread | None:
        matches = [
            session
            for session in self._storage.threads.values()
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

    async def get_consumer_cursor(self, thread_id: str) -> ConsumerCursor | None:
        return self._storage.cursors.get(thread_id)

    async def get_latest_message_seq(
        self,
        thread_id: str,
        *,
        direction: MessageDirection | None = None,
        delivery_states: frozenset[OutboundDeliveryState] | None = None,
    ) -> int:
        messages = self._filtered_messages(
            thread_id,
            direction=direction,
            delivery_states=delivery_states,
        )
        return messages[-1].seq if messages else 0

    async def get_latest_message(
        self,
        thread_id: str,
        *,
        direction: MessageDirection | None = None,
        delivery_states: frozenset[OutboundDeliveryState] | None = None,
    ) -> Message | None:
        messages = self._filtered_messages(
            thread_id,
            direction=direction,
            delivery_states=delivery_states,
        )
        return messages[-1] if messages else None

    async def count_messages(
        self,
        thread_id: str,
        *,
        after_seq: int | None = None,
        target: str | None = None,
        direction: MessageDirection | None = None,
        delivery_states: frozenset[OutboundDeliveryState] | None = None,
        notifying_only: bool = False,
    ) -> int:
        messages = self._filtered_messages(
            thread_id,
            direction=direction,
            delivery_states=delivery_states,
        )
        return sum(
            (after_seq is None or message.seq > after_seq)
            and (target is None or message.target == target)
            and (not notifying_only or message.notifies_runtime)
            for message in messages
        )

    def _in_scope(self, session: Thread) -> bool:
        return self._agent_id is None or session.workspace_id == self._agent_id

    def _scoped_threads(self) -> tuple[Thread, ...]:
        return tuple(
            session
            for session in self._storage.threads.values()
            if self._in_scope(session)
        )

    def _inbox_target_summary(self, session: Thread) -> InboxTargetSummary:
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
            thread_id=session.id,
            target_kind=channel_session.target_kind,
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
        for session in self._scoped_threads():
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
                key=lambda owner: (owner.agent_id, owner.owner_thread_id),
            )
        )

    async def list_messages(
        self,
        thread_id: str,
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
            thread_id,
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
        thread_id: str,
        *,
        direction: MessageDirection | None = None,
        delivery_states: frozenset[OutboundDeliveryState] | None = None,
    ) -> list[Message]:
        messages = list(self._storage.messages.get(thread_id, []))
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

    async def save_thread(self, session: Thread) -> None:
        self._require_workspace(session.workspace_id)
        if session.channel_session_id not in self._storage.channel_sessions:
            raise ValueError(f"unknown channel session: {session.channel_session_id}")
        existing = self._storage.threads.get(session.id)
        if existing is not None:
            if (
                existing.channel_session_id != session.channel_session_id
                or existing.workspace_id != session.workspace_id
                or existing.created_at_ms != session.created_at_ms
            ):
                raise ValueError("bcn session binding cannot change")
            session = _validate_thread_update(existing, session)
        else:
            duplicate = await self.find_thread(session.channel_session_id)
            if duplicate is not None:
                raise ValueError(f"channel session is already bound to {duplicate.id}")
        self._storage.threads[session.id] = session

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
        messages = self._storage.messages.setdefault(message.thread_id, [])
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
            if referenced.thread_id != message.thread_id:
                raise ValueError("reply_to_message_id must belong to the same session")
            if referenced.seq >= message.seq:
                raise ValueError(
                    "reply_to_message_id must reference an earlier message"
                )
        messages.append(message)
        return message

    async def save_consumer_cursor(self, cursor: ConsumerCursor) -> None:
        self._storage.cursors[cursor.thread_id] = cursor

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
        thread_id: str,
        message_id: str,
        *,
        direction: MessageDirection | None = None,
        delivery_states: frozenset[OutboundDeliveryState] | None = None,
    ) -> Message | None:
        return next(
            (
                message
                for message in self._filtered_messages(
                    thread_id,
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
        thread_id: str,
        message_id: str,
        *,
        direction: MessageDirection | None = None,
    ) -> Message | None:
        if self._agent_id is not None and self._agent_id != agent_id:
            return None
        session = self._storage.threads.get(thread_id)
        if session is None or session.workspace_id != agent_id:
            return None
        return await self.resolve_message(
            thread_id,
            message_id,
            direction=direction,
        )

    async def _save_outbound_message(self, message: Message) -> Message:
        _validate_outbound_message_input(message)
        thread = self._storage.threads.get(message.thread_id)
        if thread is None:
            raise ValueError(f"unknown thread: {message.thread_id}")
        if message.channel_session_id not in self._storage.channel_sessions:
            raise ValueError(f"unknown channel session: {message.channel_session_id}")
        if thread.channel_session_id != message.channel_session_id:
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
            self._storage.messages.setdefault(canonical.thread_id, []).append(canonical)
            return canonical
        if (
            existing.command_id != message.command_id
            or existing.thread_id != message.thread_id
            or existing.channel_session_id != message.channel_session_id
            or existing.target != message.target
            or existing.reply_to_message_id != message.reply_to_message_id
            or existing.body != message.body
            or existing.created_at_ms != message.created_at_ms
        ):
            raise ValueError("outbound message identity cannot change")
        canonical = _validate_outbound_update(existing, message)
        messages = self._storage.messages[canonical.thread_id]
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
        target_display_name=incoming.target_display_name,
        target_handle=incoming.target_handle,
        target_handle_key=incoming.target_handle_key,
        metadata=incoming.metadata,
    )


def _validate_thread_update(
    existing: Thread,
    incoming: Thread,
) -> Thread:
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
