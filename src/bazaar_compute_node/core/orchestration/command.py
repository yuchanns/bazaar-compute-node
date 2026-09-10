from __future__ import annotations

import asyncio
import hashlib
import logging
import mimetypes
import os
import stat
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from uuid import uuid7

from ..actor import Actor, Actors, Agent, Thread
from ..audit import AuditRecorder, ErrorKind
from ..channel import ChannelSendRequest, DmAddress, IChannel
from ..command import (
    ICommandService,
    InboxListResult,
    MessageCheckResult,
    MessageDraft,
    MessageReadResult,
    MessageSendFreshnessHold,
    MessageSendResult,
    MessageSendSuccess,
    ThreadUnfollowResult,
)
from ..concurrency import IThreadConcurrency
from ..correlation import CorrelationContext
from ..models import (
    ChannelSession,
    ChannelTargetKind,
    Message,
    MessageDirection,
    OutboundAttachment,
    OutboundDeliveryState,
    RuntimeEventState,
)

# `Thread` here is the actor variant; the conversation row keeps its own name.
from ..models import Thread as ConversationRow
from ..outcomes import OutboundDeliveryResult
from ..storage import (
    AmbiguousInboxTargetError,
    InboxTargetResolutionError,
    IStorage,
    MaterializeOutboundResult,
    ResolvedInboxTarget,
)
from .delivery import OutboundDeliveryService
from .services import threads_in_reach


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
class _DmOpening:
    """Where a DM that has never been held would be sent, and what to call it."""

    address: DmAddress
    channel: str
    handle: str


class CommandService(ICommandService):
    """Execute session-scoped check, read, and send commands."""

    def __init__(
        self,
        *,
        actors: Actors,
        channel: IChannel,
        delivery: OutboundDeliveryService,
        storage: IStorage,
        audit: AuditRecorder,
        concurrency: IThreadConcurrency,
        workspace: Callable[[], Path],
        clock: Callable[[], int],
    ) -> None:
        self._actors = actors
        self._channel = channel
        self._delivery = delivery
        self._storage = storage
        self._audit = audit
        self._concurrency = concurrency
        self._attachment_resolver = OutboundAttachmentResolver(workspace)
        self._clock = clock
        self._drafts: dict[str, MessageDraft] = {}
        # an outbound is written down once the provider answers, so until then
        # only this holds a command to its one attempt
        self._sending: set[str] = set()
        self._freshness_snapshots: dict[str, int] = {}
        self._logger = logging.getLogger("bazaar_compute_node.orchestration.command")

    async def pending_targets(self, actor: Actor) -> InboxListResult:
        reachable = frozenset(await threads_in_reach(self._storage, actor))
        result = await self._storage.read_inbox_catalog(limit=None)
        pending = [
            summary
            for summary in result.targets
            if summary.pending_count > 0 and summary.thread_id in reachable
        ]
        await self._audit.append_tool(
            operation="bcc.inbox.check",
            status="completed",
            state=RuntimeEventState.COMPLETED,
            correlation=self._correlation(actor=actor),
            arguments={"actor_id": actor.id},
        )
        return replace(
            result,
            targets=tuple(pending),
            total=len(pending),
            shown=len(pending),
            offset=0,
            has_more=False,
        )

    async def check(self, actor: Actor) -> tuple[MessageCheckResult, ...]:
        thread_ids = await threads_in_reach(self._storage, actor)
        drained = await self._storage.check_messages(
            thread_ids,
            checked_at_ms=self._clock(),
        )
        for thread_id, result in zip(thread_ids, drained, strict=True):
            self._observe_freshness(thread_id, result.snapshot_seq)
            await self._audit.append_tool(
                operation="bcc.message.check",
                status="completed",
                state=RuntimeEventState.COMPLETED,
                correlation=self._correlation(thread_id=thread_id),
                arguments={"thread_id": thread_id},
            )
        return drained

    async def read(
        self,
        actor: Actor,
        *,
        raw_target: str,
        around_message_id: str | None = None,
        limit: int = 100,
    ) -> MessageReadResult:
        snapshot = await self._storage.read_message_history(
            raw_target=raw_target,
            around_message_id=around_message_id,
            limit=limit,
        )
        self._require_in_reach(actor, snapshot.source_thread.id, raw_target)
        result = snapshot.history
        self._observe_freshness(snapshot.source_thread.id, result.snapshot_seq)
        await self._audit.append_tool(
            operation="bcc.message.read",
            status="completed",
            state=RuntimeEventState.COMPLETED,
            correlation=self._correlation(thread_id=snapshot.source_thread.id),
            arguments={
                "actor_id": actor.id,
                "source_thread_id": snapshot.source_thread.id,
                "target": raw_target,
                "around_message_id": around_message_id,
                "limit": limit,
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
        known = await self._storage.find_known_sender(raw_target[4:])
        if known is None:
            return None
        address = self._channel.dm_address(known.sender, sender_kind=known.sender_kind)
        if address is None:
            return None
        # The name the provider calls the peer comes first, then the token that
        # found it.
        handle = known.sender.name or raw_target[4:]
        return _DmOpening(address=address, channel=known.channel, handle=handle)

    async def _open_dm(
        self,
        *,
        command_id: str,
        raw_target: str,
        opening: _DmOpening,
        body: str,
        attachments: tuple[OutboundAttachment, ...],
        created_at_ms: int,
    ) -> MessageSendSuccess:
        """Send into a conversation that does not exist yet, then write it down.

        The provider names the conversation it delivered into, and a chat opened
        by a name it answers to is not reachable by its id until then. Writing
        afterwards is what keeps this message and every later one in the same
        conversation.
        """

        address = opening.address
        attempted_at_ms = self._clock()
        delivery_result = await self._delivery.deliver(
            ChannelSendRequest(
                session_id=address.thread_id,
                body=body,
                attachments=attachments,
                target_kind=ChannelTargetKind.DM,
                provider_thread_id=address.provider_thread_id,
                delivery_handle=address.delivery_handle,
            )
        )
        now = self._clock()
        session = ChannelSession(
            id=address.channel_session_id,
            channel=opening.channel,
            provider_thread_id=(
                delivery_result.provider_thread_id or address.provider_thread_id
            ),
            created_at_ms=now,
            updated_at_ms=now,
            target_kind=ChannelTargetKind.DM,
            target_handle=opening.handle,
            target_handle_key=opening.handle.casefold(),
        )
        outbound = Message[OutboundAttachment](
            direction=MessageDirection.OUTBOUND,
            seq=0,
            message_id=str(uuid7()),
            command_id=command_id,
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
        outbound = _answered(outbound, delivery_result, at_ms=self._clock())
        delivery_state = outbound.delivery_state
        if delivery_state is None:
            raise RuntimeError("outbound message has no delivery state")
        audit_context = self._correlation(
            thread_id=address.thread_id,
            channel=session.channel,
            channel_session_id=session.id,
            command_id=command_id,
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
                command_id=command_id,
                canonical_target=raw_target,
            )
            return MessageSendSuccess(message=outbound, target=raw_target)
        await self._storage.save_channel_session(session)
        await self._storage.save_thread(
            ConversationRow(
                id=address.thread_id,
                channel_session_id=session.id,
                workspace_id=self._actors.agent_id,
                created_at_ms=now,
                updated_at_ms=now,
            )
        )
        outbound = await self._storage.finalize_outbound_delivery(outbound)
        await self._record_delivery(
            audit_context,
            outbound,
            delivery_result,
            command_id=command_id,
            canonical_target=session.canonical_target,
        )
        resolved = await self._storage.resolve_inbox_target(session.canonical_target)
        return MessageSendSuccess(message=outbound, target=resolved.display_target)

    def _require_in_reach(
        self,
        actor: Actor,
        target_thread_id: str,
        raw_target: str,
    ) -> None:
        """Refuse a target this actor does not answer for."""

        match actor:
            case Agent():
                return
            case Thread(id) if id == target_thread_id:
                return
            case Thread():
                raise InboxTargetResolutionError(
                    f"inbox target is not this conversation: {raw_target}"
                )

    async def _stage_draft(
        self,
        *,
        command_id: str,
        raw_target: str,
        target: ResolvedInboxTarget,
        body: str,
        created_at_ms: int,
        attachments: tuple[OutboundAttachment, ...],
        reply_to_message_id: str | None,
        send_draft: bool,
    ) -> tuple[MessageDraft, int] | MessageSendFreshnessHold:
        """Settle what will be sent, and that nothing arrived while it was written."""

        target_id = target.thread.id
        async with self._concurrency.for_thread(target_id):
            if send_draft:
                draft = self._drafts.get(target_id)
                if draft is None:
                    raise ValueError(f"no active draft for target: {raw_target}")
                payload = draft
            else:
                payload = MessageDraft(
                    target=target.canonical_target,
                    target_id=target_id,
                    body=body,
                    attachments=attachments,
                    reply_to_message_id=reply_to_message_id,
                    created_at_ms=created_at_ms,
                )
            draft_replaced = not send_draft and self._drafts.get(target_id) is not None
            if not send_draft:
                self._drafts[target_id] = payload
            freshness = await self._storage.check_outbound_freshness(
                target_id,
                snapshot_seq=self._freshness_snapshots.get(target_id),
                payload=payload,
                draft_replaced=draft_replaced,
            )
            if isinstance(freshness, MessageSendFreshnessHold):
                return await self._hold(
                    freshness,
                    command_id=command_id,
                    target=target,
                )
            return payload, freshness.current_inbound_seq

    async def _hold(
        self,
        hold: MessageSendFreshnessHold,
        *,
        command_id: str,
        target: ResolvedInboxTarget,
    ) -> MessageSendFreshnessHold:
        """Record that something arrived while the message was being written."""

        target_id = target.thread.id
        self._observe_freshness(target_id, hold.current_inbound_seq)
        await self._audit_freshness_hold(
            thread_id=target_id,
            command_id=command_id,
            target=target.canonical_target,
            result=hold,
        )
        return replace(hold, target=target.display_target)

    async def _transmit(
        self,
        outbound: Message[OutboundAttachment],
        prepared: MaterializeOutboundResult,
    ) -> tuple[Message[OutboundAttachment], OutboundDeliveryResult]:
        """Give the message to its channel and fold the answer back into it."""

        channel_session = prepared.channel_session
        delivery_result = await self._delivery.deliver(
            ChannelSendRequest(
                session_id=outbound.thread_id,
                body=outbound.body,
                attachments=outbound.attachments,
                target_kind=channel_session.target_kind,
                provider_thread_id=channel_session.provider_thread_id,
                provider_reply_to_message_id=prepared.reply_to_provider_message_id,
            )
        )
        return _answered(
            outbound, delivery_result, at_ms=self._clock()
        ), delivery_result

    async def _record_delivery(
        self,
        audit_context: CorrelationContext,
        outbound: Message[OutboundAttachment],
        delivery_result: OutboundDeliveryResult,
        *,
        command_id: str,
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
        await self._audit.append(
            event_name=f"channel.outbound.{delivery_state.value}",
            state=terminal_state,
            correlation=audit_context,
            error_kind=terminal_kind,
            error_message=error_message,
            metadata=receipt,
        )
        await self._audit.append_tool(
            operation="bcc.message.send",
            status=delivery_state.value,
            state=terminal_state,
            correlation=audit_context,
            arguments={
                "command_id": command_id,
                "target": canonical_target,
                "delivery_state": delivery_state.value,
            },
            error_kind=terminal_kind,
            error_message=error_message,
        )

    async def _deliver(
        self,
        *,
        command_id: str,
        target: ResolvedInboxTarget,
        expected_target_seq: int,
        payload: MessageDraft,
    ) -> Message[OutboundAttachment] | MessageSendFreshnessHold:
        """Hand the message to its channel and record what the channel made of it."""

        canonical_target = target.canonical_target
        target_id = target.thread.id
        async with self._concurrency.for_thread(target_id):
            prepared = await self._storage.materialize_outbound_if_fresh(
                target_id,
                expected_target_seq,
                command_id=command_id,
                payload=payload,
                attempted_at_ms=self._clock(),
            )
            result = prepared.outcome
            if isinstance(result, MessageSendFreshnessHold):
                return await self._hold(
                    result,
                    command_id=command_id,
                    target=target,
                )
            outbound = result
            channel_session = prepared.channel_session
            audit_context = self._correlation(
                thread_id=target_id,
                channel=channel_session.channel,
                channel_session_id=channel_session.id,
                command_id=command_id,
                inbound_seq=expected_target_seq,
                outbound_message_id=outbound.message_id,
            )
            await self._audit.append(
                event_name="bcc.send.fresh_check.passed",
                state=RuntimeEventState.COMPLETED,
                correlation=audit_context,
            )
            await self._audit.append(
                event_name="channel.outbound.pending",
                state=RuntimeEventState.STARTED,
                correlation=audit_context,
            )
            outbound, delivery_result = await self._transmit(outbound, prepared)
            delivery_state = outbound.delivery_state
            if delivery_state is None:
                raise RuntimeError("outbound message has no delivery state")
            if _reached_the_peer(delivery_result):
                outbound = await self._storage.finalize_outbound_delivery(outbound)
            if (
                delivery_state
                in {
                    OutboundDeliveryState.SENT,
                    OutboundDeliveryState.QUEUED,
                }
                and self._drafts.get(target_id) is payload
            ):
                self._drafts.pop(target_id, None)
            await self._record_delivery(
                audit_context,
                outbound,
                delivery_result,
                command_id=command_id,
                canonical_target=canonical_target,
            )
            return outbound

    async def send(
        self,
        *,
        actor: Actor,
        command_id: str,
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
        if command_id in self._sending:
            raise ValueError(f"command was already sent: {command_id}")
        self._sending.add(command_id)
        try:
            if await self._storage.has_outbound_for_command(command_id):
                raise ValueError(f"command was already sent: {command_id}")
            return await self._send(
                actor=actor,
                command_id=command_id,
                raw_target=raw_target,
                body=body,
                created_at_ms=created_at_ms,
                attachments=attachments,
                reply_to_message_id=reply_to_message_id,
                send_draft=send_draft,
            )
        finally:
            self._sending.discard(command_id)

    async def _send(
        self,
        *,
        actor: Actor,
        command_id: str,
        raw_target: str,
        body: str,
        created_at_ms: int,
        attachments: tuple[OutboundAttachment, ...],
        reply_to_message_id: str | None,
        send_draft: bool,
    ) -> MessageSendResult:
        try:
            target = await self._storage.resolve_inbox_target(raw_target)
        except AmbiguousInboxTargetError:
            # Several conversations answer to this handle. Opening another one
            # would silently pick a peer for the caller.
            raise
        except InboxTargetResolutionError:
            opening = await self._dm_opening(actor, raw_target)
            if opening is None or send_draft:
                raise
            # the conversation may be open already under a name this token does
            # not answer to, and then there is nothing to open
            held = await self._storage.find_channel_session(
                channel=opening.channel,
                provider_thread_id=opening.address.provider_thread_id,
            )
            # a conversation whose thread never made it is not open yet, and
            # resolving it would fail for good
            if held is not None and await self._storage.find_thread(held.id) is None:
                held = None
            if held is None:
                return await self._open_dm(
                    command_id=command_id,
                    raw_target=raw_target,
                    opening=opening,
                    body=body,
                    attachments=attachments,
                    created_at_ms=created_at_ms,
                )
            target = await self._storage.resolve_inbox_target(held.canonical_target)
        self._require_in_reach(actor, target.thread.id, raw_target)

        staged = await self._stage_draft(
            command_id=command_id,
            raw_target=raw_target,
            target=target,
            body=body,
            created_at_ms=created_at_ms,
            attachments=attachments,
            reply_to_message_id=reply_to_message_id,
            send_draft=send_draft,
        )
        if isinstance(staged, MessageSendFreshnessHold):
            return staged
        payload, expected_target_seq = staged

        delivered = await self._deliver(
            command_id=command_id,
            target=target,
            expected_target_seq=expected_target_seq,
            payload=payload,
        )
        if isinstance(delivered, MessageSendFreshnessHold):
            return delivered
        return MessageSendSuccess(message=delivered, target=target.display_target)

    def _observe_freshness(self, thread_id: str, seq: int) -> None:
        previous = self._freshness_snapshots.get(thread_id)
        if previous is None or seq > previous:
            self._freshness_snapshots[thread_id] = seq

    async def _audit_freshness_hold(
        self,
        *,
        thread_id: str,
        command_id: str,
        target: str,
        result: MessageSendFreshnessHold,
    ) -> None:
        audit_context = self._correlation(
            thread_id=thread_id,
            command_id=command_id,
            inbound_seq=result.current_inbound_seq,
        )
        await self._audit.append(
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
        await self._audit.append_tool(
            operation="bcc.message.send",
            status="freshness_hold",
            state=RuntimeEventState.COMPLETED,
            correlation=audit_context,
            arguments={
                "command_id": command_id,
                "target": target,
                "shown": len(result.messages),
                "total": result.newer_message_total,
                "draft_replaced": result.draft_replaced,
            },
        )

    async def unfollow(self, actor: Actor, *, raw_target: str) -> ThreadUnfollowResult:
        target = await self._storage.resolve_inbox_target(raw_target)
        thread_id = target.thread.id
        self._require_in_reach(actor, thread_id, raw_target)
        async with self._concurrency.for_thread(thread_id):
            channel_session = target.channel_session
            target_messages = await self._storage.list_messages(
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
                await self._storage.save_channel_session(channel_session)
        await self._audit.append_tool(
            operation="bcc.thread.unfollow",
            status="completed",
            state=RuntimeEventState.COMPLETED,
            correlation=self._correlation(
                thread_id=thread_id,
                channel=channel_session.channel,
                channel_session_id=channel_session.id,
            ),
            arguments={
                "thread_id": thread_id,
                "target": target.canonical_target,
                "changed": changed,
            },
        )
        return ThreadUnfollowResult(target=target.display_target, changed=changed)

    def _correlation(
        self,
        *,
        thread_id: str | None = None,
        actor: Actor | None = None,
        channel: str | None = None,
        channel_session_id: str | None = None,
        command_id: str | None = None,
        inbound_seq: int | None = None,
        outbound_message_id: str | None = None,
    ) -> CorrelationContext:
        return CorrelationContext(
            node_id=self._actors.agent_id,
            channel=channel,
            channel_session_id=channel_session_id,
            thread_id=thread_id,
            actor=actor,
            command_id=command_id,
            inbound_seq=inbound_seq,
            outbound_message_id=outbound_message_id,
        )
