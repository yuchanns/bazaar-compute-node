from __future__ import annotations

import asyncio
import hashlib
import logging
import mimetypes
import os
import stat
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from dataclasses import dataclass, fields, replace
from pathlib import Path, PurePosixPath
from time import time_ns
from typing import cast
from uuid import UUID, uuid7

from bazaar_contrib_audit import Audit, AuditContext, AuditError
from bazaar_contrib_channel import (
    Channel,
    ChannelError,
    ChannelMessageDelivery,
    ChannelSendRequest,
    ChannelTarget,
    DmAddress,
    DmRequest,
)
from bazaar_contrib_channel import InboundAttachment as ChannelSource
from bazaar_contrib_channel import OutboundAttachment as ChannelAttachment
from bazaar_contrib_messages import ThreadConcurrency
from bazaar_contrib_messages.actor import Actor, Agent, Thread
from bazaar_contrib_messages.correlation import CorrelationContext
from bazaar_contrib_messages.delivery import from_receipt
from bazaar_contrib_messages.models import (
    ChannelSession,
    InboundAttachment,
    Message,
    OutboundAttachment,
)
from bazaar_contrib_messages.models import Thread as ConversationRow
from bazaar_contrib_messages.operations import (
    AmbiguousInboxTargetError,
    InboxTargetResolutionError,
    MaterializeOutboundResult,
    ResolvedInboxTarget,
)
from bazaar_contrib_messages.outcomes import OutboundDeliveryResult
from bazaar_contrib_messages.results import (
    InboxListResult,
    MessageBroadcast,
    MessageCheckResult,
    MessageDraft,
    MessageReadResult,
    MessageSearchRequest,
    MessageSearchResult,
    MessageSendFreshnessHold,
    MessageSendResult,
    MessageSendSuccess,
    ThreadUnfollowResult,
    UnreadSummary,
)
from bazaar_contrib_messages.states import (
    ChannelTargetKind,
    ErrorKind,
    MessageDirection,
    OutboundDeliveryState,
    Review,
    RuntimeEventState,
)
from bazaar_contrib_timer import Timer
from pydantic import JsonValue, TypeAdapter

from .delivery import OutboundDeliveryService
from .store import MessageStore


async def threads_in_reach(storage: MessageStore, actor: Actor) -> tuple[Thread, ...]:
    match actor:
        case Thread():
            return await storage.list_thread_ids(thread_id=actor)
        case Agent():
            return await storage.list_thread_ids()


class OutboundAttachmentResolver:
    """Resolve stable outbound descriptors without blocking the event loop."""

    def __init__(self, workspace: Callable[[], Path]) -> None:
        self._workspace = workspace

    def __call__(
        self, attachment_paths: tuple[str, ...]
    ) -> tuple[OutboundAttachment, ...]:
        attachments: list[OutboundAttachment] = []
        seen_paths: set[Path] = set()
        workspace = self._workspace().resolve(strict=True)
        for raw_path in attachment_paths:
            source = Path(raw_path)
            if not source.is_absolute():
                raise ValueError("attachment paths must be absolute")
            try:
                relative = source.relative_to(workspace)
            except ValueError as error:
                raise ValueError(
                    "attachment path must stay within the workspace"
                ) from error
            current = workspace
            for part in relative.parts:
                current /= part
                if current.is_symlink():
                    raise ValueError("attachment path cannot contain symbolic links")
            try:
                resolved = source.resolve(strict=True)
            except OSError as error:
                raise ValueError(
                    f"attachment path is not readable: {source}"
                ) from error
            try:
                relative = resolved.relative_to(workspace)
            except ValueError as error:
                raise ValueError(
                    "attachment path must stay within the workspace"
                ) from error
            if resolved in seen_paths:
                raise ValueError("attachment paths must not contain duplicates")
            seen_paths.add(resolved)
            digest = hashlib.sha256()
            try:
                descriptor = os.open(
                    resolved,
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                )
                with os.fdopen(descriptor, "rb") as attachment_file:
                    file_stat = os.fstat(attachment_file.fileno())
                    if not stat.S_ISREG(file_stat.st_mode):
                        raise ValueError("attachment path must identify a regular file")
                    while chunk := attachment_file.read(1024 * 1024):
                        digest.update(chunk)
            except OSError as error:
                raise ValueError(
                    f"attachment path is not readable: {source}"
                ) from error
            attachments.append(
                OutboundAttachment(
                    name=resolved.name,
                    relative_path=PurePosixPath(*relative.parts).as_posix(),
                    media_type=mimetypes.guess_type(resolved.name)[0],
                    size_bytes=file_stat.st_size,
                    sha256=digest.hexdigest(),
                )
            )
        return tuple(attachments)


_DELIVERED_STATES = frozenset(
    {
        OutboundDeliveryState.SENT,
        OutboundDeliveryState.QUEUED,
        OutboundDeliveryState.PARTIAL,
    }
)


def _answered(
    outbound: Message[OutboundAttachment],
    delivery_result: OutboundDeliveryResult,
    *,
    at_ms: int,
) -> Message[OutboundAttachment]:
    """Fold what the channel made of a message back into it."""

    outbound = outbound.transition_to(
        delivery_result.state,
        at_ms=at_ms,
        provider_message_id=delivery_result.provider_message_id,
        provider_receipt_ref=delivery_result.provider_receipt_ref,
        error_kind=delivery_result.error_kind,
        error_message=delivery_result.error_message,
    )
    if not delivery_result.receipt:
        return outbound
    return replace(
        outbound,
        metadata={
            **outbound.metadata,
            "delivery_receipt": dict(delivery_result.receipt),
        },
    )


def _named_as_called(
    hold: MessageSendFreshnessHold,
    raw_target: str,
    targets: Sequence[ResolvedInboxTarget],
) -> MessageSendFreshnessHold:
    """Name a hold on several conversations the way the caller named them.

    The draft behind it belongs to all of them, and only the caller's own
    target resolves to that same set again.
    """

    return hold if len(targets) == 1 else replace(hold, target=raw_target)


def _require_approved(target: ResolvedInboxTarget, raw_target: str) -> None:
    """Refuse a conversation the agent may not talk in: to the agent, one
    still pending or turned away is not there."""

    if target.channel_session.review is not Review.APPROVED:
        raise InboxTargetResolutionError(
            f"inbox target does not resolve to an owned session: {raw_target}"
        )


def _reached_the_peer(delivery_result: OutboundDeliveryResult) -> bool:
    """Say whether any of this message is with the peer.

    A message that never left is not part of the conversation, and writing it
    down would put words in a history that never carried them. An attempt whose
    outcome is unknown still counts when the provider named a part it took.
    """

    return (
        delivery_result.state in _DELIVERED_STATES
        or delivery_result.provider_message_id is not None
        or delivery_result.provider_thread_id is not None
    )


_DELIVERY_OUTCOMES: dict[
    OutboundDeliveryState, tuple[ErrorKind | None, RuntimeEventState]
] = {
    OutboundDeliveryState.SENT: (None, RuntimeEventState.COMPLETED),
    OutboundDeliveryState.QUEUED: (None, RuntimeEventState.STARTED),
    OutboundDeliveryState.PARTIAL: (
        ErrorKind.PROVIDER_PARTIAL,
        RuntimeEventState.FAILED,
    ),
    OutboundDeliveryState.FAILED: (ErrorKind.PROVIDER_FAILED, RuntimeEventState.FAILED),
}


@dataclass(frozen=True, slots=True)
class _PreparedDelivery:
    """A conversation that passed its last freshness look, ready to send to."""

    target: ResolvedInboxTarget
    expected_target_seq: int
    outbound: Message[OutboundAttachment]
    materialized: MaterializeOutboundResult


@dataclass(frozen=True, slots=True)
class _DmOpening:
    """Where a DM that has never been held would be sent, and what to call it."""

    address: DmAddress
    channel: str
    handle: str


class NodeMessageService:
    """The commands about messages: what is unread, what was said, saying
    something, and leaving a group's notifications."""

    def __init__(
        self,
        *,
        storage: MessageStore,
        audit: Audit,
        concurrency: ThreadConcurrency,
        channel: Channel,
        workspace: Path,
        provider_timeout: float,
        timer: Timer,
        clock: Callable[[], int] = lambda: time_ns() // 1_000_000,
    ):
        self._owner = storage.owner
        self.storage = storage
        self._audit = audit
        self._concurrency = concurrency
        self._clock = clock
        self._channel = channel
        self._workspace = workspace
        self._delivery = OutboundDeliveryService(
            channel, timer, timeout=provider_timeout
        )
        self._attachment_resolver = OutboundAttachmentResolver(lambda: workspace)
        from .attachments import AttachmentMaterializer

        self._attachments = AttachmentMaterializer(
            lambda: workspace, storage.list_ready_attachment_paths
        )
        # one draft per conversation as the caller names it, which may be a
        # peer reached on several bots: the key is the set of threads it spans
        self._drafts: dict[tuple[Thread, ...], MessageDraft] = {}
        self._freshness_snapshots: dict[Thread, int] = {}
        self._logger = logging.getLogger(__name__)

    def _require_in_reach(self, actor: Actor, target: Thread, raw_target: str) -> None:
        match actor:
            case Agent():
                return
            case Thread() if actor == target:
                return
            case Thread():
                raise InboxTargetResolutionError(
                    f"inbox target is not this conversation: {raw_target}"
                )

    def _correlation(
        self,
        *,
        thread_id: Thread | None = None,
        actor: Actor | None = None,
        channel: str | None = None,
        channel_session_id: str | None = None,
        inbound_seq: int | None = None,
        outbound_message_id: str | None = None,
    ) -> CorrelationContext:
        return CorrelationContext(
            node_id=self._owner.id,
            channel=channel,
            channel_session_id=channel_session_id,
            thread_id=thread_id.id if thread_id is not None else None,
            actor=actor,
            inbound_seq=inbound_seq,
            outbound_message_id=outbound_message_id,
        )

    async def _publish(
        self,
        *,
        event_name: str,
        state: RuntimeEventState,
        correlation: CorrelationContext,
        metadata: Mapping[str, object] | None = None,
        error_kind: ErrorKind | None = None,
        error_message: str | None = None,
    ) -> None:
        # Convert identities only at the shared audit boundary.
        at_ms = self._clock()
        payload: dict[str, object] = {
            "event_name": event_name,
            "state": state.value,
            "created_at_ms": at_ms,
            "correlation": {
                f"{item.name}_id" if isinstance(value, Actor) else item.name: value.id
                if isinstance(value, Actor)
                else value
                for item in fields(correlation)
                if (value := getattr(correlation, item.name)) is not None
            },
            "metadata": dict(metadata or {}),
        }
        if error_kind is None and error_message is not None:
            payload["error_message"] = error_message
        await self._audit.publish(
            event_name,
            context=AuditContext(
                agent_id=self._owner.id,
                channel=correlation.channel,
                thread_id=correlation.thread_id,
                message_id=correlation.outbound_message_id,
                session_id=correlation.runtime_session_id,
                turn_id=correlation.turn_id,
            ),
            payload=TypeAdapter(JsonValue).validate_python(payload),
            occurred_at_ms=at_ms,
            error=None
            if error_kind is None
            else AuditError(kind=error_kind.value, message=error_message),
        )

    def prepare_attachment(self, source: ChannelSource) -> InboundAttachment:
        return InboundAttachment(
            attachment_id=str(uuid7()),
            name=Path(source.name).name or "attachment.bin",
            kind=source.kind,
            state="downloading" if source.state == "ready" else "failed",
            media_type=source.media_type,
            error=source.error,
        )

    async def import_attachment(
        self, source: ChannelSource, *, attachment_id: str | None = None
    ) -> InboundAttachment:
        if attachment_id is None:
            attachment_id = str(uuid7())
        else:
            attachment_id = str(TypeAdapter(UUID).validate_python(attachment_id))
        if source.state == "failed":
            return self._attachments.failed(
                attachment_id=attachment_id,
                name=source.name,
                kind=source.kind,
                media_type=source.media_type,
                error=source.error,
            )
        try:
            async with cast(
                AbstractAsyncContextManager[AsyncIterator[bytes]], source.source
            ) as chunks:
                return await self._attachments.materialize(
                    chunks,
                    attachment_id=attachment_id,
                    name=source.name,
                    kind=source.kind,
                    media_type=source.media_type,
                )
        except (ChannelError, OSError, ValueError) as error:
            return self._attachments.failed(
                attachment_id=attachment_id,
                name=source.name,
                kind=source.kind,
                media_type=source.media_type,
                error=str(error),
            )

    async def format_message(self, message: Message[InboundAttachment]) -> str:
        from bazaar_contrib_messages.results import TargetProjection

        from .dispatch import format_check_message
        from .serialize import serialize_message

        target = await self.storage.resolve_inbox_target(message.target)
        serialized = serialize_message(
            message, (TargetProjection(target.canonical_target, target.display_target),)
        )
        return await asyncio.to_thread(format_check_message, serialized)

    async def read_unread_summary(self, actor: Actor, *, limit: int) -> UnreadSummary:
        return await self.storage.read_unread_summary(
            actor if isinstance(actor, Thread) else None, limit=limit
        )

    async def check_inbox(
        self,
        actor: Actor,
        *,
        limit: int | None = None,
        offset: int = 0,
        pending_only: bool = True,
        review: Review | None = Review.APPROVED,
    ) -> InboxListResult:
        result = await self.storage.read_inbox_catalog(
            limit=limit,
            offset=offset,
            review=review,
            thread_id=actor if isinstance(actor, Thread) else None,
            pending_only=pending_only,
        )
        await self._publish(
            state=RuntimeEventState.COMPLETED,
            correlation=self._correlation(actor=actor),
            event_name="tool." + "bcc.inbox.check" + "." + "completed",
            metadata={
                "kind": "tool_call",
                "operation": "bcc.inbox.check",
                "status": "completed",
                "arguments": {"actor_id": actor.id},
            },
        )
        return result

    async def check_messages(self, actor: Actor) -> tuple[MessageCheckResult, ...]:
        thread_ids = await threads_in_reach(self.storage, actor)
        drained = await self.storage.check_messages(
            thread_ids,
            checked_at_ms=self._clock(),
        )
        for thread_id, result in zip(thread_ids, drained, strict=True):
            self._observe_freshness(thread_id, result.snapshot_seq)
        # one check is one record, however many conversations it looked in
        await self._publish(
            state=RuntimeEventState.COMPLETED,
            correlation=self._correlation(actor=actor),
            event_name="tool." + "bcc.message.check" + "." + "completed",
            metadata={
                "kind": "tool_call",
                "operation": "bcc.message.check",
                "status": "completed",
                "arguments": {
                    "actor_id": actor.id,
                    "thread_ids": [actor.id for actor in thread_ids],
                },
            },
        )
        return drained

    async def read_messages(
        self,
        actor: Actor,
        *,
        raw_target: str,
        around_message_id: str | None = None,
        limit: int = 100,
        review: Review | None = Review.APPROVED,
    ) -> MessageReadResult:
        snapshot = await self.storage.read_message_history(
            raw_target=raw_target,
            around_message_id=around_message_id,
            limit=limit,
            review=review,
        )
        self._require_in_reach(actor, Thread(snapshot.source_thread.id), raw_target)
        result = snapshot.history
        self._observe_freshness(Thread(snapshot.source_thread.id), result.snapshot_seq)
        await self._publish(
            state=RuntimeEventState.COMPLETED,
            correlation=self._correlation(thread_id=Thread(snapshot.source_thread.id)),
            event_name="tool." + "bcc.message.read" + "." + "completed",
            metadata={
                "kind": "tool_call",
                "operation": "bcc.message.read",
                "status": "completed",
                "arguments": {
                    "actor_id": actor.id,
                    "source_thread_id": snapshot.source_thread.id,
                    "target": raw_target,
                    "around_message_id": around_message_id,
                    "limit": limit,
                },
            },
        )
        return result

    async def search_messages(
        self,
        actor: Actor,
        request: MessageSearchRequest,
        *,
        review: Review | None = Review.APPROVED,
        query_budget: float | None = None,
    ) -> MessageSearchResult:
        thread_id = actor if isinstance(actor, Thread) else None
        request = replace(request, query=request.query.strip())
        result = await self.storage.search_messages(
            request, actor=thread_id, review=review, query_budget=query_budget
        )
        await self._publish(
            state=RuntimeEventState.COMPLETED,
            correlation=self._correlation(actor=actor, thread_id=thread_id),
            event_name="tool." + "bcc.message.search" + "." + "completed",
            metadata={
                "kind": "tool_call",
                "operation": "bcc.message.search",
                "status": "completed",
                "arguments": {
                    "actor_id": actor.id,
                    "thread_id": thread_id.id if thread_id is not None else None,
                    "query": request.query,
                    "target": request.raw_target,
                    "sender": request.sender,
                    "after_ms": request.after_ms,
                    "before_ms": request.before_ms,
                    "sort": result.sort,
                    "limit": request.limit,
                    "offset": request.offset,
                    "review": review.value if review is not None else None,
                    "shown": result.shown,
                },
            },
        )
        return result

    async def _dm_opening(self, actor: Actor, raw_target: str) -> _DmOpening | None:
        """Find where a DM this node has never held would be sent.

        Only `dm:@` opens, and only towards a sender this Agent has already
        heard from: the address comes from that past message, never from a
        directory lookup. A conversation-scoped actor never reaches a
        conversation it did not already own, so it is refused before anything
        is sent. `None` means the target stays unresolvable.
        """

        if (
            not isinstance(actor, Agent)
            or not raw_target.startswith("dm:@")
            or len(raw_target) == 4
        ):
            return None
        known = await self.storage.find_known_sender(raw_target[4:])
        if known is None:
            return None
        address = self._channel.dm_address(
            DmRequest(
                sender_id=known.sender.id,
                sender_name=known.sender.name,
                sender_kind=known.sender_kind.value,
                channel=known.channel,
                channel_identity=known.channel_identity,
            ),
        )
        if address is None:
            return None
        # The name the provider calls the peer comes first, then the token that
        # found it.
        handle = known.sender.name or raw_target[4:]
        return _DmOpening(address=address, channel=known.channel, handle=handle)

    async def _open_dm(
        self,
        *,
        raw_target: str,
        opening: _DmOpening,
        body: str,
        attachments: tuple[OutboundAttachment, ...],
        created_at_ms: int,
    ) -> MessageSendSuccess:
        """Open a conversation when its first visible message reaches the peer."""

        address = opening.address
        attempted_at_ms = self._clock()
        now = self._clock()
        session = ChannelSession(
            id=address.channel_session_id,
            channel=opening.channel,
            channel_identity=address.channel_identity,
            provider_thread_id=address.provider_thread_id,
            created_at_ms=now,
            updated_at_ms=now,
            target_kind=ChannelTargetKind.DM,
            target_handle=opening.handle,
            target_handle_key=opening.handle.casefold(),
            # a conversation the agent opened itself, with someone it has
            # already been talking to, needs no one's leave
            review=Review.APPROVED,
        )
        outbound = Message[OutboundAttachment](
            direction=MessageDirection.OUTBOUND,
            seq=0,
            message_id=str(uuid7()),
            thread_id=address.thread_id,
            channel_session_id=session.id,
            target=session.canonical_target,
            body=body,
            attachments=attachments,
            target_kind=ChannelTargetKind.DM,
            delivery_state=OutboundDeliveryState.PENDING,
            created_at_ms=created_at_ms,
            provider_attempted_at_ms=attempted_at_ms,
        )
        outbound, delivery_result = await self._transmit(
            outbound,
            session,
            delivery_handle=address.delivery_handle,
            opening=True,
        )
        delivery_state = outbound.delivery_state
        if delivery_state is None:
            raise RuntimeError("outbound message has no delivery state")
        audit_context = self._correlation(
            thread_id=Thread(address.thread_id),
            channel=session.channel,
            channel_session_id=session.id,
            outbound_message_id=outbound.message_id,
        )
        # a chat that was never opened is not a conversation, and writing one
        # down would leave `dm:@name` resolving to something that cannot be
        # spoken to; what became of the attempt is still worth recording
        if not _reached_the_peer(delivery_result):
            await self._record_delivery(
                audit_context,
                outbound,
                delivery_result,
                canonical_target=raw_target,
            )
            return MessageSendSuccess(message=outbound, target=raw_target)
        await self._record_delivery(
            audit_context,
            outbound,
            delivery_result,
            canonical_target=session.canonical_target,
        )
        resolved = await self.storage.resolve_inbox_target(session.canonical_target)
        return MessageSendSuccess(message=outbound, target=resolved.display_target)

    async def _stage_draft(
        self,
        *,
        target: ResolvedInboxTarget,
        payload: MessageDraft,
        draft_replaced: bool,
    ) -> tuple[MessageDraft, int] | MessageSendFreshnessHold:
        """Settle that nothing arrived in this conversation while the draft was written."""

        target_id = Thread(target.thread.id)
        async with self._concurrency.for_thread(target_id):
            freshness = await self.storage.check_outbound_freshness(
                target_id,
                snapshot_seq=self._freshness_snapshots.get(target_id),
                payload=payload,
                draft_replaced=draft_replaced,
            )
            if isinstance(freshness, MessageSendFreshnessHold):
                return await self._hold(
                    freshness,
                    target=target,
                )
            return payload, freshness.current_inbound_seq

    async def _hold(
        self,
        hold: MessageSendFreshnessHold,
        *,
        target: ResolvedInboxTarget,
    ) -> MessageSendFreshnessHold:
        """Record that something arrived while the message was being written."""

        target_id = Thread(target.thread.id)
        self._observe_freshness(target_id, hold.current_inbound_seq)
        await self._audit_freshness_hold(
            thread_id=target_id,
            target=target.canonical_target,
            result=hold,
        )
        return replace(hold, target=target.display_target)

    async def _transmit(
        self,
        outbound: Message[OutboundAttachment],
        channel_session: ChannelSession,
        *,
        reply_to_provider_message_id: str | None = None,
        delivery_handle: str | None = None,
        opening: bool = False,
    ) -> tuple[Message[OutboundAttachment], OutboundDeliveryResult]:
        """Persist each visible part before the channel sends the next one."""

        messages: list[Message[OutboundAttachment]] = []
        attachments = {
            self._workspace / item.relative_path: item for item in outbound.attachments
        }

        async def record(delivery: ChannelMessageDelivery) -> None:
            nonlocal channel_session
            result = from_receipt(delivery.receipt)
            if not _reached_the_peer(result):
                return
            now = self._clock()
            if opening and not messages:
                channel_session = replace(
                    channel_session,
                    provider_thread_id=(
                        result.provider_thread_id or channel_session.provider_thread_id
                    ),
                    created_at_ms=now,
                    updated_at_ms=now,
                )
                await self.storage.save_channel_session(channel_session)
                await self.storage.save_thread(
                    ConversationRow(
                        id=outbound.thread_id,
                        channel_session_id=channel_session.id,
                        workspace_id=self._owner.id,
                        created_at_ms=now,
                        updated_at_ms=now,
                    )
                )
            message = replace(
                outbound,
                message_id=outbound.message_id if not messages else str(uuid7()),
                body=delivery.message.body,
                attachments=tuple(
                    replace(
                        attachments[item.path],
                        name=item.name,
                        media_type=item.media_type,
                    )
                    for item in delivery.message.attachments
                ),
                metadata={
                    **outbound.metadata,
                    "outbound_send_id": outbound.message_id,
                    "outbound_part": {"index": delivery.index, "id": delivery.id},
                },
            )
            message = await self.storage.finalize_outbound_delivery(
                _answered(message, result, at_ms=now)
            )
            messages.append(message)

        delivery_result = await self._delivery.deliver(
            ChannelSendRequest(
                actor_id=outbound.thread_id,
                target=ChannelTarget(
                    kind=channel_session.target_kind.value,
                    thread_id=channel_session.provider_thread_id,
                    reply_to_message_id=reply_to_provider_message_id,
                ),
                body=outbound.body,
                attachments=tuple(
                    ChannelAttachment(
                        name=item.name,
                        path=self._workspace / item.relative_path,
                        media_type=item.media_type,
                        size_bytes=item.size_bytes,
                        sha256=item.sha256,
                    )
                    for item in outbound.attachments
                ),
                delivery_handle=delivery_handle,
                channel=channel_session.channel,
                channel_identity=channel_session.channel_identity,
            ),
            on_message=record,
        )
        outbound = _answered(outbound, delivery_result, at_ms=self._clock())
        if messages:
            first = messages[0]
            first = await self.storage.finalize_outbound_delivery(
                replace(
                    first,
                    metadata={
                        **first.metadata,
                        "outbound_delivery": {
                            "state": delivery_result.state.value,
                            "error_kind": delivery_result.error_kind,
                            "error_message": delivery_result.error_message,
                            "receipt": dict(delivery_result.receipt),
                        },
                    },
                )
            )
            outbound = replace(
                outbound,
                seq=first.seq,
                provider_message_id=first.provider_message_id,
                provider_receipt_ref=first.provider_receipt_ref,
                provider_thread_id=first.provider_thread_id,
            )
        return outbound, delivery_result

    async def _record_delivery(
        self,
        audit_context: CorrelationContext,
        outbound: Message[OutboundAttachment],
        delivery_result: OutboundDeliveryResult,
        *,
        canonical_target: str,
    ) -> None:
        """Write down what the channel did with the message, twice over."""

        delivery_state = outbound.delivery_state
        if delivery_state is None:
            raise RuntimeError("outbound message has no delivery state")
        error_message = outbound.error_message
        receipt = delivery_result.receipt
        terminal_kind, terminal_state = _DELIVERY_OUTCOMES.get(
            delivery_result.state,
            (ErrorKind.PROVIDER_UNKNOWN, RuntimeEventState.UNKNOWN),
        )
        await self._publish(
            event_name=f"channel.outbound.{delivery_state.value}",
            state=terminal_state,
            correlation=audit_context,
            error_kind=terminal_kind,
            error_message=error_message,
            metadata={
                **receipt,
                "text": outbound.body,
                "attachments": [
                    {
                        "name": attachment.name,
                        "media_type": attachment.media_type,
                        "size_bytes": attachment.size_bytes,
                    }
                    for attachment in outbound.attachments
                ],
            },
        )
        await self._publish(
            state=terminal_state,
            correlation=audit_context,
            error_kind=terminal_kind,
            error_message=error_message,
            event_name="tool." + "bcc.message.send" + "." + delivery_state.value,
            metadata={
                "kind": "tool_call",
                "operation": "bcc.message.send",
                "status": delivery_state.value,
                "arguments": {
                    "target": canonical_target,
                    "delivery_state": delivery_state.value,
                },
            },
        )

    async def _deliver(
        self,
        staged: Sequence[tuple[ResolvedInboxTarget, MessageDraft, int]],
    ) -> tuple[Message[OutboundAttachment], ...] | MessageSendFreshnessHold:
        """Hand the message to its channels and record what each made of it.

        Every conversation gets its last look under its own lock before
        anything goes out, so something new in any of them holds the whole
        while nothing has been sent yet.
        """

        async with AsyncExitStack() as locks:
            for target, _, _ in sorted(staged, key=lambda item: item[0].thread.id):
                await locks.enter_async_context(
                    self._concurrency.for_thread(Thread(target.thread.id))
                )
            prepared: list[_PreparedDelivery] = []
            for target, payload, expected_target_seq in staged:
                materialized = await self.storage.materialize_outbound_if_fresh(
                    Thread(target.thread.id),
                    expected_target_seq,
                    payload=payload,
                    attempted_at_ms=self._clock(),
                )
                outcome = materialized.outcome
                if isinstance(outcome, MessageSendFreshnessHold):
                    return await self._hold(outcome, target=target)
                prepared.append(
                    _PreparedDelivery(
                        target, expected_target_seq, outcome, materialized
                    )
                )
            # the conversations are delivered side by side, so the send takes
            # as long as the slowest one rather than all of them added up
            return tuple(
                await asyncio.gather(*(self._deliver_one(item) for item in prepared))
            )

    async def _deliver_one(
        self, item: _PreparedDelivery
    ) -> Message[OutboundAttachment]:
        target = item.target
        outbound = item.outbound
        channel_session = item.materialized.channel_session
        audit_context = self._correlation(
            thread_id=Thread(target.thread.id),
            channel=channel_session.channel,
            channel_session_id=channel_session.id,
            inbound_seq=item.expected_target_seq,
            outbound_message_id=outbound.message_id,
        )
        await self._publish(
            event_name="bcc.send.fresh_check.passed",
            state=RuntimeEventState.COMPLETED,
            correlation=audit_context,
        )
        await self._publish(
            event_name="channel.outbound.pending",
            state=RuntimeEventState.STARTED,
            correlation=audit_context,
        )
        outbound, delivery_result = await self._transmit(
            outbound,
            channel_session,
            reply_to_provider_message_id=item.materialized.reply_to_provider_message_id,
        )
        delivery_state = outbound.delivery_state
        if delivery_state is None:
            raise RuntimeError("outbound message has no delivery state")
        await self._record_delivery(
            audit_context,
            outbound,
            delivery_result,
            canonical_target=target.canonical_target,
        )
        return outbound

    async def send_message(
        self,
        *,
        actor: Actor,
        raw_target: str,
        body: str,
        created_at_ms: int,
        attachment_paths: tuple[str, ...] = (),
        reply_to_message_id: str | None = None,
        send_draft: bool = False,
    ) -> MessageSendResult:
        attachments = (
            await asyncio.to_thread(self._attachment_resolver, attachment_paths)
            if attachment_paths and not send_draft
            else ()
        )
        if send_draft and (body or attachment_paths or reply_to_message_id is not None):
            raise ValueError(
                "send_draft cannot be combined with body, reply, or attachments"
            )
        if not send_draft and not body.strip() and not attachments:
            raise ValueError("outbound message must not be empty")
        return await self._send(
            actor=actor,
            raw_target=raw_target,
            body=body,
            created_at_ms=created_at_ms,
            attachments=attachments,
            reply_to_message_id=reply_to_message_id,
            send_draft=send_draft,
        )

    async def _send(
        self,
        *,
        actor: Actor,
        raw_target: str,
        body: str,
        created_at_ms: int,
        attachments: tuple[OutboundAttachment, ...],
        reply_to_message_id: str | None,
        send_draft: bool,
    ) -> MessageSendResult:
        try:
            targets = await self.storage.resolve_inbox_targets(raw_target)
        except InboxTargetResolutionError:
            opening = await self._dm_opening(actor, raw_target)
            if opening is None or send_draft:
                raise
            # the conversation may be open already under a name this token does
            # not answer to, and then there is nothing to open
            held = await self.storage.find_channel_session(
                channel=opening.channel,
                channel_identity=opening.address.channel_identity,
                provider_thread_id=opening.address.provider_thread_id,
            )
            # a conversation whose thread never made it is not open yet, and
            # resolving it would fail for good
            if held is not None and await self.storage.find_thread(held.id) is None:
                held = None
            if held is None:
                return await self._open_dm(
                    raw_target=raw_target,
                    opening=opening,
                    body=body,
                    attachments=attachments,
                    created_at_ms=created_at_ms,
                )
            targets = (await self.storage.resolve_inbox_target(held.canonical_target),)
        for target in targets:
            self._require_in_reach(actor, Thread(target.thread.id), raw_target)
            _require_approved(target, raw_target)
        # a handle held on several bots names the same peer everywhere, so the
        # message goes to every conversation; two conversations on one bot
        # are two different peers wearing the same name, and picking would
        # be guessing
        bots = {
            (target.channel_session.channel, target.channel_session.channel_identity)
            for target in targets
        }
        if len(bots) != len(targets):
            raise AmbiguousInboxTargetError(
                "inbox target resolves to more than one conversation on one bot"
            )

        # the draft belongs to the conversation as the caller named it, however
        # many threads that spans; something new in any of them holds the whole
        # send and the one draft waits for the next attempt
        draft_key = tuple(
            sorted(
                (Thread(target.thread.id) for target in targets),
                key=lambda actor: actor.id,
            )
        )
        if send_draft:
            draft = self._drafts.get(draft_key)
            if draft is None:
                raise ValueError(f"no active draft for target: {raw_target}")
        else:
            draft = MessageDraft(
                target=targets[0].canonical_target,
                actor=Thread(targets[0].thread.id),
                body=body,
                attachments=attachments,
                reply_to_message_id=reply_to_message_id,
                created_at_ms=created_at_ms,
            )
        draft_replaced = not send_draft and draft_key in self._drafts
        if not send_draft:
            self._drafts[draft_key] = draft
        staged: list[tuple[ResolvedInboxTarget, MessageDraft, int]] = []
        hold: MessageSendFreshnessHold | None = None
        for target in targets:
            payload = replace(
                draft, target=target.canonical_target, actor=Thread(target.thread.id)
            )
            settled = await self._stage_draft(
                target=target,
                payload=payload,
                draft_replaced=draft_replaced,
            )
            if isinstance(settled, MessageSendFreshnessHold):
                hold = hold or settled
                continue
            payload, expected_target_seq = settled
            staged.append((target, payload, expected_target_seq))
        if hold is not None:
            return _named_as_called(hold, raw_target, targets)

        delivered = await self._deliver(staged)
        if isinstance(delivered, MessageSendFreshnessHold):
            return _named_as_called(delivered, raw_target, targets)
        deliveries = tuple(
            MessageSendSuccess(message=outbound, target=target.display_target)
            for (target, _, _), outbound in zip(staged, delivered, strict=True)
        )
        if self._drafts.get(draft_key) is draft and all(
            outcome.message.delivery_state
            in {OutboundDeliveryState.SENT, OutboundDeliveryState.QUEUED}
            for outcome in deliveries
        ):
            self._drafts.pop(draft_key, None)
        if len(deliveries) == 1:
            return deliveries[0]
        return MessageBroadcast(deliveries=deliveries)

    def _observe_freshness(self, thread_id: Thread, seq: int) -> None:
        previous = self._freshness_snapshots.get(thread_id)
        if previous is None or seq > previous:
            self._freshness_snapshots[thread_id] = seq

    async def _audit_freshness_hold(
        self,
        *,
        thread_id: Thread,
        target: str,
        result: MessageSendFreshnessHold,
    ) -> None:
        audit_context = self._correlation(
            thread_id=thread_id,
            inbound_seq=result.current_inbound_seq,
        )
        await self._publish(
            event_name="bcc.send.freshness_hold",
            state=RuntimeEventState.COMPLETED,
            correlation=audit_context,
            metadata={
                "target": target,
                "snapshot_seq": result.snapshot_seq,
                "current_inbound_seq": result.current_inbound_seq,
                "shown": len(result.messages),
                "total": result.newer_message_total,
                "draft_replaced": result.draft_replaced,
            },
        )
        await self._publish(
            state=RuntimeEventState.COMPLETED,
            correlation=audit_context,
            event_name="tool." + "bcc.message.send" + "." + "freshness_hold",
            metadata={
                "kind": "tool_call",
                "operation": "bcc.message.send",
                "status": "freshness_hold",
                "arguments": {
                    "target": target,
                    "shown": len(result.messages),
                    "total": result.newer_message_total,
                    "draft_replaced": result.draft_replaced,
                },
            },
        )

    async def unfollow_thread(
        self, actor: Actor, *, raw_target: str
    ) -> ThreadUnfollowResult:
        target = await self.storage.resolve_inbox_target(raw_target)
        thread_id = Thread(target.thread.id)
        self._require_in_reach(actor, thread_id, raw_target)
        _require_approved(target, raw_target)
        async with self._concurrency.for_thread(thread_id):
            channel_session = target.channel_session
            target_messages = await self.storage.list_messages(
                thread_id,
                target=target.canonical_target,
                direction=MessageDirection.INBOUND,
                limit=1,
            )
            if not target_messages:
                raise ValueError(f"Thread target is not found: {raw_target}")
            changed = (
                channel_session.target_kind is ChannelTargetKind.GROUP
                and channel_session.following
            )
            if changed:
                channel_session = replace(
                    channel_session,
                    following=False,
                    updated_at_ms=self._clock(),
                )
                await self.storage.save_channel_session(channel_session)
        await self._publish(
            state=RuntimeEventState.COMPLETED,
            correlation=self._correlation(
                thread_id=thread_id,
                channel=channel_session.channel,
                channel_session_id=channel_session.id,
            ),
            event_name="tool." + "bcc.thread.unfollow" + "." + "completed",
            metadata={
                "kind": "tool_call",
                "operation": "bcc.thread.unfollow",
                "status": "completed",
                "arguments": {
                    "thread_id": thread_id.id if thread_id is not None else None,
                    "target": target.canonical_target,
                    "changed": changed,
                },
            },
        )
        return ThreadUnfollowResult(target=target.display_target, changed=changed)
