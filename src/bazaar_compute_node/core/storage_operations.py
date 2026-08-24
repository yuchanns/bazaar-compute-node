from __future__ import annotations

from dataclasses import replace
from typing import Any

from .command import (
    InboxListResult,
    MessageCheckResult,
    MessageDraft,
    MessageReadResult,
    MessageSendFreshnessHold,
    MessageSendHandoffRequired,
    SessionNotFoundError,
)
from .handoff import HandoffCheckItem, HandoffCheckResult
from .models import (
    ConsumerCursor,
    InboundAttachment,
    Message,
    MessageDirection,
    OutboundDeliveryState,
)
from .reminder import ReminderCheckItem, ReminderCheckResult
from .storage import (
    HandoffWakeResult,
    PrepareOutboundResult,
    ReadMessageHistoryResult,
    ReminderWakeResult,
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
        latest_seq = await self.get_latest_inbound_seq(session_id)
        messages = await self.list_inbound_messages(
            session_id,
            after_seq=cursor.delivered_through_seq,
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
        messages = await self.list_inbound_messages(
            source_session.id,
            target=target,
            around_message_id=around_message_id,
            limit=limit,
        )
        references = await _referenced_messages(self, source_session.id, messages)
        latest_seq = await self.get_latest_inbound_seq(source_session.id)
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
        target_messages = await self.list_inbound_messages(
            target_session.id,
            target=payload.target,
            limit=1,
        )
        if not target_messages:
            raise ValueError(f"thread target is not replyable: {payload.target}")
        reply_to_provider_message_id = None
        if payload.reply_to_message_id is not None:
            reply_messages = await self.list_inbound_messages(
                target_session.id,
                target=payload.target,
                around_message_id=payload.reply_to_message_id,
                limit=1,
            )
            reply_to_provider_message_id = reply_messages[0].provider_message_id

        if target_session.id != caller_session_id:
            outcome = MessageSendHandoffRequired(target=payload.target)
        else:
            cursor = await self.get_consumer_cursor(caller_session_id)
            if cursor is None:
                cursor = ConsumerCursor(session_id=caller_session_id)
            current_seq = await self.get_latest_inbound_seq(caller_session_id)
            if (
                cursor.inbox_snapshot_seq is None
                or current_seq > cursor.inbox_snapshot_seq
            ):
                newer_total = await self.count_inbound_messages(
                    caller_session_id,
                    after_seq=cursor.inbox_snapshot_seq,
                    target=payload.target,
                )
                newer_messages = await self.list_inbound_messages(
                    caller_session_id,
                    after_seq=cursor.inbox_snapshot_seq,
                    target=payload.target,
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
                outcome = await self.save_outbound_message(
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
            anchor = await self.resolve_inbound_message(
                session_id,
                occurrence.anchor_message_id,
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
                await self.get_latest_inbound_message(handoff.source_session_id)
                if handoff.source_message_id is None
                else await self.resolve_inbound_message(
                    handoff.source_session_id,
                    handoff.source_message_id,
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
        anchor = await self.resolve_inbound_message(
            session_id,
            occurrence.anchor_message_id,
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
        anchor = await self.get_latest_inbound_message(session_id)
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
    messages: tuple[Message[InboundAttachment], ...],
) -> tuple[Message[InboundAttachment], ...]:
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
        history = await storage.list_inbound_messages(
            session_id,
            target=message.target,
            around_message_id=reference_id,
            limit=1,
        )
        referenced.append(history[0])
        referenced_ids.add(reference_id)
    return tuple(referenced)


def _operations(value: object) -> Any:
    return value


__all__ = ["StorageOperationMixin"]
