from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, cast

from bazaar_contrib_messages.actor import Thread as ThreadActor

if TYPE_CHECKING:
    from .repository import Repository
from uuid import uuid7

from bazaar_contrib_messages.models import (
    ConsumerCursor,
    InboundAttachment,
    Message,
    OutboundAttachment,
)
from bazaar_contrib_messages.operations import (
    InboxTargetResolutionError,
    MaterializeOutboundResult,
    ReadMessageHistoryResult,
)
from bazaar_contrib_messages.results import (
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
from bazaar_contrib_messages.states import (
    MessageDirection,
    OutboundDeliveryState,
    Review,
)

from .queries import concurrent_reads

_HISTORY_DELIVERY_STATES = frozenset(
    {OutboundDeliveryState.QUEUED, OutboundDeliveryState.SENT}
)


class StorageOperationMixin:
    """Repository-level operations shared by durable and in-memory adapters."""

    async def check_messages(
        self,
        thread_ids: Sequence[ThreadActor],
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
        thread_id: ThreadActor | None,
        *,
        limit: int,
    ) -> UnreadSummary:
        """Say what is unread, counted and carried from the same read."""

        self = _operations(self)  # noqa: PLW0642
        if thread_id is None:
            return UnreadSummary(
                total=await self.count_unread_messages(),
                messages=cast(
                    tuple[Message[InboundAttachment], ...],
                    await self.list_unread_messages(limit=limit) if limit else (),
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
            messages=cast(
                tuple[Message[InboundAttachment], ...],
                (
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
            ),
        )

    async def read_message_history(
        self,
        *,
        raw_target: str,
        around_message_id: str | None,
        limit: int,
        review: Review | None = Review.APPROVED,
    ) -> ReadMessageHistoryResult:
        """A conversation's messages around one of them; a conversation not
        in the review state asked for is not there, unless any is asked."""

        self = _operations(self)  # noqa: PLW0642
        target = await self.resolve_inbox_target(raw_target)
        if review is not None and target.channel_session.review is not review:
            raise InboxTargetResolutionError(
                f"inbox target is not {review.value}: {raw_target}"
            )
        source_thread = target.thread
        messages = await self.list_messages(
            ThreadActor(source_thread.id),
            target=target.canonical_target,
            around_message_id=around_message_id,
            delivery_states=_HISTORY_DELIVERY_STATES,
            limit=limit,
        )
        oldest = await self.list_messages(
            ThreadActor(source_thread.id),
            target=target.canonical_target,
            delivery_states=_HISTORY_DELIVERY_STATES,
            limit=1,
        )
        references = await _referenced_messages(
            self, ThreadActor(source_thread.id), messages
        )
        canonical_targets = {message.target for message in (*messages, *references)}
        canonical_targets.update(
            source_target
            for message in (*messages, *references)
            if isinstance(
                source_target := message.metadata.get("system_message_source_target"),
                str,
            )
        )
        targets = await concurrent_reads(
            *(self.resolve_inbox_target(target) for target in sorted(canonical_targets))
        )
        target_projections = [
            TargetProjection(canonical_target, target.display_target)
            for canonical_target, target in zip(
                sorted(canonical_targets), targets, strict=True
            )
        ]
        latest_seq = await self.get_latest_message_seq(
            ThreadActor(source_thread.id),
            delivery_states=_HISTORY_DELIVERY_STATES,
        )
        return ReadMessageHistoryResult(
            source_thread=source_thread,
            history=MessageReadResult(
                messages=messages,
                snapshot_seq=latest_seq,
                first_seq=messages[0].seq if messages else None,
                last_seq=messages[-1].seq if messages else None,
                has_before=bool(messages) and messages[0].seq > oldest[0].seq,
                referenced_messages=references,
                target_projections=tuple(target_projections),
            ),
        )

    async def read_inbox_catalog(
        self,
        *,
        limit: int | None,
        offset: int = 0,
        review: Review | None = Review.APPROVED,
        thread_id: ThreadActor | None = None,
        pending_only: bool = False,
    ) -> InboxListResult:
        """The conversations as a listing names them: those the agent may
        talk in, unless another review state - or any - is asked for."""

        self = _operations(self)  # noqa: PLW0642
        page = await self.list_inbox_targets(
            limit=limit,
            offset=offset,
            review=review,
            thread_id=thread_id,
            pending_only=pending_only,
        )
        resolved = await concurrent_reads(
            *(self.resolve_inbox_target(summary.target) for summary in page.targets)
        )
        targets = [
            replace(
                summary, target=target.display_target, canonical_target=summary.target
            )
            for summary, target in zip(page.targets, resolved, strict=True)
        ]
        return InboxListResult(
            targets=tuple(targets),
            total=page.total,
            shown=len(targets),
            offset=page.offset,
            has_more=page.has_more,
            pending_review=page.pending_review,
        )

    async def check_outbound_freshness(
        self,
        target_id: ThreadActor,
        *,
        snapshot_seq: int | None,
        payload: MessageDraft,
        draft_replaced: bool,
    ) -> OutboundFreshnessPass | MessageSendFreshnessHold:
        self = _operations(self)  # noqa: PLW0642
        if payload.actor != target_id:
            raise ValueError("outbound draft target binding cannot change")
        if await self.get_thread(target_id) is None:
            raise ThreadNotFoundError(f"unknown thread: {target_id.id}")
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
        targets = await concurrent_reads(
            *(self.resolve_inbox_target(target) for target in sorted(canonical_targets))
        )
        target_projections = [
            TargetProjection(canonical_target, target.display_target)
            for canonical_target, target in zip(
                sorted(canonical_targets), targets, strict=True
            )
        ]
        return MessageSendFreshnessHold(
            target=payload.target,
            messages=cast(tuple[Message[InboundAttachment], ...], newer_messages),
            referenced_messages=references,
            newer_message_total=newer_total,
            snapshot_seq=snapshot_seq,
            current_inbound_seq=current_seq,
            draft_replaced=draft_replaced,
            target_projections=tuple(target_projections),
        )

    async def materialize_outbound_if_fresh(
        self,
        target_id: ThreadActor,
        expected_target_seq: int,
        *,
        payload: MessageDraft,
        attempted_at_ms: int,
    ) -> MaterializeOutboundResult:
        self = _operations(self)  # noqa: PLW0642
        if payload.actor != target_id:
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
                raise ThreadNotFoundError(f"unknown thread: {target_id.id}")
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
        if ThreadActor(target.thread.id) != target_id:
            raise ValueError("outbound target alias binding cannot change")
        target_thread = await self.get_thread(target_id)
        if target_thread is None:
            raise ThreadNotFoundError(f"unknown thread: {target_id.id}")
        channel_session = await self.get_channel_session(
            target_thread.channel_session_id
        )
        if channel_session is None:
            raise ValueError(
                f"unknown channel session: {target_thread.channel_session_id}"
            )
        # a reply the target cannot resolve is dropped on both sides: keeping the
        # local id would leave the outbound pointing at a message that reading
        # this target's history can never resolve
        reply_to_message_id = None
        reply_to_provider_message_id = None
        if payload.reply_to_message_id is not None:
            reply_message = await self.resolve_message(
                ThreadActor(target_thread.id),
                payload.reply_to_message_id,
                delivery_states=_HISTORY_DELIVERY_STATES,
            )
            if reply_message is not None and reply_message.target == payload.target:
                reply_to_message_id = payload.reply_to_message_id
                reply_to_provider_message_id = reply_message.provider_message_id
        outcome = Message(
            direction=MessageDirection.OUTBOUND,
            seq=0,
            message_id=str(uuid7()),
            thread_id=target_id.id,
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
    storage: Repository,
    thread_id: ThreadActor,
    *,
    checked_at_ms: int,
) -> MessageCheckResult:
    if await storage.get_thread(thread_id) is None:
        raise ThreadNotFoundError(f"unknown thread: {thread_id.id}")
    cursor = await storage.get_consumer_cursor(thread_id)
    if cursor is None:
        cursor = ConsumerCursor(thread_id=thread_id.id)
    limit = 100
    messages = await storage.list_messages(
        thread_id,
        after_seq=cursor.delivered_through_seq,
        direction=MessageDirection.INBOUND,
        notifying_only=True,
        limit=limit,
    )
    # A full page stops at its last message so the rest come in the next check.
    latest_seq = (
        messages[-1].seq
        if len(messages) == limit
        else await storage.get_latest_message_seq(
            thread_id, direction=MessageDirection.INBOUND
        )
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
    targets = await concurrent_reads(
        *(storage.resolve_inbox_target(target) for target in sorted(canonical_targets))
    )
    target_projections = [
        TargetProjection(canonical_target, target.display_target)
        for canonical_target, target in zip(
            sorted(canonical_targets), targets, strict=True
        )
    ]
    await storage.save_consumer_cursor(
        replace(
            cursor,
            delivered_through_seq=latest_seq,
            last_check_at_ms=checked_at_ms,
            updated_at_ms=checked_at_ms,
        )
    )
    return MessageCheckResult(
        messages=cast(tuple[Message[InboundAttachment], ...], messages),
        snapshot_seq=latest_seq,
        delivered_through_seq=latest_seq,
        referenced_messages=references,
        target_projections=tuple(target_projections),
    )


async def _referenced_messages(
    storage: Repository,
    thread_id: ThreadActor,
    messages: tuple[Message[InboundAttachment | OutboundAttachment], ...],
) -> tuple[Message[InboundAttachment | OutboundAttachment], ...]:
    message_ids = {message.message_id for message in messages}
    reference_ids = dict.fromkeys(
        message.reply_to_message_id
        for message in messages
        if message.reply_to_message_id is not None
        and message.reply_to_message_id not in message_ids
    )
    referenced = await concurrent_reads(
        *(
            storage.resolve_message(
                thread_id, id, delivery_states=_HISTORY_DELIVERY_STATES
            )
            for id in reference_ids
        )
    )
    if any(message is None for message in referenced):
        raise ValueError("reply reference does not resolve within the session")
    return cast(tuple[Message[InboundAttachment | OutboundAttachment], ...], referenced)


async def _latest_notifying_inbound_seq(
    storage: Repository, thread_id: ThreadActor
) -> int:
    messages = await storage.list_messages(
        thread_id,
        direction=MessageDirection.INBOUND,
        notifying_only=True,
        latest=True,
        limit=1,
    )
    return messages[-1].seq if messages else 0


def _operations(value: object) -> Repository:
    return cast("Repository", value)
