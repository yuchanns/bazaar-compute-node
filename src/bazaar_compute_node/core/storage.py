from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Protocol
from uuid import uuid7

from .command import (
    InboxListResult,
    MessageCheckResult,
    MessageDraft,
    MessageReadResult,
    MessageSendFreshnessHold,
    OutboundFreshnessPass,
    SessionNotFoundError,
    TargetProjection,
    render_handoff_message_body,
)
from .inbox import InboxTargetPage
from .lifecycle import IAsyncLifecycle
from .models import (
    BcnSession,
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
    SenderIdentity,
    SenderKind,
    SystemMessageKind,
)


class InboxTargetResolutionError(ValueError):
    """A target does not resolve to exactly one Agent-owned BCN session."""


@dataclass(frozen=True, slots=True)
class RecordInboundResult:
    channel_session: ChannelSession
    bcn_session: BcnSession
    message: Message[InboundAttachment]
    channel_session_created: bool
    bcn_session_created: bool
    message_created: bool


@dataclass(frozen=True, slots=True)
class ResolvedInboxTarget:
    bcn_session: BcnSession
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
    source_session: BcnSession
    history: MessageReadResult


@dataclass(frozen=True, slots=True)
class MaterializeOutboundResult:
    channel_session: ChannelSession
    target_session: BcnSession
    reply_to_provider_message_id: str | None
    outcome: Message[OutboundAttachment] | MessageSendFreshnessHold


@dataclass(frozen=True, slots=True)
class FinalizeOutboundResult:
    outbound: Message[OutboundAttachment]
    handoff_message: Message[InboundAttachment] | None


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
    def owner_session_id(self) -> str:
        return self.trigger_message.session_id


_HISTORY_DELIVERY_STATES = frozenset(
    {OutboundDeliveryState.QUEUED, OutboundDeliveryState.SENT}
)


class StorageOperationMixin:
    """Repository-level operations shared by durable and in-memory adapters."""

    async def check_messages(
        self,
        session_id: str,
        *,
        checked_at_ms: int,
    ) -> MessageCheckResult:
        self = _operations(self)  # noqa: PLW0642
        if await self.get_bcn_session(session_id) is None:
            raise SessionNotFoundError(f"unknown bcn session: {session_id}")
        cursor = await self.get_consumer_cursor(session_id)
        if cursor is None:
            cursor = ConsumerCursor(session_id=session_id)
        latest_seq = await self.get_latest_message_seq(
            session_id,
            direction=MessageDirection.INBOUND,
        )
        messages = await self.list_messages(
            session_id,
            after_seq=cursor.delivered_through_seq,
            direction=MessageDirection.INBOUND,
            notifying_only=True,
        )
        references = await _referenced_messages(self, session_id, messages)
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
            target = await self.resolve_inbox_target(canonical_target)
            target_projections.append(
                TargetProjection(canonical_target, target.display_target)
            )
        await self.save_consumer_cursor(
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

    async def read_message_history(
        self,
        caller_session_id: str,
        *,
        raw_target: str,
        around_message_id: str | None,
        limit: int,
    ) -> ReadMessageHistoryResult:
        self = _operations(self)  # noqa: PLW0642
        if await self.get_bcn_session(caller_session_id) is None:
            raise SessionNotFoundError(f"unknown bcn session: {caller_session_id}")
        target = await self.resolve_inbox_target(raw_target)
        source_session = target.bcn_session
        messages = await self.list_messages(
            source_session.id,
            target=target.canonical_target,
            around_message_id=around_message_id,
            delivery_states=_HISTORY_DELIVERY_STATES,
            limit=limit,
        )
        references = await _referenced_messages(self, source_session.id, messages)
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
            source_session.id,
            delivery_states=_HISTORY_DELIVERY_STATES,
        )
        return ReadMessageHistoryResult(
            source_session=source_session,
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
        caller_session_id: str,
        *,
        limit: int,
        offset: int,
    ) -> InboxListResult:
        self = _operations(self)  # noqa: PLW0642
        if await self.get_bcn_session(caller_session_id) is None:
            raise SessionNotFoundError(f"unknown bcn session: {caller_session_id}")
        page = await self.list_inbox_targets(limit=limit, offset=offset)
        targets = []
        for summary in page.targets:
            target = await self.resolve_inbox_target(summary.target)
            targets.append(
                replace(
                    summary,
                    target=target.display_target,
                    current=summary.session_id == caller_session_id,
                )
            )
        return InboxListResult(
            targets=tuple(targets),
            total=page.total,
            shown=len(targets),
            offset=page.offset,
            has_more=page.has_more,
        )

    async def check_outbound_freshness(
        self,
        source_target_id: str,
        *,
        source_snapshot_seq: int | None,
        payload: MessageDraft,
        draft_replaced: bool,
    ) -> OutboundFreshnessPass | MessageSendFreshnessHold:
        self = _operations(self)  # noqa: PLW0642
        if payload.source_target_id != source_target_id:
            raise ValueError("outbound draft source binding cannot change")
        if await self.get_bcn_session(source_target_id) is None:
            raise SessionNotFoundError(f"unknown bcn session: {source_target_id}")
        current_seq = await _latest_notifying_inbound_seq(self, source_target_id)
        if current_seq == 0 or (
            source_snapshot_seq is not None and current_seq <= source_snapshot_seq
        ):
            return OutboundFreshnessPass(current_inbound_seq=current_seq)
        newer_total = await self.count_messages(
            source_target_id,
            after_seq=source_snapshot_seq,
            direction=MessageDirection.INBOUND,
            notifying_only=True,
        )
        newer_messages = await self.list_messages(
            source_target_id,
            after_seq=source_snapshot_seq,
            direction=MessageDirection.INBOUND,
            notifying_only=True,
            latest=True,
            limit=20,
        )
        references = await _referenced_messages(
            self,
            source_target_id,
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
            snapshot_seq=source_snapshot_seq,
            current_inbound_seq=current_seq,
            draft_replaced=draft_replaced,
            target_projections=tuple(target_projections),
        )

    async def materialize_outbound_if_fresh(
        self,
        source_target_id: str,
        expected_source_seq: int,
        target_id: str,
        *,
        command_id: str,
        payload: MessageDraft,
        attempted_at_ms: int,
    ) -> MaterializeOutboundResult:
        self = _operations(self)  # noqa: PLW0642
        if (
            payload.source_target_id != source_target_id
            or payload.target_id != target_id
        ):
            raise ValueError("outbound draft target binding cannot change")
        current_source_seq = await _latest_notifying_inbound_seq(
            self,
            source_target_id,
        )
        if current_source_seq > expected_source_seq:
            outcome = await self.check_outbound_freshness(
                source_target_id,
                source_snapshot_seq=expected_source_seq,
                payload=payload,
                draft_replaced=False,
            )
            if isinstance(outcome, OutboundFreshnessPass):
                raise RuntimeError("source freshness recheck lost its stale boundary")
            source_session = await self.get_bcn_session(source_target_id)
            if source_session is None:
                raise SessionNotFoundError(f"unknown bcn session: {source_target_id}")
            source_channel = await self.get_channel_session(
                source_session.channel_session_id
            )
            if source_channel is None:
                raise ValueError(
                    f"unknown channel session: {source_session.channel_session_id}"
                )
            return MaterializeOutboundResult(
                channel_session=source_channel,
                target_session=source_session,
                reply_to_provider_message_id=None,
                outcome=outcome,
            )
        target = await self.resolve_inbox_target(payload.target)
        if target.bcn_session.id != target_id:
            raise ValueError("outbound target alias binding cannot change")
        target_session = await self.get_bcn_session(target_id)
        if target_session is None:
            raise SessionNotFoundError(f"unknown bcn session: {target_id}")
        channel_session = await self.get_channel_session(
            target_session.channel_session_id
        )
        if channel_session is None:
            raise ValueError(
                f"unknown channel session: {target_session.channel_session_id}"
            )
        target_messages = await self.list_messages(
            target_session.id,
            target=payload.target,
            direction=MessageDirection.INBOUND,
            limit=1,
        )
        if not target_messages:
            raise ValueError(f"thread target is not replyable: {payload.target}")
        reply_to_provider_message_id = None
        if payload.reply_to_message_id is not None:
            reply_message = await self.resolve_message(
                target_session.id,
                payload.reply_to_message_id,
                delivery_states=_HISTORY_DELIVERY_STATES,
            )
            if reply_message is not None and reply_message.target == payload.target:
                reply_to_provider_message_id = reply_message.provider_message_id
        cross_session = source_target_id != target_id
        metadata: dict[str, object] = {}
        if cross_session:
            metadata = {
                "source_target_id": source_target_id,
                "target_id": target_id,
                "source_message_id": payload.source_message_id,
                "handoff_message_id": str(uuid7()),
            }
        outcome = await self.save_message(
            Message(
                direction=MessageDirection.OUTBOUND,
                seq=0,
                message_id=f"outbound-{source_target_id}-{command_id}",
                command_id=command_id,
                session_id=target_id,
                channel_session_id=channel_session.id,
                target=payload.target,
                body=payload.body,
                attachments=payload.attachments,
                target_kind=channel_session.target_kind,
                delivery_state=OutboundDeliveryState.PENDING,
                created_at_ms=payload.created_at_ms,
                provider_attempted_at_ms=attempted_at_ms,
                reply_to_message_id=payload.reply_to_message_id,
                metadata=metadata,
            )
        )
        return MaterializeOutboundResult(
            channel_session=channel_session,
            target_session=target_session,
            reply_to_provider_message_id=reply_to_provider_message_id,
            outcome=outcome,
        )

    async def finalize_outbound_delivery(
        self,
        outbound: Message[OutboundAttachment],
    ) -> FinalizeOutboundResult:
        self = _operations(self)  # noqa: PLW0642
        outbound = await self.save_message(outbound)
        if outbound.delivery_state not in {
            OutboundDeliveryState.SENT,
            OutboundDeliveryState.QUEUED,
        }:
            return FinalizeOutboundResult(outbound=outbound, handoff_message=None)
        source_target_id = outbound.metadata.get("source_target_id")
        if not isinstance(source_target_id, str):
            return FinalizeOutboundResult(outbound=outbound, handoff_message=None)
        source_message_id = str(outbound.metadata["source_message_id"])
        handoff_message_id = str(outbound.metadata["handoff_message_id"])
        source_message = await self.resolve_message(
            source_target_id,
            source_message_id,
            direction=MessageDirection.INBOUND,
        )
        if source_message is None:
            raise ValueError("Handoff source context is missing")
        channel_session = await self.get_channel_session(outbound.channel_session_id)
        if channel_session is None:
            raise ValueError(f"unknown channel session: {outbound.channel_session_id}")
        delivered_at_ms = outbound.completed_at_ms or outbound.provider_attempted_at_ms
        if delivered_at_ms is None:
            raise RuntimeError("delivered outbound message has no delivery time")
        candidate = Message[InboundAttachment](
            direction=MessageDirection.INBOUND,
            seq=0,
            message_id=handoff_message_id,
            session_id=outbound.session_id,
            channel_session_id=outbound.channel_session_id,
            channel=channel_session.channel,
            provider_thread_id=channel_session.provider_thread_id,
            received_at_ms=delivered_at_ms,
            sender=SenderIdentity(id="system", name="system"),
            target=outbound.target,
            target_kind=channel_session.target_kind,
            body=render_handoff_message_body(
                source_message.target, outbound.message_id
            ),
            notifies_runtime=True,
            metadata={
                "sender_kind": SenderKind.SYSTEM.value,
                "system_message_kind": SystemMessageKind.HANDOFF.value,
                "system_message_source_target": source_message.target,
                "system_message_source_message_id": source_message.message_id,
                "system_message_outbound_message_id": outbound.message_id,
            },
        )
        existing = await self.get_message(handoff_message_id)
        if existing is not None:
            comparable = replace(existing, seq=0)
            if comparable != candidate:
                raise ValueError("Handoff system message identity cannot change")
            handoff_message = existing
        else:
            handoff_message = await self.save_message(candidate)
        return FinalizeOutboundResult(
            outbound=outbound,
            handoff_message=handoff_message,
        )


async def _referenced_messages(
    storage: Any,
    session_id: str,
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
            session_id,
            reference_id,
            delivery_states=_HISTORY_DELIVERY_STATES,
        )
        if referenced_message is None:
            raise ValueError("reply reference does not resolve within the session")
        referenced.append(referenced_message)
        referenced_ids.add(reference_id)
    return tuple(referenced)


async def _latest_notifying_inbound_seq(storage: Any, session_id: str) -> int:
    messages = await storage.list_messages(
        session_id,
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
        session_id: str,
        *,
        checked_at_ms: int,
    ) -> MessageCheckResult: ...

    async def read_message_history(
        self,
        caller_session_id: str,
        *,
        raw_target: str,
        around_message_id: str | None,
        limit: int,
    ) -> ReadMessageHistoryResult: ...

    async def read_inbox_catalog(
        self,
        caller_session_id: str,
        *,
        limit: int,
        offset: int,
    ) -> InboxListResult: ...

    async def check_outbound_freshness(
        self,
        source_target_id: str,
        *,
        source_snapshot_seq: int | None,
        payload: MessageDraft,
        draft_replaced: bool,
    ) -> OutboundFreshnessPass | MessageSendFreshnessHold: ...

    async def materialize_outbound_if_fresh(
        self,
        source_target_id: str,
        expected_source_seq: int,
        target_id: str,
        *,
        command_id: str,
        payload: MessageDraft,
        attempted_at_ms: int,
    ) -> MaterializeOutboundResult: ...

    async def finalize_outbound_delivery(
        self,
        outbound: Message[OutboundAttachment],
    ) -> FinalizeOutboundResult: ...

    async def find_channel_session(
        self, *, channel: str, provider_thread_id: str
    ) -> ChannelSession | None: ...

    async def get_channel_session(self, session_id: str) -> ChannelSession | None: ...
    async def get_bcn_session(self, session_id: str) -> BcnSession | None: ...
    async def find_bcn_session(self, channel_session_id: str) -> BcnSession | None: ...
    async def get_runtime_attempt(self, turn_id: str) -> RuntimeAttempt | None: ...
    async def get_consumer_cursor(self, session_id: str) -> ConsumerCursor | None: ...
    async def get_latest_message_seq(
        self,
        session_id: str,
        *,
        direction: MessageDirection | None = None,
        delivery_states: frozenset[OutboundDeliveryState] | None = None,
    ) -> int: ...

    async def get_latest_message(
        self,
        session_id: str,
        *,
        direction: MessageDirection | None = None,
        delivery_states: frozenset[OutboundDeliveryState] | None = None,
    ) -> Message[InboundAttachment | OutboundAttachment] | None: ...

    async def count_messages(
        self,
        session_id: str,
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
        session_id: str,
        message_id: str,
        *,
        direction: MessageDirection | None = None,
        delivery_states: frozenset[OutboundDeliveryState] | None = None,
    ) -> Message[InboundAttachment | OutboundAttachment] | None: ...

    async def get_owned_message(
        self,
        agent_id: str,
        session_id: str,
        message_id: str,
        *,
        direction: MessageDirection | None = None,
    ) -> Message[InboundAttachment | OutboundAttachment] | None: ...

    async def list_ready_attachment_paths(self) -> tuple[str, ...]: ...

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
    ) -> tuple[Message[InboundAttachment | OutboundAttachment], ...]: ...

    async def save_channel_session(self, session: ChannelSession) -> None: ...
    async def save_bcn_session(self, session: BcnSession) -> None: ...
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
        self, owner_session_id: str, reminder_id: str
    ) -> Reminder | None: ...

    async def list_reminders(
        self,
        owner_session_id: str,
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
        owner_session_id: str,
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
