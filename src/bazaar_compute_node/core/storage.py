from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Protocol

from .command import (
    InboxListResult,
    MessageCheckResult,
    MessageDraft,
    MessageReadResult,
    MessageSendFreshnessHold,
    MessageSendHandoffRequired,
    MessageSendResult,
    SessionNotFoundError,
)
from .handoff import HandoffCheckItem, HandoffCheckResult
from .inbox import InboxTargetPage
from .lifecycle import IAsyncLifecycle
from .models import (
    BcnSession,
    ChannelSession,
    ConsumerCursor,
    Handoff,
    InboundAttachment,
    Message,
    MessageDirection,
    OutboundAttachment,
    OutboundDeliveryState,
    OwnedReminder,
    OwnedReminderOccurrence,
    Reminder,
    ReminderOccurrence,
    ReminderOwner,
    ReminderState,
    RuntimeAttempt,
)
from .reminder import ReminderCheckItem, ReminderCheckResult


class InboxTargetResolutionError(ValueError):
    """A target does not resolve to exactly one Agent-owned BCN session."""


class HandoffConflictError(ValueError):
    """A handoff command ID is already associated with another payload."""


@dataclass(frozen=True, slots=True)
class RecordInboundResult:
    channel_session: ChannelSession
    bcn_session: BcnSession
    message: Message[InboundAttachment]
    channel_session_created: bool
    bcn_session_created: bool
    message_created: bool


@dataclass(frozen=True, slots=True)
class ReadMessageHistoryResult:
    source_session: BcnSession
    history: MessageReadResult


@dataclass(frozen=True, slots=True)
class PrepareOutboundResult:
    channel_session: ChannelSession
    target_session: BcnSession
    reply_to_provider_message_id: str | None
    outcome: MessageSendResult


@dataclass(frozen=True, slots=True)
class ReminderWakeResult:
    occurrence: ReminderOccurrence
    channel_session: ChannelSession
    bcn_session: BcnSession
    anchor_message: Message[InboundAttachment]


@dataclass(frozen=True, slots=True)
class HandoffWakeResult:
    channel_session: ChannelSession
    bcn_session: BcnSession
    anchor_message: Message[InboundAttachment]


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
        await self.save_consumer_cursor(
            replace(
                cursor,
                delivered_through_seq=latest_seq,
                inbox_snapshot_seq=latest_seq,
                inbox_snapshot_source="check",
                inbox_snapshot_at_ms=checked_at_ms,
                last_check_at_ms=checked_at_ms,
                updated_at_ms=checked_at_ms,
            )
        )
        return MessageCheckResult(
            messages=messages,
            snapshot_seq=latest_seq,
            delivered_through_seq=latest_seq,
            referenced_messages=references,
        )

    async def read_message_history(
        self,
        caller_session_id: str,
        *,
        target: str,
        around_message_id: str | None,
        limit: int,
    ) -> ReadMessageHistoryResult:
        self = _operations(self)  # noqa: PLW0642
        if await self.get_bcn_session(caller_session_id) is None:
            raise SessionNotFoundError(f"unknown bcn session: {caller_session_id}")
        source_session = await self.resolve_inbox_target(target)
        messages = await self.list_messages(
            source_session.id,
            target=target,
            around_message_id=around_message_id,
            delivery_states=_HISTORY_DELIVERY_STATES,
            limit=limit,
        )
        references = await _referenced_messages(self, source_session.id, messages)
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
        targets = tuple(
            replace(target, current=target.session_id == caller_session_id)
            for target in page.targets
        )
        return InboxListResult(
            targets=targets,
            total=page.total,
            shown=len(targets),
            offset=page.offset,
            has_more=page.has_more,
        )

    async def prepare_outbound(
        self,
        caller_session_id: str,
        *,
        command_id: str,
        payload: MessageDraft,
        attempted_at_ms: int,
        draft_replaced: bool,
    ) -> PrepareOutboundResult:
        self = _operations(self)  # noqa: PLW0642
        caller_session = await self.get_bcn_session(caller_session_id)
        if caller_session is None:
            raise SessionNotFoundError(f"unknown bcn session: {caller_session_id}")
        channel_session = await self.get_channel_session(
            caller_session.channel_session_id
        )
        if channel_session is None:
            raise ValueError(
                f"unknown channel session: {caller_session.channel_session_id}"
            )
        target_session = await self.resolve_inbox_target(payload.target)
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
            reply_messages = await self.list_messages(
                target_session.id,
                target=payload.target,
                around_message_id=payload.reply_to_message_id,
                direction=MessageDirection.INBOUND,
                limit=1,
            )
            reply_to_provider_message_id = reply_messages[0].provider_message_id

        if target_session.id != caller_session_id:
            outcome = MessageSendHandoffRequired(target=payload.target)
        else:
            cursor = await self.get_consumer_cursor(caller_session_id)
            if cursor is None:
                cursor = ConsumerCursor(session_id=caller_session_id)
            current_seq = await self.get_latest_message_seq(
                caller_session_id,
                direction=MessageDirection.INBOUND,
            )
            if (
                cursor.inbox_snapshot_seq is None
                or current_seq > cursor.inbox_snapshot_seq
            ):
                newer_total = await self.count_messages(
                    caller_session_id,
                    after_seq=cursor.inbox_snapshot_seq,
                    target=payload.target,
                    direction=MessageDirection.INBOUND,
                )
                newer_messages = await self.list_messages(
                    caller_session_id,
                    after_seq=cursor.inbox_snapshot_seq,
                    target=payload.target,
                    direction=MessageDirection.INBOUND,
                    latest=True,
                    limit=20,
                )
                outcome = MessageSendFreshnessHold(
                    target=payload.target,
                    messages=newer_messages,
                    referenced_messages=await _referenced_messages(
                        self,
                        caller_session_id,
                        newer_messages,
                    ),
                    newer_message_total=newer_total,
                    snapshot_seq=cursor.inbox_snapshot_seq,
                    current_inbound_seq=current_seq,
                    draft_replaced=draft_replaced,
                )
            else:
                outcome = await self.save_message(
                    Message(
                        direction=MessageDirection.OUTBOUND,
                        seq=0,
                        message_id=f"outbound-{caller_session_id}-{command_id}",
                        command_id=command_id,
                        session_id=caller_session_id,
                        channel_session_id=channel_session.id,
                        target=payload.target,
                        body=payload.body,
                        attachments=payload.attachments,
                        target_kind=channel_session.target_kind,
                        delivery_state=OutboundDeliveryState.PENDING,
                        created_at_ms=payload.created_at_ms,
                        snapshot_seq=cursor.inbox_snapshot_seq,
                        current_inbound_seq=current_seq,
                        provider_attempted_at_ms=attempted_at_ms,
                        reply_to_message_id=payload.reply_to_message_id,
                    )
                )
        return PrepareOutboundResult(
            channel_session=channel_session,
            target_session=target_session,
            reply_to_provider_message_id=reply_to_provider_message_id,
            outcome=outcome,
        )

    async def check_reminders(
        self,
        session_id: str,
        *,
        limit: int,
        read_at_ms: int,
    ) -> ReminderCheckResult:
        self = _operations(self)  # noqa: PLW0642
        if await self.get_bcn_session(session_id) is None:
            raise SessionNotFoundError(f"unknown bcn session: {session_id}")
        occurrences = await self.list_pending_reminder_occurrences(
            session_id,
            limit=limit,
        )
        if not occurrences:
            return ReminderCheckResult(items=(), has_more=False)
        snapshots = []
        for occurrence in occurrences:
            reminder = await self.get_reminder(session_id, occurrence.reminder_id)
            anchor = await self.resolve_message(
                session_id,
                occurrence.anchor_message_id,
                direction=MessageDirection.INBOUND,
            )
            if reminder is None or anchor is None:
                raise ValueError("Reminder check context is incomplete")
            snapshots.append((occurrence, reminder.title, anchor.target))
        marked = await self.mark_reminder_occurrences_read(
            session_id,
            tuple(occurrence.occurrence_id for occurrence in occurrences),
            read_at_ms=read_at_ms,
        )
        marked_by_id = {occurrence.occurrence_id: occurrence for occurrence in marked}
        return ReminderCheckResult(
            items=tuple(
                ReminderCheckItem(
                    occurrence=marked_by_id[occurrence.occurrence_id],
                    title=title,
                    canonical_target=target,
                )
                for occurrence, title, target in snapshots
            ),
            has_more=await self.count_pending_reminder_occurrences(session_id) > 0,
        )

    async def check_handoffs(
        self,
        session_id: str,
        *,
        limit: int,
        read_at_ms: int,
    ) -> HandoffCheckResult:
        self = _operations(self)  # noqa: PLW0642
        if await self.get_bcn_session(session_id) is None:
            raise SessionNotFoundError(f"unknown bcn session: {session_id}")
        handoffs = await self.list_pending_handoffs(session_id, limit=limit)
        if not handoffs:
            return HandoffCheckResult(items=(), has_more=False)
        source_targets = []
        for handoff in handoffs:
            source = (
                await self.get_latest_message(
                    handoff.source_session_id,
                    direction=MessageDirection.INBOUND,
                )
                if handoff.source_message_id is None
                else await self.resolve_message(
                    handoff.source_session_id,
                    handoff.source_message_id,
                    direction=MessageDirection.INBOUND,
                )
            )
            if source is None:
                raise ValueError(
                    f"Handoff source context is missing: {handoff.handoff_id}"
                )
            source_targets.append(source.target)
        marked = await self.mark_handoffs_read(
            session_id,
            tuple(handoff.handoff_id for handoff in handoffs),
            read_at_ms=read_at_ms,
        )
        marked_by_id = {handoff.handoff_id: handoff for handoff in marked}
        return HandoffCheckResult(
            items=tuple(
                HandoffCheckItem(
                    handoff=marked_by_id[handoff.handoff_id],
                    source_target=source_target,
                )
                for handoff, source_target in zip(
                    handoffs,
                    source_targets,
                    strict=True,
                )
            ),
            has_more=await self.count_pending_handoffs(session_id) > 0,
        )

    async def load_reminder_wake(
        self,
        session_id: str,
    ) -> ReminderWakeResult | None:
        self = _operations(self)  # noqa: PLW0642
        pending = await self.list_pending_reminder_occurrences(session_id, limit=1)
        if not pending:
            return None
        occurrence = pending[0]
        bcn_session = await self.get_bcn_session(session_id)
        if bcn_session is None:
            raise ValueError(f"unknown bcn session: {session_id}")
        channel_session = await self.get_channel_session(bcn_session.channel_session_id)
        anchor = await self.resolve_message(
            session_id,
            occurrence.anchor_message_id,
            direction=MessageDirection.INBOUND,
        )
        if channel_session is None or anchor is None:
            raise ValueError("Reminder wake context is incomplete")
        return ReminderWakeResult(
            occurrence=occurrence,
            channel_session=channel_session,
            bcn_session=bcn_session,
            anchor_message=anchor,
        )

    async def load_handoff_wake(self, session_id: str) -> HandoffWakeResult | None:
        self = _operations(self)  # noqa: PLW0642
        if await self.count_pending_handoffs(session_id) == 0:
            return None
        bcn_session = await self.get_bcn_session(session_id)
        if bcn_session is None:
            raise ValueError(f"unknown bcn session: {session_id}")
        channel_session = await self.get_channel_session(bcn_session.channel_session_id)
        anchor = await self.get_latest_message(
            session_id,
            direction=MessageDirection.INBOUND,
        )
        if channel_session is None or anchor is None:
            raise ValueError("Handoff wake context is incomplete")
        return HandoffWakeResult(
            channel_session=channel_session,
            bcn_session=bcn_session,
            anchor_message=anchor,
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
        target: str,
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

    async def prepare_outbound(
        self,
        caller_session_id: str,
        *,
        command_id: str,
        payload: MessageDraft,
        attempted_at_ms: int,
        draft_replaced: bool,
    ) -> PrepareOutboundResult: ...

    async def check_reminders(
        self,
        session_id: str,
        *,
        limit: int,
        read_at_ms: int,
    ) -> ReminderCheckResult: ...

    async def load_reminder_wake(
        self,
        session_id: str,
    ) -> ReminderWakeResult | None: ...

    async def load_handoff_wake(
        self,
        session_id: str,
    ) -> HandoffWakeResult | None: ...

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
    ) -> int: ...

    async def list_inbox_targets(
        self, *, limit: int = 100, offset: int = 0
    ) -> InboxTargetPage: ...

    async def resolve_inbox_target(self, target: str) -> BcnSession: ...

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

    async def save_fired_occurrence(
        self,
        expected_revision: int,
        reminder: Reminder,
        occurrence: ReminderOccurrence,
    ) -> ReminderOccurrence: ...

    async def list_pending_reminder_occurrences(
        self, owner_session_id: str, *, limit: int
    ) -> tuple[ReminderOccurrence, ...]: ...

    async def count_pending_reminder_occurrences(
        self, owner_session_id: str
    ) -> int: ...

    async def mark_reminder_occurrences_read(
        self,
        owner_session_id: str,
        occurrence_ids: tuple[str, ...],
        *,
        read_at_ms: int,
    ) -> tuple[ReminderOccurrence, ...]: ...

    async def list_sessions_with_pending_reminders(self) -> tuple[str, ...]: ...

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

    async def save_owned_fired_occurrence(
        self,
        expected_revision: int,
        reminder: OwnedReminder,
        occurrence: OwnedReminderOccurrence,
    ) -> OwnedReminderOccurrence: ...

    async def list_pending_reminder_owners(self) -> tuple[ReminderOwner, ...]: ...


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


class IHandoffStorageScope(IStorageScope, Protocol):
    """Agent storage scope with handoff operations."""

    async def check_handoffs(
        self,
        session_id: str,
        *,
        limit: int,
        read_at_ms: int,
    ) -> HandoffCheckResult: ...

    async def save_handoff(self, handoff: Handoff) -> Handoff: ...

    async def list_pending_handoffs(
        self, target_session_id: str, *, limit: int
    ) -> tuple[Handoff, ...]: ...

    async def count_pending_handoffs(self, target_session_id: str) -> int: ...

    async def mark_handoffs_read(
        self,
        target_session_id: str,
        handoff_ids: tuple[str, ...],
        *,
        read_at_ms: int,
    ) -> tuple[Handoff, ...]: ...
