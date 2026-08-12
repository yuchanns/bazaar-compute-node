from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import os
import stat
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path, PurePosixPath

from ..audit import ErrorKind
from ..channel import ChannelSendRequest, IChannel
from ..command import (
    ICommandService,
    MessageCheckResult,
    MessageReadResult,
    SessionNotFoundError,
)
from ..concurrency import ISessionConcurrency
from ..correlation import CorrelationContext
from ..models import (
    ChannelTargetKind,
    ConsumerCursor,
    FreshCheckState,
    InboundMessage,
    OutboundAttachment,
    OutboundDeliveryState,
    OutboundMessage,
    RuntimeEventState,
)
from ..outcomes import ProviderCallStatus
from ..storage import IStorage, IStorageTransaction
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
            if not isinstance(raw_path, str) or not raw_path:
                raise ValueError("attachment paths must be non-empty strings")
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

    def verify(self, attachments: tuple[OutboundAttachment, ...]) -> None:
        workspace = self._workspace().resolve(strict=True)
        current = self(
            tuple(
                str(workspace.joinpath(*PurePosixPath(attachment.relative_path).parts))
                for attachment in attachments
            )
        )
        if current != attachments:
            raise ValueError("attachment content changed after the draft was accepted")


class SessionCommandService(ICommandService):
    """Execute session-scoped check, read, and send commands."""

    def __init__(
        self,
        *,
        channel: IChannel,
        storage: IStorage,
        audit: SessionAuditRecorder,
        provider_call_timeout: float,
        concurrency: ISessionConcurrency,
        node_id: Callable[[], str],
        workspace: Callable[[], Path],
        clock: Callable[[], int],
    ) -> None:
        self._channel = channel
        self._storage = storage
        self._audit = audit
        self._provider_call_timeout = provider_call_timeout
        self._concurrency = concurrency
        self._node_id = node_id
        self._attachment_resolver = OutboundAttachmentResolver(workspace)
        self._clock = clock

    async def check(self, session_id: str) -> MessageCheckResult:
        async with (
            self._concurrency.for_session(session_id),
            self._storage.transaction() as transaction,
        ):
            bcn_session = await transaction.get_bcn_session(session_id)
            if bcn_session is None:
                raise SessionNotFoundError(f"unknown bcn session: {session_id}")
            cursor = await transaction.get_consumer_cursor(session_id)
            if cursor is None:
                cursor = ConsumerCursor(session_id=session_id)
            latest_seq = await transaction.get_latest_inbound_seq(session_id)
            messages = await transaction.list_inbound_messages(
                session_id,
                after_seq=cursor.delivered_through_seq,
                notifying_only=True,
            )
            referenced_messages = await self._referenced_messages(
                transaction,
                session_id=session_id,
                messages=messages,
            )
            now_ms = self._clock()
            cursor = replace(
                cursor,
                delivered_through_seq=latest_seq,
                inbox_snapshot_seq=latest_seq,
                inbox_snapshot_source="check",
                inbox_snapshot_at_ms=now_ms,
                last_check_at_ms=now_ms,
                updated_at_ms=now_ms,
            )
            await transaction.save_consumer_cursor(cursor)
            result = MessageCheckResult(
                messages=messages,
                snapshot_seq=latest_seq,
                delivered_through_seq=latest_seq,
                referenced_messages=referenced_messages,
            )
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
        if not target:
            raise ValueError("target must be a non-empty string")
        if limit <= 0:
            raise ValueError("limit must be positive")
        async with self._concurrency.for_session(session_id):
            async with self._storage.transaction() as transaction:
                bcn_session = await transaction.get_bcn_session(session_id)
                if bcn_session is None:
                    raise SessionNotFoundError(f"unknown bcn session: {session_id}")
                messages = await transaction.list_inbound_messages(
                    session_id,
                    target=target,
                    around_message_id=around_message_id,
                    limit=limit,
                )
                referenced_messages = await self._referenced_messages(
                    transaction,
                    session_id=session_id,
                    messages=messages,
                )
                latest_seq = await transaction.get_latest_inbound_seq(session_id)
                cursor = await transaction.get_consumer_cursor(session_id)
                if cursor is None:
                    cursor = ConsumerCursor(session_id=session_id)
                now_ms = self._clock()
                cursor = replace(
                    cursor,
                    inbox_snapshot_seq=latest_seq,
                    inbox_snapshot_source="read",
                    inbox_snapshot_at_ms=now_ms,
                    last_read_at_ms=now_ms,
                    updated_at_ms=now_ms,
                )
                await transaction.save_consumer_cursor(cursor)
            result = MessageReadResult(
                messages=messages,
                snapshot_seq=latest_seq,
                first_seq=messages[0].seq if messages else None,
                last_seq=messages[-1].seq if messages else None,
                referenced_messages=referenced_messages,
            )
        await self._audit.append_tool(
            operation="bcc.message.read",
            status="completed",
            state=RuntimeEventState.COMPLETED,
            correlation=self._correlation(session_id=session_id),
            arguments={
                "session_id": session_id,
                "target": target,
                "around_message_id": around_message_id,
                "limit": limit,
            },
        )
        return result

    @staticmethod
    async def _referenced_messages(
        transaction: IStorageTransaction,
        *,
        session_id: str,
        messages: tuple[InboundMessage, ...],
    ) -> tuple[InboundMessage, ...]:
        message_ids = {message.message_id for message in messages}
        referenced: list[InboundMessage] = []
        referenced_ids: set[str] = set()
        for message in messages:
            reference_id = message.reply_to_message_id
            if (
                reference_id is None
                or reference_id in message_ids
                or reference_id in referenced_ids
            ):
                continue
            history = await transaction.list_inbound_messages(
                session_id,
                target=message.canonical_target,
                around_message_id=reference_id,
                limit=1,
            )
            referenced_message = history[0]
            if referenced_message.message_id != reference_id:
                raise RuntimeError(
                    "referenced inbound lookup returned a different message"
                )
            referenced.append(referenced_message)
            referenced_ids.add(reference_id)
        return tuple(referenced)

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
    ) -> OutboundMessage:
        if not command_id:
            raise ValueError("command_id must be a non-empty string")
        if not target:
            raise ValueError("target must be a non-empty string")
        attachments = (
            await asyncio.to_thread(self._attachment_resolver, attachment_paths)
            if attachment_paths
            else ()
        )
        is_empty = True
        if body.strip() or attachments:
            is_empty = False
        outbound_id = f"outbound-{session_id}-{command_id}"
        async with self._concurrency.for_session(session_id):
            async with self._storage.transaction() as transaction:
                bcn_session = await transaction.get_bcn_session(session_id)
                if bcn_session is None:
                    raise SessionNotFoundError(f"unknown bcn session: {session_id}")
                channel_session = await transaction.get_channel_session(
                    bcn_session.channel_session_id
                )
                if channel_session is None:
                    raise ValueError(
                        f"unknown channel session: {bcn_session.channel_session_id}"
                    )
                cursor = await transaction.get_consumer_cursor(session_id)
                if cursor is None:
                    cursor = ConsumerCursor(session_id=session_id)
                current_seq = await transaction.get_latest_inbound_seq(session_id)
                target_messages = await transaction.list_inbound_messages(
                    session_id,
                    target=target,
                    limit=1,
                )
                reply_to_provider_message_id = None
                if reply_to_message_id is not None:
                    reply_messages = await transaction.list_inbound_messages(
                        session_id,
                        target=target,
                        around_message_id=reply_to_message_id,
                        limit=1,
                    )
                    reply_to_provider_message_id = reply_messages[0].provider_message_id
                outbound = OutboundMessage(
                    outbound_message_id=outbound_id,
                    command_id=command_id,
                    session_id=session_id,
                    channel_session_id=channel_session.id,
                    target=target,
                    body=body,
                    attachments=attachments,
                    state=OutboundDeliveryState.DRAFT,
                    fresh_check_state=FreshCheckState.REQUIRED,
                    created_at_ms=created_at_ms,
                    reply_to_message_id=reply_to_message_id,
                )
                if not is_empty:
                    outbound = await transaction.save_outbound_message(outbound)
                    outbound_id = outbound.outbound_message_id
                rejection_event_name = "bcc.send.fresh_check.failed"
                if is_empty:
                    outbound = outbound.transition_to(
                        OutboundDeliveryState.REJECTED,
                        at_ms=self._clock(),
                        save_draft=False,
                        error_kind=ErrorKind.EMPTY_BODY.value,
                        error_message="Outbound message must not be empty.",
                        next_action="Provide a message body or attachment and retry.",
                    )
                    audit_context = self._correlation(
                        session_id=session_id,
                        channel=channel_session.channel,
                        channel_session_id=channel_session.id,
                        command_id=command_id,
                        inbound_seq=current_seq,
                        outbound_message_id=None,
                    )
                    audit_state = RuntimeEventState.FAILED
                    audit_kind = ErrorKind.EMPTY_BODY
                    rejection_event_name = "bcc.send.empty_body.failed"
                elif not target_messages:
                    outbound = outbound.transition_to(
                        OutboundDeliveryState.REJECTED,
                        at_ms=self._clock(),
                        error_kind=ErrorKind.TARGET_NOT_REPLYABLE.value,
                        error_message=(
                            f"Thread target is not found or is not replyable: {target}"
                        ),
                        next_action=(
                            "Run `bcc message read` or `bcc message check` for this "
                            "target to verify whether the message already landed; "
                            "retry only after stable verification."
                        ),
                    )
                    await transaction.save_outbound_message(outbound)
                    audit_context = self._correlation(
                        session_id=session_id,
                        channel=channel_session.channel,
                        channel_session_id=channel_session.id,
                        command_id=command_id,
                        inbound_seq=current_seq,
                        outbound_message_id=outbound_id,
                    )
                    audit_state = RuntimeEventState.FAILED
                    audit_kind = ErrorKind.TARGET_NOT_REPLYABLE
                    rejection_event_name = "bcc.send.target.failed"
                elif cursor.inbox_snapshot_seq is None:
                    outbound = outbound.record_fresh_check(
                        FreshCheckState.FAILED,
                        snapshot_seq=None,
                        current_inbound_seq=current_seq,
                    )
                    outbound = outbound.transition_to(
                        OutboundDeliveryState.REJECTED,
                        at_ms=self._clock(),
                        error_kind=ErrorKind.FRESH_CHECK_REQUIRED.value,
                        error_message=(
                            "No inbox snapshot is available; outbound send was refused."
                        ),
                        next_action=(
                            "Run `bcc message check` or `bcc message read` before "
                            "retrying."
                        ),
                    )
                    await transaction.save_outbound_message(outbound)
                    audit_context = self._correlation(
                        session_id=session_id,
                        channel=channel_session.channel,
                        channel_session_id=channel_session.id,
                        command_id=command_id,
                        inbound_seq=current_seq,
                        outbound_message_id=outbound_id,
                    )
                    audit_state = RuntimeEventState.FAILED
                    audit_kind = ErrorKind.FRESH_CHECK_REQUIRED
                elif current_seq > cursor.inbox_snapshot_seq:
                    outbound = outbound.record_fresh_check(
                        FreshCheckState.FAILED,
                        snapshot_seq=cursor.inbox_snapshot_seq,
                        current_inbound_seq=current_seq,
                    )
                    outbound = outbound.transition_to(
                        OutboundDeliveryState.REJECTED,
                        at_ms=self._clock(),
                        error_kind=ErrorKind.FRESH_CHECK_FAILED.value,
                        error_message=(
                            "New inbound message(s) arrived after the latest inbox "
                            "snapshot; outbound send was refused."
                        ),
                        next_action=(
                            "Run `bcc message check` to read the new messages, then "
                            "retry `bcc message send` if still appropriate."
                        ),
                    )
                    await transaction.save_outbound_message(outbound)
                    audit_context = self._correlation(
                        session_id=session_id,
                        channel=channel_session.channel,
                        channel_session_id=channel_session.id,
                        command_id=command_id,
                        inbound_seq=current_seq,
                        outbound_message_id=outbound_id,
                    )
                    audit_state = RuntimeEventState.FAILED
                    audit_kind = ErrorKind.FRESH_CHECK_FAILED
                else:
                    audit_context = self._correlation(
                        session_id=session_id,
                        channel=channel_session.channel,
                        channel_session_id=channel_session.id,
                        command_id=command_id,
                        inbound_seq=current_seq,
                        outbound_message_id=outbound_id,
                    )
                    audit_state = RuntimeEventState.STARTED
                    audit_kind = None

            if outbound.state is OutboundDeliveryState.REJECTED:
                await self._audit.append(
                    event_name=rejection_event_name,
                    state=audit_state,
                    correlation=audit_context,
                    error_kind=audit_kind,
                    error_message=outbound.error_message,
                )
                await self._audit.append_tool(
                    operation="bcc.message.send",
                    status="rejected",
                    state=audit_state,
                    correlation=audit_context,
                    arguments={
                        "command_id": command_id,
                        "target": target,
                        "reason": outbound.error_kind,
                    },
                    error_kind=audit_kind,
                    error_message=outbound.error_message,
                )
                return outbound

            if outbound.attachments:
                try:
                    await asyncio.to_thread(
                        self._attachment_resolver.verify, outbound.attachments
                    )
                except ValueError as error:
                    outbound = outbound.transition_to(
                        OutboundDeliveryState.REJECTED,
                        at_ms=self._clock(),
                        error_kind=ErrorKind.VALIDATION.value,
                        error_message=str(error),
                        next_action=(
                            "Inspect the attachment files and submit a new message."
                        ),
                    )
                    async with self._storage.transaction() as transaction:
                        await transaction.save_outbound_message(outbound)
                    await self._audit.append(
                        event_name="bcc.send.attachment_preflight.failed",
                        state=RuntimeEventState.FAILED,
                        correlation=audit_context,
                        error_kind=ErrorKind.VALIDATION,
                        error_message=outbound.error_message,
                    )
                    await self._audit.append_tool(
                        operation="bcc.message.send",
                        status="rejected",
                        state=RuntimeEventState.FAILED,
                        correlation=audit_context,
                        arguments={
                            "command_id": command_id,
                            "target": target,
                            "reason": outbound.error_kind,
                        },
                        error_kind=ErrorKind.VALIDATION,
                        error_message=outbound.error_message,
                    )
                    return outbound

            if cursor.inbox_snapshot_seq is None:
                raise AssertionError("accepted send requires an inbox snapshot")
            outbound = outbound.record_fresh_check(
                FreshCheckState.PASSED,
                snapshot_seq=cursor.inbox_snapshot_seq,
                current_inbound_seq=current_seq,
            )
            outbound = outbound.transition_to(
                OutboundDeliveryState.PENDING,
                at_ms=self._clock(),
            )
            outbound = replace(
                outbound,
                provider_attempted_at_ms=self._clock(),
            )
            async with self._storage.transaction() as transaction:
                await transaction.save_outbound_message(outbound)

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
            try:
                provider_result = await self._channel.send(
                    ChannelSendRequest(
                        outbound=outbound,
                        target_kind=channel_session.target_kind,
                        provider_thread_id=channel_session.provider_thread_id,
                        provider_reply_to_message_id=(reply_to_provider_message_id),
                    ),
                    timeout=self._provider_call_timeout,
                )
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001
                provider_result = None
                provider_error = error
            else:
                provider_error = None

            attempted_at_ms = outbound.provider_attempted_at_ms or self._clock()
            outbound = replace(outbound, provider_attempted_at_ms=attempted_at_ms)
            if provider_result is None:
                outbound = outbound.transition_to(
                    OutboundDeliveryState.UNKNOWN,
                    at_ms=self._clock(),
                    error_kind=ErrorKind.PROVIDER_UNKNOWN.value,
                    error_message=str(provider_error),
                    next_action="reconcile channel delivery before retrying",
                )
                terminal_kind = ErrorKind.PROVIDER_UNKNOWN
                terminal_state = RuntimeEventState.UNKNOWN
            elif provider_result.status is ProviderCallStatus.CONFIRMED:
                receipt = provider_result.value
                if receipt is None:
                    raise ValueError("confirmed channel delivery has no receipt")
                outbound = outbound.transition_to(
                    OutboundDeliveryState.SENT,
                    at_ms=self._clock(),
                    provider_message_id=receipt.provider_message_id,
                    provider_receipt_ref=receipt.provider_receipt_ref,
                )
                terminal_kind = None
                terminal_state = RuntimeEventState.COMPLETED
            elif provider_result.status is ProviderCallStatus.QUEUED:
                receipt = provider_result.value
                if receipt is None:
                    raise ValueError("queued channel delivery has no receipt")
                outbound = outbound.transition_to(
                    OutboundDeliveryState.QUEUED,
                    at_ms=self._clock(),
                    provider_message_id=receipt.provider_message_id,
                    provider_receipt_ref=receipt.provider_receipt_ref,
                )
                terminal_kind = None
                terminal_state = RuntimeEventState.STARTED
            elif provider_result.status is ProviderCallStatus.PARTIAL:
                receipt = provider_result.value
                if receipt is None:
                    raise ValueError("partial channel delivery has no receipt")
                outbound = outbound.transition_to(
                    OutboundDeliveryState.PARTIAL,
                    at_ms=self._clock(),
                    provider_message_id=receipt.provider_message_id,
                    provider_receipt_ref=receipt.provider_receipt_ref,
                    error_kind=provider_result.error_kind
                    or ErrorKind.PROVIDER_PARTIAL.value,
                    error_message=provider_result.error_message,
                    next_action="do not retry the complete message automatically",
                )
                terminal_kind = ErrorKind.PROVIDER_PARTIAL
                terminal_state = RuntimeEventState.FAILED
            elif provider_result.status is ProviderCallStatus.FAILED:
                provider_receipt_ref = provider_result.receipt.get(
                    "provider_receipt_ref"
                )
                outbound = outbound.transition_to(
                    OutboundDeliveryState.FAILED,
                    at_ms=self._clock(),
                    provider_receipt_ref=(
                        provider_receipt_ref
                        if isinstance(provider_receipt_ref, str)
                        and provider_receipt_ref
                        else None
                    ),
                    error_kind=provider_result.error_kind
                    or ErrorKind.PROVIDER_FAILED.value,
                    error_message=provider_result.error_message,
                )
                terminal_kind = ErrorKind.PROVIDER_FAILED
                terminal_state = RuntimeEventState.FAILED
            else:
                provider_receipt_ref = provider_result.receipt.get(
                    "provider_receipt_ref"
                )
                outbound = outbound.transition_to(
                    OutboundDeliveryState.UNKNOWN,
                    at_ms=self._clock(),
                    provider_receipt_ref=(
                        provider_receipt_ref
                        if isinstance(provider_receipt_ref, str)
                        and provider_receipt_ref
                        else None
                    ),
                    error_kind=provider_result.error_kind
                    or ErrorKind.PROVIDER_UNKNOWN.value,
                    error_message=provider_result.error_message,
                    next_action="reconcile channel delivery before retrying",
                )
                terminal_kind = ErrorKind.PROVIDER_UNKNOWN
                terminal_state = RuntimeEventState.UNKNOWN

            if provider_result is not None and provider_result.receipt:
                outbound = replace(
                    outbound,
                    metadata={
                        **outbound.metadata,
                        "delivery_receipt": dict(provider_result.receipt),
                    },
                )

            async with self._storage.transaction() as transaction:
                await transaction.save_outbound_message(outbound)
            await self._audit.append(
                event_name=f"channel.outbound.{outbound.state.value}",
                state=terminal_state,
                correlation=audit_context,
                error_kind=terminal_kind,
                error_message=outbound.error_message,
                metadata=provider_result.receipt if provider_result is not None else {},
            )
            await self._audit.append_tool(
                operation="bcc.message.send",
                status=outbound.state.value,
                state=terminal_state,
                correlation=audit_context,
                arguments={
                    "command_id": command_id,
                    "target": target,
                    "delivery_state": outbound.state.value,
                },
                error_kind=terminal_kind,
                error_message=outbound.error_message,
            )
            return outbound

    async def unfollow(self, session_id: str, *, target: str) -> bool:
        if not target:
            raise ValueError("target must be a non-empty string")
        async with (
            self._concurrency.for_session(session_id),
            self._storage.transaction() as transaction,
        ):
            bcn_session = await transaction.get_bcn_session(session_id)
            if bcn_session is None:
                raise SessionNotFoundError(f"unknown bcn session: {session_id}")
            channel_session = await transaction.get_channel_session(
                bcn_session.channel_session_id
            )
            if channel_session is None:
                raise ValueError(
                    f"unknown channel session: {bcn_session.channel_session_id}"
                )
            target_messages = await transaction.list_inbound_messages(
                session_id,
                target=target,
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
                await transaction.save_channel_session(channel_session)
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
