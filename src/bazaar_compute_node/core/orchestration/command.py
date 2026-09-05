from __future__ import annotations

import asyncio
import hashlib
import logging
import mimetypes
import os
import stat
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path, PurePosixPath

from ..actor import Actor, Actors, Agent, Thread
from ..audit import ErrorKind
from ..channel import ChannelSendRequest
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
    ChannelTargetKind,
    InboxTargetSummary,
    Message,
    MessageDirection,
    OutboundAttachment,
    OutboundDeliveryState,
    RuntimeEventState,
)
from ..outcomes import OutboundDeliveryResult
from ..storage import (
    InboxTargetResolutionError,
    IStorage,
    MaterializeOutboundResult,
    ResolvedInboxTarget,
)
from .delivery import OutboundDeliveryService
from .services import _REACH_PAGE, AuditRecorder, threads_in_reach


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


class CommandService(ICommandService):
    """Execute session-scoped check, read, and send commands."""

    def __init__(
        self,
        *,
        actors: Actors,
        delivery: OutboundDeliveryService,
        storage: IStorage,
        audit: AuditRecorder,
        concurrency: IThreadConcurrency,
        workspace: Callable[[], Path],
        clock: Callable[[], int],
    ) -> None:
        self._actors = actors
        self._delivery = delivery
        self._storage = storage
        self._audit = audit
        self._concurrency = concurrency
        self._attachment_resolver = OutboundAttachmentResolver(workspace)
        self._clock = clock
        self._drafts: dict[str, MessageDraft] = {}
        self._freshness_snapshots: dict[str, int] = {}
        self._logger = logging.getLogger("bazaar_compute_node.orchestration.command")

    async def pending_targets(self, actor: Actor) -> InboxListResult:
        reachable = frozenset(await threads_in_reach(self._storage, actor))
        pending: list[InboxTargetSummary] = []
        offset = 0
        while True:
            result = await self._storage.read_inbox_catalog(
                limit=_REACH_PAGE, offset=offset
            )
            pending.extend(
                summary
                for summary in result.targets
                if summary.pending_count > 0 and summary.thread_id in reachable
            )
            if not result.targets or not result.has_more:
                break
            offset += len(result.targets)
        await self._audit.append_tool(
            operation="bcc.inbox.check",
            status="completed",
            state=RuntimeEventState.COMPLETED,
            correlation=self._correlation(thread_id=actor.id),
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
        outbound = replace(
            outbound,
            provider_attempted_at_ms=outbound.provider_attempted_at_ms or self._clock(),
        )
        outbound = outbound.transition_to(
            delivery_result.state,
            at_ms=self._clock(),
            provider_message_id=delivery_result.provider_message_id,
            provider_receipt_ref=delivery_result.provider_receipt_ref,
            error_kind=delivery_result.error_kind,
            error_message=delivery_result.error_message,
        )
        if delivery_result.receipt:
            outbound = replace(
                outbound,
                metadata={
                    **outbound.metadata,
                    "delivery_receipt": dict(delivery_result.receipt),
                },
            )
        return outbound, delivery_result

    async def _record_delivery(
        self,
        audit_context: CorrelationContext,
        *,
        command_id: str,
        canonical_target: str,
        delivery_state: OutboundDeliveryState,
        error_message: str | None,
        receipt: Mapping[str, object] | None,
        terminal_kind: ErrorKind | None,
        terminal_state: RuntimeEventState,
    ) -> None:
        """Write down what the channel did with the message, twice over."""

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
            terminal_kind, terminal_state = _DELIVERY_OUTCOMES.get(
                delivery_result.state,
                (ErrorKind.PROVIDER_UNKNOWN, RuntimeEventState.UNKNOWN),
            )
            outbound = await self._storage.finalize_outbound_delivery(outbound)
            delivery_state = outbound.delivery_state
            if delivery_state is None:
                raise RuntimeError("outbound message has no delivery state")
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
                command_id=command_id,
                canonical_target=canonical_target,
                delivery_state=delivery_state,
                error_message=outbound.error_message,
                receipt=delivery_result.receipt,
                terminal_kind=terminal_kind,
                terminal_state=terminal_state,
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
        target = await self._storage.resolve_inbox_target(raw_target)
        self._require_in_reach(actor, target.thread.id, raw_target)
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
        thread_id: str,
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
            command_id=command_id,
            inbound_seq=inbound_seq,
            outbound_message_id=outbound_message_id,
        )
