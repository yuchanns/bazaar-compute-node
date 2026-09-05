from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any, Protocol

from .command import (
    InboxListResult,
    MessageCheckResult,
    MessageDraft,
    MessageReadResult,
    MessageSendFreshnessHold,
    OutboundFreshnessPass,
    TargetProjection,
    ThreadNotFoundError,
    UnreadSummary,
)
from .inbox import InboxTargetPage
from .lifecycle import IAsyncLifecycle
from .models import (
    ChannelSession,
    ConsumerCursor,
    InboundAttachment,
    Message,
    MessageDirection,
    OutboundAttachment,
    OutboundDeliveryState,
    OwnedReminder,
    Reminder,
    ReminderState,
    RuntimeAttempt,
    Thread,
)


class InboxTargetResolutionError(ValueError):
    """A target does not resolve to exactly one Agent-owned BCN session."""


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
    agent_id: str
    trigger_message: Message[InboundAttachment]

    def __post_init__(self) -> None:
        if self.trigger_message.direction is not MessageDirection.INBOUND:
            raise ValueError("trigger_message must be inbound")
        if not self.trigger_message.notifies_runtime:
            raise ValueError("trigger_message must notify the runtime")

    @property
    def owner_thread_id(self) -> str:
        return self.trigger_message.thread_id


_HISTORY_DELIVERY_STATES = frozenset(
    {OutboundDeliveryState.QUEUED, OutboundDeliveryState.SENT}
)


class StorageOperationMixin:
    """Repository-level operations shared by durable and in-memory adapters."""

    async def check_messages(
        self,
        thread_ids: Sequence[str],
        *,
        checked_at_ms: int,
    ) -> tuple[MessageCheckResult, ...]:
        """Drain these conversations together, so none is read without arriving."""

        return tuple(
            [
                await _check_one_thread(
                    _operations(self), thread_id, checked_at_ms=checked_at_ms
                )
                for thread_id in thread_ids
            ]
        )

    async def read_unread_summary(
        self,
        thread_id: str | None,
        *,
        limit: int,
    ) -> UnreadSummary:
        """Say what is unread, counted and carried from the same read."""

        self = _operations(self)  # noqa: PLW0642
        if thread_id is None:
            return UnreadSummary(
                total=await self.count_unread_messages(),
                messages=(
                    await self.list_unread_messages(limit=limit) if limit else ()
                ),
            )
        cursor = await self.get_consumer_cursor(thread_id)
        after_seq = cursor.delivered_through_seq if cursor is not None else 0
        return UnreadSummary(
            total=await self.count_messages(
                thread_id,
                after_seq=after_seq,
                direction=MessageDirection.INBOUND,
                notifying_only=True,
            ),
            messages=(
                await self.list_messages(
                    thread_id,
                    after_seq=after_seq,
                    direction=MessageDirection.INBOUND,
                    notifying_only=True,
                    latest=True,
                    limit=limit,
                )
                if limit
                else ()
            ),
        )

    async def read_message_history(
        self,
        *,
        raw_target: str,
        around_message_id: str | None,
        limit: int,
    ) -> ReadMessageHistoryResult:
        self = _operations(self)  # noqa: PLW0642
        target = await self.resolve_inbox_target(raw_target)
        source_thread = target.thread
        messages = await self.list_messages(
            source_thread.id,
            target=target.canonical_target,
            around_message_id=around_message_id,
            delivery_states=_HISTORY_DELIVERY_STATES,
            limit=limit,
        )
        references = await _referenced_messages(self, source_thread.id, messages)
        canonical_targets = {message.target for message in (*messages, *references)}
        canonical_targets.update(
            source_target
            for message in (*messages, *references)
            if isinstance(
                source_target := message.metadata.get("system_message_source_target"),
                str,
            )
        )
        target_projections = []
        for canonical_target in sorted(canonical_targets):
            target = await self.resolve_inbox_target(canonical_target)
            target_projections.append(
                TargetProjection(canonical_target, target.display_target)
            )
        latest_seq = await self.get_latest_message_seq(
            source_thread.id,
            delivery_states=_HISTORY_DELIVERY_STATES,
        )
        return ReadMessageHistoryResult(
            source_thread=source_thread,
            history=MessageReadResult(
                messages=messages,
                snapshot_seq=latest_seq,
                first_seq=messages[0].seq if messages else None,
                last_seq=messages[-1].seq if messages else None,
                referenced_messages=references,
                target_projections=tuple(target_projections),
            ),
        )

    async def read_inbox_catalog(
        self,
        *,
        limit: int,
        offset: int = 0,
    ) -> InboxListResult:
        self = _operations(self)  # noqa: PLW0642
        page = await self.list_inbox_targets(limit=limit, offset=offset)
        targets = []
        for summary in page.targets:
            target = await self.resolve_inbox_target(summary.target)
            targets.append(replace(summary, target=target.display_target))
        return InboxListResult(
            targets=tuple(targets),
            total=page.total,
            shown=len(targets),
            offset=page.offset,
            has_more=page.has_more,
        )

    async def check_outbound_freshness(
        self,
        target_id: str,
        *,
        snapshot_seq: int | None,
        payload: MessageDraft,
        draft_replaced: bool,
    ) -> OutboundFreshnessPass | MessageSendFreshnessHold:
        self = _operations(self)  # noqa: PLW0642
        if payload.target_id != target_id:
            raise ValueError("outbound draft target binding cannot change")
        if await self.get_thread(target_id) is None:
            raise ThreadNotFoundError(f"unknown thread: {target_id}")
        current_seq = await _latest_notifying_inbound_seq(self, target_id)
        if current_seq == 0 or (
            snapshot_seq is not None and current_seq <= snapshot_seq
        ):
            return OutboundFreshnessPass(current_inbound_seq=current_seq)
        newer_total = await self.count_messages(
            target_id,
            after_seq=snapshot_seq,
            direction=MessageDirection.INBOUND,
            notifying_only=True,
        )
        newer_messages = await self.list_messages(
            target_id,
            after_seq=snapshot_seq,
            direction=MessageDirection.INBOUND,
            notifying_only=True,
            latest=True,
            limit=20,
        )
        references = await _referenced_messages(
            self,
            target_id,
            newer_messages,
        )
        canonical_targets = {
            message.target for message in (*newer_messages, *references)
        }
        canonical_targets.update(
            source_target
            for message in (*newer_messages, *references)
            if isinstance(
                source_target := message.metadata.get("system_message_source_target"),
                str,
            )
        )
        target_projections = []
        for canonical_target in sorted(canonical_targets):
            target = await self.resolve_inbox_target(canonical_target)
            target_projections.append(
                TargetProjection(canonical_target, target.display_target)
            )
        return MessageSendFreshnessHold(
            target=payload.target,
            messages=newer_messages,
            referenced_messages=references,
            newer_message_total=newer_total,
            snapshot_seq=snapshot_seq,
            current_inbound_seq=current_seq,
            draft_replaced=draft_replaced,
            target_projections=tuple(target_projections),
        )

    async def materialize_outbound_if_fresh(
        self,
        target_id: str,
        expected_target_seq: int,
        *,
        command_id: str,
        payload: MessageDraft,
        attempted_at_ms: int,
    ) -> MaterializeOutboundResult:
        self = _operations(self)  # noqa: PLW0642
        if payload.target_id != target_id:
            raise ValueError("outbound draft target binding cannot change")
        current_target_seq = await _latest_notifying_inbound_seq(self, target_id)
        if current_target_seq > expected_target_seq:
            outcome = await self.check_outbound_freshness(
                target_id,
                snapshot_seq=expected_target_seq,
                payload=payload,
                draft_replaced=False,
            )
            if isinstance(outcome, OutboundFreshnessPass):
                raise RuntimeError("target freshness recheck lost its stale boundary")
            held_thread = await self.get_thread(target_id)
            if held_thread is None:
                raise ThreadNotFoundError(f"unknown thread: {target_id}")
            held_channel = await self.get_channel_session(
                held_thread.channel_session_id
            )
            if held_channel is None:
                raise ValueError(
                    f"unknown channel session: {held_thread.channel_session_id}"
                )
            return MaterializeOutboundResult(
                channel_session=held_channel,
                target_thread=held_thread,
                reply_to_provider_message_id=None,
                outcome=outcome,
            )
        target = await self.resolve_inbox_target(payload.target)
        if target.thread.id != target_id:
            raise ValueError("outbound target alias binding cannot change")
        target_thread = await self.get_thread(target_id)
        if target_thread is None:
            raise ThreadNotFoundError(f"unknown thread: {target_id}")
        channel_session = await self.get_channel_session(
            target_thread.channel_session_id
        )
        if channel_session is None:
            raise ValueError(
                f"unknown channel session: {target_thread.channel_session_id}"
            )
        target_messages = await self.list_messages(
            target_thread.id,
            target=payload.target,
            direction=MessageDirection.INBOUND,
            limit=1,
        )
        if not target_messages:
            raise ValueError(f"thread target is not replyable: {payload.target}")
        # a reply the target cannot resolve is dropped on both sides: keeping the
        # local id would leave the outbound pointing at a message that reading
        # this target's history can never resolve
        reply_to_message_id = None
        reply_to_provider_message_id = None
        if payload.reply_to_message_id is not None:
            reply_message = await self.resolve_message(
                target_thread.id,
                payload.reply_to_message_id,
                delivery_states=_HISTORY_DELIVERY_STATES,
            )
            if reply_message is not None and reply_message.target == payload.target:
                reply_to_message_id = payload.reply_to_message_id
                reply_to_provider_message_id = reply_message.provider_message_id
        outcome = await self.save_message(
            Message(
                direction=MessageDirection.OUTBOUND,
                seq=0,
                message_id=f"outbound-{target_id}-{command_id}",
                command_id=command_id,
                thread_id=target_id,
                channel_session_id=channel_session.id,
                target=payload.target,
                body=payload.body,
                attachments=payload.attachments,
                target_kind=channel_session.target_kind,
                delivery_state=OutboundDeliveryState.PENDING,
                created_at_ms=payload.created_at_ms,
                provider_attempted_at_ms=attempted_at_ms,
                reply_to_message_id=reply_to_message_id,
            )
        )
        return MaterializeOutboundResult(
            channel_session=channel_session,
            target_thread=target_thread,
            reply_to_provider_message_id=reply_to_provider_message_id,
            outcome=outcome,
        )

    async def finalize_outbound_delivery(
        self,
        outbound: Message[OutboundAttachment],
    ) -> Message[OutboundAttachment]:
        self = _operations(self)  # noqa: PLW0642
        return await self.save_message(outbound)


async def _check_one_thread(
    storage: Any,
    thread_id: str,
    *,
    checked_at_ms: int,
) -> MessageCheckResult:
    if await storage.get_thread(thread_id) is None:
        raise ThreadNotFoundError(f"unknown thread: {thread_id}")
    cursor = await storage.get_consumer_cursor(thread_id)
    if cursor is None:
        cursor = ConsumerCursor(thread_id=thread_id)
    latest_seq = await storage.get_latest_message_seq(
        thread_id,
        direction=MessageDirection.INBOUND,
    )
    messages = await storage.list_messages(
        thread_id,
        after_seq=cursor.delivered_through_seq,
        direction=MessageDirection.INBOUND,
        notifying_only=True,
    )
    references = await _referenced_messages(storage, thread_id, messages)
    canonical_targets = {message.target for message in (*messages, *references)}
    canonical_targets.update(
        source_target
        for message in (*messages, *references)
        if isinstance(
            source_target := message.metadata.get("system_message_source_target"),
            str,
        )
    )
    target_projections: list[TargetProjection] = []
    for canonical_target in sorted(canonical_targets):
        target = await storage.resolve_inbox_target(canonical_target)
        target_projections.append(
            TargetProjection(canonical_target, target.display_target)
        )
    await storage.save_consumer_cursor(
        replace(
            cursor,
            delivered_through_seq=latest_seq,
            last_check_at_ms=checked_at_ms,
            updated_at_ms=checked_at_ms,
        )
    )
    return MessageCheckResult(
        messages=messages,
        snapshot_seq=latest_seq,
        delivered_through_seq=latest_seq,
        referenced_messages=references,
        target_projections=tuple(target_projections),
    )


async def _referenced_messages(
    storage: Any,
    thread_id: str,
    messages: tuple[Message[InboundAttachment | OutboundAttachment], ...],
) -> tuple[Message[InboundAttachment | OutboundAttachment], ...]:
    message_ids = {message.message_id for message in messages}
    referenced = []
    referenced_ids = set()
    for message in messages:
        reference_id = message.reply_to_message_id
        if (
            reference_id is None
            or reference_id in message_ids
            or reference_id in referenced_ids
        ):
            continue
        referenced_message = await storage.resolve_message(
            thread_id,
            reference_id,
            delivery_states=_HISTORY_DELIVERY_STATES,
        )
        if referenced_message is None:
            raise ValueError("reply reference does not resolve within the session")
        referenced.append(referenced_message)
        referenced_ids.add(reference_id)
    return tuple(referenced)


async def _latest_notifying_inbound_seq(storage: Any, thread_id: str) -> int:
    messages = await storage.list_messages(
        thread_id,
        direction=MessageDirection.INBOUND,
        notifying_only=True,
        latest=True,
        limit=1,
    )
    return messages[-1].seq if messages else 0


def _operations(value: object) -> Any:
    return value


class _StorageOperations(Protocol):
    """Storage operations exposed without implementation-specific transactions."""

    async def record_inbound(
        self,
        message: Message[InboundAttachment],
        *,
        now_ms: int,
    ) -> RecordInboundResult: ...

    async def check_messages(
        self,
        thread_ids: Sequence[str],
        *,
        checked_at_ms: int,
    ) -> tuple[MessageCheckResult, ...]: ...

    async def read_unread_summary(
        self,
        thread_id: str | None,
        *,
        limit: int,
    ) -> UnreadSummary: ...

    async def read_message_history(
        self,
        *,
        raw_target: str,
        around_message_id: str | None,
        limit: int,
    ) -> ReadMessageHistoryResult: ...

    async def read_inbox_catalog(
        self,
        *,
        limit: int,
        offset: int = 0,
    ) -> InboxListResult: ...

    async def check_outbound_freshness(
        self,
        target_id: str,
        *,
        snapshot_seq: int | None,
        payload: MessageDraft,
        draft_replaced: bool,
    ) -> OutboundFreshnessPass | MessageSendFreshnessHold: ...

    async def materialize_outbound_if_fresh(
        self,
        target_id: str,
        expected_target_seq: int,
        *,
        command_id: str,
        payload: MessageDraft,
        attempted_at_ms: int,
    ) -> MaterializeOutboundResult: ...

    async def finalize_outbound_delivery(
        self,
        outbound: Message[OutboundAttachment],
    ) -> Message[OutboundAttachment]: ...

    async def find_channel_session(
        self, *, channel: str, provider_thread_id: str
    ) -> ChannelSession | None: ...

    async def get_channel_session(
        self, channel_session_id: str
    ) -> ChannelSession | None: ...
    async def get_thread(self, thread_id: str) -> Thread | None: ...
    async def find_thread(self, channel_session_id: str) -> Thread | None: ...
    async def get_runtime_attempt(self, turn_id: str) -> RuntimeAttempt | None: ...
    async def get_consumer_cursor(self, thread_id: str) -> ConsumerCursor | None: ...
    async def get_latest_message_seq(
        self,
        thread_id: str,
        *,
        direction: MessageDirection | None = None,
        delivery_states: frozenset[OutboundDeliveryState] | None = None,
    ) -> int: ...

    async def get_latest_message(
        self,
        thread_id: str,
        *,
        direction: MessageDirection | None = None,
        delivery_states: frozenset[OutboundDeliveryState] | None = None,
    ) -> Message[InboundAttachment | OutboundAttachment] | None: ...

    async def count_messages(
        self,
        thread_id: str,
        *,
        after_seq: int | None = None,
        target: str | None = None,
        direction: MessageDirection | None = None,
        delivery_states: frozenset[OutboundDeliveryState] | None = None,
        notifying_only: bool = False,
    ) -> int: ...

    async def list_inbox_targets(
        self, *, limit: int = 100, offset: int = 0
    ) -> InboxTargetPage: ...

    async def list_unread_messages(
        self, *, limit: int
    ) -> tuple[Message[InboundAttachment | OutboundAttachment], ...]: ...

    async def count_unread_messages(self) -> int: ...

    async def resolve_inbox_target(self, raw_target: str) -> ResolvedInboxTarget: ...

    async def find_message(
        self,
        channel: str,
        provider_thread_id: str,
        provider_message_id: str,
        *,
        direction: MessageDirection | None = None,
    ) -> Message[InboundAttachment | OutboundAttachment] | None: ...

    async def resolve_message(
        self,
        thread_id: str,
        message_id: str,
        *,
        direction: MessageDirection | None = None,
        delivery_states: frozenset[OutboundDeliveryState] | None = None,
    ) -> Message[InboundAttachment | OutboundAttachment] | None: ...

    async def get_owned_message(
        self,
        agent_id: str,
        thread_id: str,
        message_id: str,
        *,
        direction: MessageDirection | None = None,
    ) -> Message[InboundAttachment | OutboundAttachment] | None: ...

    async def list_ready_attachment_paths(self) -> tuple[str, ...]: ...

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
    ) -> tuple[Message[InboundAttachment | OutboundAttachment], ...]: ...

    async def save_channel_session(self, session: ChannelSession) -> None: ...
    async def save_thread(self, session: Thread) -> None: ...
    async def save_runtime_attempt(self, attempt: RuntimeAttempt) -> None: ...
    async def save_consumer_cursor(self, cursor: ConsumerCursor) -> None: ...

    async def get_message(
        self,
        message_id: str,
        *,
        direction: MessageDirection | None = None,
    ) -> Message[InboundAttachment | OutboundAttachment] | None: ...

    async def save_message(self, message: Message) -> Message: ...

    async def get_reminder(
        self, owner_thread_id: str, reminder_id: str
    ) -> Reminder | None: ...

    async def list_reminders(
        self,
        owner_thread_id: str,
        statuses: frozenset[ReminderState],
    ) -> tuple[Reminder, ...]: ...

    async def save_new_reminder(self, reminder: Reminder) -> Reminder: ...

    async def save_reminder_transition(
        self, expected_revision: int, reminder: Reminder
    ) -> Reminder: ...

    async def get_next_scheduled_reminder(self) -> Reminder | None: ...

    async def list_due_reminders(
        self, now_ms: int, *, limit: int
    ) -> tuple[Reminder, ...]: ...

    async def get_owned_reminder(
        self,
        agent_id: str,
        owner_thread_id: str,
        reminder_id: str,
    ) -> OwnedReminder | None: ...

    async def get_next_scheduled_owned_reminder(self) -> OwnedReminder | None: ...

    async def list_due_owned_reminders(
        self,
        now_ms: int,
        *,
        limit: int,
    ) -> tuple[OwnedReminder, ...]: ...

    async def save_owned_reminder_transition(
        self,
        expected_revision: int,
        reminder: OwnedReminder,
    ) -> Reminder | None: ...

    async def materialize_owned_reminder_message(
        self,
        expected_revision: int,
        reminder: OwnedReminder,
        system_message: Message[InboundAttachment],
    ) -> Message[InboundAttachment] | None: ...

    async def list_unread_message_owners(self) -> tuple[UnreadMessageOwner, ...]: ...


class IStorage(IAsyncLifecycle, _StorageOperations, Protocol):
    """Node-owned durable storage lifecycle and Agent scope factory."""

    @property
    def name(self) -> str: ...

    def scope(self, agent_id: str, agent_name: str) -> IStorageScope: ...


class IStorageScope(IStorage, Protocol):
    """Immutable Agent view over shared storage.

    Scope lifecycle methods are intentionally no-op at the adapter boundary; the
    Node-owned storage remains the only object that starts or stops physical storage.
    """

    @property
    def agent_id(self) -> str: ...

    @property
    def agent_name(self) -> str: ...
