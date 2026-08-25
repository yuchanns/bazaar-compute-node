from __future__ import annotations

import asyncio
import hashlib
import logging
import mimetypes
import os
import stat
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path, PurePosixPath

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
    SessionNotFoundError,
)
from ..concurrency import ISessionConcurrency
from ..correlation import CorrelationContext
from ..models import (
    ChannelTargetKind,
    InboundAttachment,
    Message,
    MessageDirection,
    OutboundAttachment,
    OutboundDeliveryState,
    RuntimeEventState,
)
from ..storage import IStorage
from .delivery import OutboundDeliveryService
from .services import SessionAuditRecorder


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


class SessionCommandService(ICommandService):
    """Execute session-scoped check, read, and send commands."""

    def __init__(
        self,
        *,
        delivery: OutboundDeliveryService,
        storage: IStorage,
        audit: SessionAuditRecorder,
        concurrency: ISessionConcurrency,
        node_id: Callable[[], str],
        workspace: Callable[[], Path],
        clock: Callable[[], int],
        publish_wake: Callable[[Message[InboundAttachment]], Awaitable[None]],
    ) -> None:
        self._delivery = delivery
        self._storage = storage
        self._audit = audit
        self._concurrency = concurrency
        self._node_id = node_id
        self._attachment_resolver = OutboundAttachmentResolver(workspace)
        self._clock = clock
        self._publish_wake = publish_wake
        self._drafts: dict[str, MessageDraft] = {}
        self._freshness_snapshots: dict[str, int] = {}
        self._logger = logging.getLogger("bazaar_compute_node.orchestration.command")

    async def check(self, session_id: str) -> MessageCheckResult:
        async with self._concurrency.for_session(session_id):
            result = await self._storage.check_messages(
                session_id,
                checked_at_ms=self._clock(),
            )
            self._observe_freshness(session_id, result.snapshot_seq)
        await self._audit.append_tool(
            operation="bcc.message.check",
            status="completed",
            state=RuntimeEventState.COMPLETED,
            correlation=self._correlation(session_id=session_id),
            arguments={"session_id": session_id},
        )
        return result

    async def read(
        self,
        session_id: str,
        *,
        target: str,
        around_message_id: str | None = None,
        limit: int = 100,
    ) -> MessageReadResult:
        snapshot = await self._storage.read_message_history(
            session_id,
            target=target,
            around_message_id=around_message_id,
            limit=limit,
        )
        result = snapshot.history
        if snapshot.source_session.id == session_id:
            self._observe_freshness(session_id, result.snapshot_seq)
        await self._audit.append_tool(
            operation="bcc.message.read",
            status="completed",
            state=RuntimeEventState.COMPLETED,
            correlation=self._correlation(session_id=session_id),
            arguments={
                "caller_session_id": session_id,
                "source_session_id": snapshot.source_session.id,
                "target": target,
                "around_message_id": around_message_id,
                "limit": limit,
            },
        )
        return result

    async def list_inbox(
        self,
        caller_session_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> InboxListResult:
        result = await self._storage.read_inbox_catalog(
            caller_session_id,
            limit=limit,
            offset=offset,
        )
        await self._audit.append_tool(
            operation="bcc.inbox.list",
            status="completed",
            state=RuntimeEventState.COMPLETED,
            correlation=self._correlation(session_id=caller_session_id),
            arguments={
                "caller_session_id": caller_session_id,
                "limit": limit,
                "offset": offset,
            },
        )
        return result

    async def send(
        self,
        *,
        session_id: str,
        command_id: str,
        target: str,
        body: str,
        created_at_ms: int,
        attachment_paths: tuple[str, ...] = (),
        reply_to_message_id: str | None = None,
        send_draft: bool = False,
    ) -> MessageSendResult:
        target_session = await self._storage.resolve_inbox_target(target)
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

        async with self._concurrency.for_session(session_id):
            if send_draft:
                draft = self._drafts.get(session_id)
                if draft is None:
                    raise ValueError(f"no active draft for target: {target}")
                if draft.target != target or draft.target_id != target_session.id:
                    raise ValueError(
                        f"active draft belongs to another target: {draft.target}"
                    )
                payload = draft
            else:
                source_message = await self._storage.get_latest_message(
                    session_id,
                    direction=MessageDirection.INBOUND,
                )
                if source_message is None:
                    raise ValueError(
                        "current conversation has no inbound source anchor"
                    )
                payload = MessageDraft(
                    source_target_id=session_id,
                    target=target,
                    target_id=target_session.id,
                    body=body,
                    attachments=attachments,
                    reply_to_message_id=reply_to_message_id,
                    source_message_id=source_message.message_id,
                    created_at_ms=created_at_ms,
                )
            active_draft = self._drafts.get(session_id)
            draft_replaced = not send_draft and active_draft is not None
            if not send_draft:
                self._drafts[session_id] = payload
            freshness = await self._storage.check_outbound_freshness(
                session_id,
                source_snapshot_seq=self._freshness_snapshots.get(session_id),
                payload=payload,
                draft_replaced=draft_replaced,
            )
            if isinstance(freshness, MessageSendFreshnessHold):
                self._observe_freshness(session_id, freshness.current_inbound_seq)
                await self._audit_freshness_hold(
                    session_id=session_id,
                    command_id=command_id,
                    target=target,
                    result=freshness,
                )
                return freshness
            expected_source_seq = freshness.current_inbound_seq

        handoff_message: Message[InboundAttachment] | None = None
        async with self._concurrency.for_session(target_session.id):
            prepared = await self._storage.materialize_outbound_if_fresh(
                session_id,
                expected_source_seq,
                target_session.id,
                command_id=command_id,
                payload=payload,
                attempted_at_ms=self._clock(),
            )
            result = prepared.outcome
            if isinstance(result, MessageSendFreshnessHold):
                self._observe_freshness(session_id, result.current_inbound_seq)
                await self._audit_freshness_hold(
                    session_id=session_id,
                    command_id=command_id,
                    target=target,
                    result=result,
                )
                return result
            outbound = result
            channel_session = prepared.channel_session
            audit_context = self._correlation(
                session_id=session_id,
                channel=channel_session.channel,
                channel_session_id=channel_session.id,
                command_id=command_id,
                inbound_seq=expected_source_seq,
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
            delivery_result = await self._delivery.deliver(
                ChannelSendRequest(
                    session_id=outbound.session_id,
                    body=outbound.body,
                    attachments=outbound.attachments,
                    target_kind=channel_session.target_kind,
                    provider_thread_id=channel_session.provider_thread_id,
                    provider_reply_to_message_id=(
                        prepared.reply_to_provider_message_id
                    ),
                )
            )
            attempted_at_ms = outbound.provider_attempted_at_ms or self._clock()
            outbound = replace(outbound, provider_attempted_at_ms=attempted_at_ms)
            outbound = outbound.transition_to(
                delivery_result.state,
                at_ms=self._clock(),
                provider_message_id=delivery_result.provider_message_id,
                provider_receipt_ref=delivery_result.provider_receipt_ref,
                error_kind=delivery_result.error_kind,
                error_message=delivery_result.error_message,
            )
            if delivery_result.state is OutboundDeliveryState.SENT:
                terminal_kind = None
                terminal_state = RuntimeEventState.COMPLETED
            elif delivery_result.state is OutboundDeliveryState.QUEUED:
                terminal_kind = None
                terminal_state = RuntimeEventState.STARTED
            elif delivery_result.state is OutboundDeliveryState.PARTIAL:
                terminal_kind = ErrorKind.PROVIDER_PARTIAL
                terminal_state = RuntimeEventState.FAILED
            elif delivery_result.state is OutboundDeliveryState.FAILED:
                terminal_kind = ErrorKind.PROVIDER_FAILED
                terminal_state = RuntimeEventState.FAILED
            else:
                terminal_kind = ErrorKind.PROVIDER_UNKNOWN
                terminal_state = RuntimeEventState.UNKNOWN

            if delivery_result.receipt:
                outbound = replace(
                    outbound,
                    metadata={
                        **outbound.metadata,
                        "delivery_receipt": dict(delivery_result.receipt),
                    },
                )

            finalized = await self._storage.finalize_outbound_delivery(outbound)
            outbound = finalized.outbound
            handoff_message = finalized.handoff_message
            delivery_state = outbound.delivery_state
            if delivery_state is None:
                raise RuntimeError("outbound message has no delivery state")
            if (
                delivery_state
                in {
                    OutboundDeliveryState.SENT,
                    OutboundDeliveryState.QUEUED,
                }
                and self._drafts.get(session_id) is payload
            ):
                self._drafts.pop(session_id, None)
            await self._audit.append(
                event_name=f"channel.outbound.{delivery_state.value}",
                state=terminal_state,
                correlation=audit_context,
                error_kind=terminal_kind,
                error_message=outbound.error_message,
                metadata=delivery_result.receipt,
            )
            await self._audit.append_tool(
                operation="bcc.message.send",
                status=delivery_state.value,
                state=terminal_state,
                correlation=audit_context,
                arguments={
                    "command_id": command_id,
                    "target": target,
                    "delivery_state": delivery_state.value,
                },
                error_kind=terminal_kind,
                error_message=outbound.error_message,
            )
        if handoff_message is not None:
            try:
                await self._publish_wake(handoff_message)
            except asyncio.CancelledError:
                raise
            except Exception:
                self._logger.exception("Handoff inbox wake could not be published")
        return outbound

    def _observe_freshness(self, session_id: str, seq: int) -> None:
        previous = self._freshness_snapshots.get(session_id)
        if previous is None or seq > previous:
            self._freshness_snapshots[session_id] = seq

    async def _audit_freshness_hold(
        self,
        *,
        session_id: str,
        command_id: str,
        target: str,
        result: MessageSendFreshnessHold,
    ) -> None:
        audit_context = self._correlation(
            session_id=session_id,
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

    async def unfollow(self, session_id: str, *, target: str) -> bool:
        async with self._concurrency.for_session(session_id):
            bcn_session = await self._storage.get_bcn_session(session_id)
            if bcn_session is None:
                raise SessionNotFoundError(f"unknown bcn session: {session_id}")
            channel_session = await self._storage.get_channel_session(
                bcn_session.channel_session_id
            )
            if channel_session is None:
                raise ValueError(
                    f"unknown channel session: {bcn_session.channel_session_id}"
                )
            target_messages = await self._storage.list_messages(
                session_id,
                target=target,
                direction=MessageDirection.INBOUND,
                limit=1,
            )
            if not target_messages:
                raise ValueError(f"Thread target is not found: {target}")
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
                session_id=session_id,
                channel=channel_session.channel,
                channel_session_id=channel_session.id,
            ),
            arguments={"session_id": session_id, "target": target, "changed": changed},
        )
        return changed

    def _correlation(
        self,
        *,
        session_id: str,
        channel: str | None = None,
        channel_session_id: str | None = None,
        command_id: str | None = None,
        inbound_seq: int | None = None,
        outbound_message_id: str | None = None,
    ) -> CorrelationContext:
        return CorrelationContext(
            node_id=self._node_id(),
            channel=channel,
            channel_session_id=channel_session_id,
            bcn_session_id=session_id,
            command_id=command_id,
            inbound_seq=inbound_seq,
            outbound_message_id=outbound_message_id,
        )
