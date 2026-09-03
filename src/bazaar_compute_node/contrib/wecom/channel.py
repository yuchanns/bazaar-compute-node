from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import math
import random
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from email.message import Message as EmailMessage
from time import time_ns
from urllib.parse import unquote
from uuid import NAMESPACE_URL, uuid4, uuid5

import aiohttp
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from ...core.activity import ActivityOverview, ActivityReducer
from ...core.approval import approval_action_text, approval_description_text
from ...core.channel import (
    ChannelApprovalRequest,
    ChannelContext,
    ChannelDeliveryReceipt,
    ChannelIdentity,
    ChannelSendRequest,
    IChannel,
)
from ...core.models import (
    ApprovalDecision,
    ApprovalResult,
    ChannelTargetKind,
    InboundAttachment,
    Message,
    MessageDirection,
    OutboundAttachment,
    RuntimeOutputEvent,
    SenderIdentity,
    SenderKind,
)
from ...core.outcomes import ProviderCallResult, ProviderCallStatus
from ...core.timerwheel import TimerWheel
from ...core.utils.clock import remaining
from ...core.utils.markdown import split_markdown, utf8_bytes
from ...i18n import ENGLISH, Translator, create_translator
from .outbound import (
    AttachmentReader,
    PreparedAttachment,
    UploadResult,
    encode_request,
    prepare_attachments,
    visible_message_body,
)

_STOP = object()
_MAX_MEDIA_BYTES = 25 * 1024 * 1024
_MAX_MARKDOWN_BYTES = 20_480
_CARD_UPDATE_TIMEOUT_SECONDS = 5.0
_ACTIVITY_CARD_TIMEOUT_SECONDS = 10.0
_APPROVE_KEY = "bcn_approve"
_REJECT_KEY = "bcn_reject"
_RESOLVED_KEY = "bcn_resolved"


@dataclass(frozen=True, slots=True)
class _RequestResult:
    status: ProviderCallStatus
    receipt: Mapping[str, object]
    body: Mapping[str, object] | None = None
    error_kind: str | None = None
    error_message: str | None = None


@dataclass(slots=True)
class _Delivery:
    """What a multi-part WeCom send has confirmed so far."""

    total: int
    confirmed: int = 0
    receipts: list[dict[str, object]] = field(default_factory=list)
    uploads: list[dict[str, object]] = field(default_factory=list)

    def receipt(self) -> Mapping[str, object]:
        return {
            "total_parts": self.total,
            "confirmed_parts": self.confirmed,
            "parts": tuple(self.receipts),
            "uploads": tuple(self.uploads),
            "provider_receipt_ref": (
                self.receipts[-1]["provider_request_id"] if self.receipts else None
            ),
        }


def _request_receipt(
    request_id: str,
    state: str,
    *,
    visible: bool,
    attempted_at_ms: int,
    **rest: object,
) -> dict[str, object]:
    return {
        "provider_request_id": request_id,
        "state": state,
        "visible": visible,
        "attempted_at_ms": attempted_at_ms,
        **rest,
    }


def _upload_failure(
    receipts: list[dict[str, object]], error_kind: str, error_message: str | None
) -> UploadResult:
    return UploadResult(
        media_id=None,
        receipts=tuple(receipts),
        error_kind=error_kind,
        error_message=error_message,
    )


@dataclass(frozen=True, slots=True)
class _InboundRoute:
    provider_message_id: str
    sender_id: str
    message_type: str
    conversation: str
    target_kind: ChannelTargetKind
    mentions_agent: bool
    target_prefix: str


@dataclass(frozen=True, slots=True)
class _InboundContent:
    body: str
    attachments: tuple[InboundAttachment, ...]
    fingerprint: str


@dataclass(slots=True)
class _PendingApproval:
    request: ChannelApprovalRequest
    task_id: str
    future: asyncio.Future[ApprovalResult]


class WeComChannel(IChannel):
    def __init__(
        self,
        context: ChannelContext,
        *,
        bot_id: str,
        secret: str,
        websocket_url: str,
    ) -> None:
        self._context = context
        self._activity_reducers: dict[tuple[str, str], ActivityReducer] = {}
        self._activity_routes: dict[str, tuple[str, ChannelTargetKind]] = {}
        self._activity_tasks: set[asyncio.Task[None]] = set()
        self._activity_cards_sent = 0
        self._activity_failures = 0
        self._bot_id = bot_id
        self._secret = secret
        self._websocket_url = websocket_url
        self._timer_wheel: TimerWheel | None = context.timer_wheel
        self._inbound: asyncio.Queue[Message | object] = asyncio.Queue()
        self._ready = asyncio.Event()
        self._stopping = asyncio.Event()
        self._startup_finished = asyncio.Event()
        self._startup_error: Exception | None = None
        self._runner: asyncio.Task[None] | None = None
        self._connection: aiohttp.ClientWebSocketResponse | None = None
        self._send_lock = asyncio.Lock()
        self._pending_acks: dict[str, asyncio.Future[Mapping[str, object]]] = {}
        self._pending_approvals: dict[str, _PendingApproval] = {}
        self._approval_task_ids_by_request: dict[str, str] = {}
        self._approval_card_update_tasks: set[asyncio.Task[None]] = set()
        self._translator: Translator = context.translator or create_translator(ENGLISH)
        self._degraded = False
        self._heartbeat_ack = ""
        self._state = "stopped"
        self._network_attempts = 0
        self._auth_attempts = 0
        self._last_disconnect_kind: str | None = None
        self._connected_at_ms: int | None = None
        self._last_frame_at_ms: int | None = None
        self._connection_generation = 0
        self._ignored_event_frames = 0
        self._message_frames_received = 0
        self._message_frames_queued = 0
        self._message_frames_filtered = 0
        self._last_message_frame_at_ms: int | None = None
        self._last_message_disposition: str | None = None
        self._last_message_filter_reason: str | None = None
        self._last_event_type: str | None = None
        self._approval_card_update_attempts = 0
        self._approval_card_update_unknown = 0
        self._approval_card_update_failures = 0
        self._last_approval_card_update_disposition: str | None = None
        self._logger = logging.getLogger("bazaar_compute_node.channel.wecom")
        if not self._logger.handlers:
            self._logger.addHandler(logging.StreamHandler())
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False

    @property
    def name(self) -> str:
        return "wecom"

    @property
    def health(self) -> Mapping[str, object]:
        return {
            "state": self._state,
            "full_group_ingress": False,
            "network_attempts": self._network_attempts,
            "auth_attempts": self._auth_attempts,
            "connection_generation": self._connection_generation,
            "connected_at_ms": self._connected_at_ms,
            "last_frame_at_ms": self._last_frame_at_ms,
            "last_disconnect_kind": self._last_disconnect_kind,
            "ignored_event_frames": self._ignored_event_frames,
            "message_frames_received": self._message_frames_received,
            "message_frames_queued": self._message_frames_queued,
            "message_frames_filtered": self._message_frames_filtered,
            "last_message_frame_at_ms": self._last_message_frame_at_ms,
            "last_message_disposition": self._last_message_disposition,
            "last_message_filter_reason": self._last_message_filter_reason,
            "last_event_type": self._last_event_type,
            "approval_card_updates_pending": len(self._approval_card_update_tasks),
            "approval_card_update_attempts": self._approval_card_update_attempts,
            "approval_card_update_unknown": self._approval_card_update_unknown,
            "approval_card_update_failures": self._approval_card_update_failures,
            "activity_cards_sent": self._activity_cards_sent,
            "activity_failures": self._activity_failures,
            "activity_tasks_pending": len(self._activity_tasks),
            "last_approval_card_update_disposition": (
                self._last_approval_card_update_disposition
            ),
        }

    def get_identity(self) -> ChannelIdentity | None:
        return ChannelIdentity(id=self._bot_id)

    async def start(self, *, timeout: float) -> None:
        if self._runner is not None:
            return
        self._stopping.clear()
        self._startup_finished.clear()
        self._startup_error = None
        self._state = "connecting"
        self._runner = asyncio.create_task(self._run(), name="bcn-wecom-channel")
        try:
            await asyncio.wait_for(self._startup_finished.wait(), timeout=timeout)
        except BaseException:
            self._stopping.set()
            self._runner.cancel()
            await asyncio.gather(self._runner, return_exceptions=True)
            self._runner = None
            self._state = "stopped"
            raise
        if self._startup_error is not None:
            raise self._startup_error

    async def stop(self, *, timeout: float) -> None:
        self._stopping.set()
        decided_at_ms = time_ns() // 1_000_000
        for pending in tuple(self._pending_approvals.values()):
            if not pending.future.done():
                pending.future.set_result(
                    ApprovalResult(
                        request_id=pending.request.approval.request_id,
                        decision=ApprovalDecision.REJECTED,
                        decided_at_ms=decided_at_ms,
                        reason="channel_stopped",
                    )
                )
        self._pending_approvals.clear()
        self._approval_task_ids_by_request.clear()
        await self._cancel_approval_card_updates()
        await self._cancel_activity_cards()
        try:
            await asyncio.wait_for(self._send_lock.acquire(), timeout=timeout)
        except TimeoutError:
            pass
        else:
            self._send_lock.release()
        connection = self._connection
        if connection is not None:
            await connection.close()
        runner = self._runner
        if runner is not None:
            try:
                await asyncio.wait_for(runner, timeout=timeout)
            except TimeoutError:
                runner.cancel()
                await asyncio.gather(runner, return_exceptions=True)
        self._runner = None
        self._state = "stopped"
        await self._inbound.put(_STOP)

    async def receive(self) -> AsyncIterator[Message]:
        while True:
            item = await self._inbound.get()
            if item is _STOP:
                return
            if not isinstance(item, Message):
                raise TypeError("WeCom inbound queue contained an invalid message")
            yield item

    def accept_turn_event(
        self,
        item: RuntimeOutputEvent,
        *,
        session_id: str,
    ) -> None:
        key = (session_id, item.envelope.turn_id)
        reducer = self._activity_reducers.get(key)
        if reducer is None:
            reducer = ActivityReducer()
            self._activity_reducers[key] = reducer
        reducer.apply(item.payload)
        overview = reducer.overview
        if overview is None:
            return
        self._activity_reducers.pop(key, None)
        route = self._activity_routes.get(session_id)
        if route is None:
            self._observe(
                "wecom.activity.card_skipped",
                reason="unknown_session_route",
                session_id=session_id,
            )
            return
        if overview.empty:
            self._observe(
                "wecom.activity.card_skipped",
                reason="empty_overview",
                session_id=session_id,
            )
            return
        task = asyncio.create_task(
            self._send_activity_card(route, overview),
            name=f"bcn-wecom-activity-{item.envelope.turn_id}",
        )
        self._activity_tasks.add(task)
        task.add_done_callback(self._activity_tasks.discard)

    def _activity_markdown(self, overview: ActivityOverview) -> str:
        translator = self._translator
        lines = [
            translator.text(
                "activity.wecom.title",
                {
                    "title": translator.text("activity.title"),
                    "state": translator.text(
                        f"activity.state.{overview.outcome.value}"
                    ),
                },
            )
        ]
        if overview.error_message:
            lines.append("")
            lines.append(
                translator.text("activity.error", {"error": overview.error_message})
            )
        lines.extend(("", "---", "", "|  |  |", "| --- | --- |"))
        counts = (
            ("activity.label.tool_calls", overview.tool_calls),
            ("activity.label.context_compactions", overview.context_compactions),
        )
        for key, count in counts:
            if not count:
                continue
            value = translator.text("activity.value.count", {"count": count})
            lines.append(f"| {translator.text(key)} | {value} |")
        tokens = (
            ("activity.label.input", overview.input_tokens),
            ("activity.label.cached", overview.cached_input_tokens),
            ("activity.label.output", overview.output_tokens),
        )
        for key, value in tokens:
            if not value:
                continue
            lines.append(f"| {translator.text(key)} | {value} |")
        if overview.input_tokens or overview.cached_input_tokens:
            note = translator.text("activity.note.tokens")
            lines.extend(("", "---", "", f"*{note}*"))
        return "\n".join(lines)

    async def _send_activity_card(
        self,
        route: tuple[str, ChannelTargetKind],
        overview: ActivityOverview,
    ) -> None:
        connection = self._connection
        if connection is None:
            self._observe("wecom.activity.card_skipped", reason="no_connection")
            return
        target_id, target_kind = route
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _ACTIVITY_CARD_TIMEOUT_SECONDS
        try:
            await asyncio.wait_for(
                self._send_lock.acquire(),
                timeout=_ACTIVITY_CARD_TIMEOUT_SECONDS,
            )
            try:
                result = await self._request(
                    connection,
                    command="aibot_send_msg",
                    body=visible_message_body(
                        target_id=target_id,
                        target_kind=target_kind,
                        message_type="markdown",
                        content={"content": self._activity_markdown(overview)},
                    ),
                    deadline=deadline,
                    rejection_kind="provider_rejected_activity",
                    rejection_message="WeCom rejected the activity card",
                    unknown_kind="activity_outcome_unknown",
                    unknown_message="WeCom activity card outcome is unknown",
                )
            finally:
                self._send_lock.release()
            if result.status is not ProviderCallStatus.CONFIRMED:
                self._activity_failures += 1
                self._observe(
                    "wecom.activity.card_sent",
                    outcome=result.status.value,
                )
                return
            self._activity_cards_sent += 1
            self._observe("wecom.activity.card_sent", outcome="confirmed")
        except asyncio.CancelledError:
            raise
        except Exception:
            self._activity_failures += 1
            self._observe("wecom.activity.card_sent", outcome="failed")
            self._logger.exception("WeCom activity card delivery failed")

    async def _stage_attachments(
        self,
        delivery: _Delivery,
        attachments: tuple[OutboundAttachment, ...],
        *,
        deadline: float,
    ) -> tuple[PreparedAttachment, ...] | ProviderCallResult[ChannelDeliveryReceipt]:
        """Stage the attachments on disk before any part goes out."""

        try:
            return await asyncio.wait_for(
                asyncio.to_thread(
                    prepare_attachments, self._context.workspace(), attachments
                ),
                timeout=remaining(deadline),
            )
        except TimeoutError:
            return self._clear_failure(
                delivery,
                error_kind="delivery_timeout",
                error_message="WeCom attachment preflight timed out",
            )
        except (OSError, ValueError) as error:
            return self._clear_failure(
                delivery,
                error_kind="invalid_attachment",
                error_message=str(error),
            )

    def _ready_connection(
        self,
        delivery: _Delivery,
        *,
        deadline: float,
        timeout_message: str,
        unavailable_message: str,
    ) -> aiohttp.ClientWebSocketResponse | ProviderCallResult[ChannelDeliveryReceipt]:
        """Confirm there is still time and a live socket before sending a part."""

        if remaining(deadline) <= 0:
            return self._clear_failure(
                delivery,
                error_kind="delivery_timeout",
                error_message=timeout_message,
            )
        connection = self._connection
        if connection is None or connection.closed or not self._ready.is_set():
            return self._clear_failure(
                delivery,
                error_kind="connection_unavailable",
                error_message=unavailable_message,
            )
        return connection

    async def _send_batches(
        self,
        delivery: _Delivery,
        batches: tuple[str, ...],
        *,
        target_id: str,
        target_kind: ChannelTargetKind,
        deadline: float,
    ) -> ProviderCallResult[ChannelDeliveryReceipt] | None:
        """Send the message text, one markdown batch at a time."""

        for batch in batches:
            connection = self._ready_connection(
                delivery,
                deadline=deadline,
                timeout_message=(
                    "WeCom delivery timed out between batches"
                    if delivery.confirmed
                    else "WeCom delivery timed out before sending"
                ),
                unavailable_message=(
                    "WeCom connection became unavailable between batches"
                    if delivery.confirmed
                    else "WeCom connection is unavailable"
                ),
            )
            if isinstance(connection, ProviderCallResult):
                return connection
            result = await self._send_batch(
                connection,
                target_id=target_id,
                target_kind=target_kind,
                content=batch,
                deadline=deadline,
            )
            receipt = dict(result.receipt)
            receipt.update(
                {"part_type": "markdown", "ordinal": len(delivery.receipts) + 1}
            )
            delivery.receipts.append(receipt)
            if result.status is ProviderCallStatus.CONFIRMED:
                delivery.confirmed += 1
                continue
            if result.status is ProviderCallStatus.FAILED:
                return self._clear_failure(
                    delivery,
                    error_kind=result.error_kind or "provider_rejected_batch",
                    error_message=result.error_message
                    or "WeCom rejected the outbound message",
                )
            return ProviderCallResult(
                status=ProviderCallStatus.UNKNOWN,
                error_kind=result.error_kind or "ack_unknown",
                error_message=result.error_message
                or "WeCom acknowledgement outcome is unknown",
                receipt=delivery.receipt(),
            )
        return None

    async def _send_media_parts(
        self,
        delivery: _Delivery,
        attachments: tuple[PreparedAttachment, ...],
        *,
        target_id: str,
        target_kind: ChannelTargetKind,
        deadline: float,
    ) -> ProviderCallResult[ChannelDeliveryReceipt] | None:
        """Upload each attachment and send the message that carries it."""

        for attachment_ordinal, attachment in enumerate(attachments, start=1):
            connection = self._ready_connection(
                delivery,
                deadline=deadline,
                timeout_message="WeCom delivery timed out between parts",
                unavailable_message=(
                    "WeCom connection became unavailable between parts"
                ),
            )
            if isinstance(connection, ProviderCallResult):
                return connection
            upload = await self._upload_attachment(
                connection,
                attachment=attachment,
                attachment_ordinal=attachment_ordinal,
                deadline=deadline,
            )
            delivery.uploads.extend(dict(item) for item in upload.receipts)
            if upload.error_kind is not None:
                return self._clear_failure(
                    delivery,
                    error_kind=upload.error_kind,
                    error_message=upload.error_message
                    or "WeCom attachment upload failed",
                )
            if upload.media_id is None:
                raise AssertionError("successful WeCom upload requires media_id")
            result = await self._send_media(
                connection,
                target_id=target_id,
                target_kind=target_kind,
                attachment=attachment,
                media_id=upload.media_id,
                deadline=deadline,
            )
            receipt = dict(result.receipt)
            receipt.update(
                {
                    "part_type": attachment.media_type,
                    "ordinal": len(delivery.receipts) + 1,
                    "attachment_name": attachment.descriptor.name,
                }
            )
            delivery.receipts.append(receipt)
            if result.status is ProviderCallStatus.CONFIRMED:
                delivery.confirmed += 1
                continue
            if result.status is ProviderCallStatus.FAILED:
                return self._clear_failure(
                    delivery,
                    error_kind=result.error_kind or "provider_rejected_part",
                    error_message=result.error_message
                    or "WeCom rejected an outbound attachment",
                )
            return ProviderCallResult(
                status=ProviderCallStatus.UNKNOWN,
                error_kind=result.error_kind or "ack_unknown",
                error_message=result.error_message
                or "WeCom acknowledgement outcome is unknown",
                receipt=delivery.receipt(),
            )
        return None

    async def send(
        self, request: ChannelSendRequest, *, timeout: float
    ) -> ProviderCallResult[ChannelDeliveryReceipt]:
        target_id = request.provider_thread_id
        try:
            batches = (
                split_markdown(
                    request.body, limit=_MAX_MARKDOWN_BYTES, measure=utf8_bytes
                )
                if request.body.strip()
                else ()
            )
        except ValueError as error:
            return ProviderCallResult(
                status=ProviderCallStatus.FAILED,
                error_kind="invalid_markdown",
                error_message=str(error),
            )

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        total_parts = len(batches) + len(request.attachments)
        if total_parts == 0:
            return ProviderCallResult(
                status=ProviderCallStatus.FAILED,
                error_kind="empty_message",
                error_message="WeCom outbound message must not be empty",
            )
        delivery = _Delivery(total=total_parts)
        try:
            await asyncio.wait_for(
                self._send_lock.acquire(), timeout=remaining(deadline)
            )
        except TimeoutError:
            return self._clear_failure(
                delivery,
                error_kind="delivery_timeout",
                error_message="WeCom delivery timed out waiting for the send lock",
            )
        try:
            attachments = await self._stage_attachments(
                delivery, request.attachments, deadline=deadline
            )
            if isinstance(attachments, ProviderCallResult):
                return attachments
            failure = await self._send_batches(
                delivery,
                batches,
                target_id=target_id,
                target_kind=request.target_kind,
                deadline=deadline,
            )
            if failure is None:
                failure = await self._send_media_parts(
                    delivery,
                    attachments,
                    target_id=target_id,
                    target_kind=request.target_kind,
                    deadline=deadline,
                )
            if failure is not None:
                return failure
        finally:
            self._send_lock.release()

        return ProviderCallResult(
            status=ProviderCallStatus.CONFIRMED,
            value=ChannelDeliveryReceipt(
                provider_receipt_ref=str(delivery.receipts[-1]["provider_request_id"])
            ),
            receipt=delivery.receipt(),
        )

    async def _send_batch(
        self,
        connection: aiohttp.ClientWebSocketResponse,
        *,
        target_id: str,
        target_kind: ChannelTargetKind,
        content: str,
        deadline: float,
    ) -> _RequestResult:
        return await self._request(
            connection,
            command="aibot_send_msg",
            body=visible_message_body(
                target_id=target_id,
                target_kind=target_kind,
                message_type="markdown",
                content={"content": content},
            ),
            deadline=deadline,
            rejection_kind="provider_rejected_part",
            rejection_message="WeCom rejected an outbound markdown part",
            unknown_kind="send_unknown",
            unknown_message="WeCom markdown delivery outcome is unknown",
        )

    async def _send_media(
        self,
        connection: aiohttp.ClientWebSocketResponse,
        *,
        target_id: str,
        target_kind: ChannelTargetKind,
        attachment: PreparedAttachment,
        media_id: str,
        deadline: float,
    ) -> _RequestResult:
        return await self._request(
            connection,
            command="aibot_send_msg",
            body=visible_message_body(
                target_id=target_id,
                target_kind=target_kind,
                message_type=attachment.media_type,
                content={"media_id": media_id},
            ),
            deadline=deadline,
            rejection_kind="provider_rejected_part",
            rejection_message="WeCom rejected an outbound attachment",
            unknown_kind="send_unknown",
            unknown_message="WeCom attachment delivery outcome is unknown",
        )

    async def _upload_chunks(
        self,
        connection: aiohttp.ClientWebSocketResponse,
        receipts: list[dict[str, object]],
        *,
        attachment: PreparedAttachment,
        attachment_ordinal: int,
        upload_id: str,
        deadline: float,
    ) -> UploadResult | None:
        """Send the attachment chunk by chunk, or say which chunk went wrong."""

        try:
            reader = await asyncio.to_thread(AttachmentReader.open, attachment)
        except (OSError, ValueError) as error:
            return _upload_failure(receipts, "attachment_read_failed", str(error))
        try:
            for chunk_index in range(attachment.total_chunks):
                try:
                    chunk = await asyncio.to_thread(reader.read_chunk)
                except OSError as error:
                    return _upload_failure(
                        receipts, "attachment_read_failed", str(error)
                    )
                if not chunk:
                    return _upload_failure(
                        receipts,
                        "attachment_read_failed",
                        "WeCom attachment ended before all chunks were read",
                    )
                result = await self._upload_request(
                    connection,
                    command="aibot_upload_media_chunk",
                    body={
                        "upload_id": upload_id,
                        "chunk_index": chunk_index,
                        "base64_data": base64.b64encode(chunk).decode("ascii"),
                    },
                    deadline=deadline,
                    rejection_message="WeCom rejected an attachment upload chunk",
                )
                receipts.append(
                    {
                        **result.receipt,
                        "stage": "chunk",
                        "attachment_ordinal": attachment_ordinal,
                        "chunk_index": chunk_index,
                    }
                )
                if result.status is not ProviderCallStatus.CONFIRMED:
                    return _upload_failure(
                        receipts,
                        result.error_kind or "upload_unknown",
                        result.error_message,
                    )
        finally:
            await asyncio.to_thread(reader.close)
        return None

    async def _upload_request(
        self,
        connection: aiohttp.ClientWebSocketResponse,
        *,
        command: str,
        body: dict[str, object],
        deadline: float,
        rejection_message: str,
    ) -> _RequestResult:
        """Make one of the three upload calls, which differ only in what they ask."""

        return await self._request(
            connection,
            command=command,
            body=body,
            deadline=deadline,
            rejection_kind="provider_rejected_upload",
            rejection_message=rejection_message,
            unknown_kind="upload_unknown",
            unknown_message="WeCom attachment upload outcome is unknown",
            visible=False,
        )

    async def _upload_attachment(
        self,
        connection: aiohttp.ClientWebSocketResponse,
        *,
        attachment: PreparedAttachment,
        attachment_ordinal: int,
        deadline: float,
    ) -> UploadResult:
        receipts: list[dict[str, object]] = []
        init = await self._upload_request(
            connection,
            command="aibot_upload_media_init",
            body={
                "type": attachment.media_type,
                "filename": attachment.descriptor.name,
                "total_size": attachment.size_bytes,
                "total_chunks": attachment.total_chunks,
                "md5": attachment.md5,
            },
            deadline=deadline,
            rejection_message="WeCom rejected attachment upload initialization",
        )
        receipts.append(
            {**init.receipt, "stage": "init", "attachment_ordinal": attachment_ordinal}
        )
        if init.status is not ProviderCallStatus.CONFIRMED:
            return _upload_failure(
                receipts, init.error_kind or "upload_unknown", init.error_message
            )
        upload_id = init.body.get("upload_id") if init.body is not None else None
        if not isinstance(upload_id, str) or not upload_id:
            receipts[-1]["state"] = "invalid_ack"
            return _upload_failure(
                receipts,
                "invalid_upload_ack",
                "WeCom upload initialization omitted upload_id",
            )

        failure = await self._upload_chunks(
            connection,
            receipts,
            attachment=attachment,
            attachment_ordinal=attachment_ordinal,
            upload_id=upload_id,
            deadline=deadline,
        )
        if failure is not None:
            return failure

        finish = await self._upload_request(
            connection,
            command="aibot_upload_media_finish",
            body={"upload_id": upload_id},
            deadline=deadline,
            rejection_message="WeCom rejected attachment upload completion",
        )
        receipts.append(
            {
                **finish.receipt,
                "stage": "finish",
                "attachment_ordinal": attachment_ordinal,
            }
        )
        if finish.status is not ProviderCallStatus.CONFIRMED:
            return _upload_failure(
                receipts, finish.error_kind or "upload_unknown", finish.error_message
            )
        media_id = finish.body.get("media_id") if finish.body is not None else None
        if not isinstance(media_id, str) or not media_id:
            receipts[-1]["state"] = "invalid_ack"
            return _upload_failure(
                receipts,
                "invalid_upload_ack",
                "WeCom upload completion omitted media_id",
            )
        return UploadResult(media_id=media_id, receipts=tuple(receipts))

    async def _send_and_wait(
        self,
        connection: aiohttp.ClientWebSocketResponse,
        *,
        command: str,
        request_id: str,
        body: Mapping[str, object],
        deadline: float,
    ) -> Mapping[str, object] | str:
        """Send a request and wait for its ack, or name what went wrong."""

        loop = asyncio.get_running_loop()
        future: asyncio.Future[Mapping[str, object]] = loop.create_future()
        self._pending_acks[request_id] = future
        try:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError
            await asyncio.wait_for(
                connection.send_str(encode_request(command, request_id, body)),
                timeout=remaining,
            )
        except asyncio.CancelledError:
            self._pending_acks.pop(request_id, None)
            future.cancel()
            raise
        except Exception as error:  # noqa: BLE001
            self._pending_acks.pop(request_id, None)
            future.cancel()
            return type(error).__name__
        try:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError
            frame = await asyncio.wait_for(future, timeout=remaining)
        except asyncio.CancelledError:
            raise
        except (TimeoutError, ConnectionError) as error:
            return type(error).__name__
        finally:
            self._pending_acks.pop(request_id, None)
        return frame

    async def _request(
        self,
        connection: aiohttp.ClientWebSocketResponse,
        *,
        command: str,
        body: Mapping[str, object],
        deadline: float,
        rejection_kind: str,
        rejection_message: str,
        unknown_kind: str,
        unknown_message: str,
        visible: bool = True,
    ) -> _RequestResult:
        request_id = f"{command}-{uuid4()}"
        attempted_at_ms = time_ns() // 1_000_000
        frame = await self._send_and_wait(
            connection,
            command=command,
            request_id=request_id,
            body=body,
            deadline=deadline,
        )
        if isinstance(frame, str):
            return _RequestResult(
                status=ProviderCallStatus.UNKNOWN,
                receipt=_request_receipt(
                    request_id,
                    "unknown",
                    visible=visible,
                    attempted_at_ms=attempted_at_ms,
                    error_type=frame,
                ),
                error_kind=unknown_kind,
                error_message=unknown_message,
            )

        acknowledged_at_ms = time_ns() // 1_000_000
        error_code = frame.get("errcode")
        error_message = frame.get("errmsg")
        if not isinstance(error_code, int) or isinstance(error_code, bool):
            return _RequestResult(
                status=ProviderCallStatus.UNKNOWN,
                receipt=_request_receipt(
                    request_id,
                    "unknown",
                    visible=visible,
                    attempted_at_ms=attempted_at_ms,
                    acknowledged_at_ms=acknowledged_at_ms,
                    error_type="InvalidAcknowledgement",
                ),
                error_kind=unknown_kind,
                error_message=unknown_message,
            )
        if error_code != 0:
            return _RequestResult(
                status=ProviderCallStatus.FAILED,
                receipt=_request_receipt(
                    request_id,
                    "failed",
                    visible=visible,
                    attempted_at_ms=attempted_at_ms,
                    acknowledged_at_ms=acknowledged_at_ms,
                    error_code=error_code,
                    error_message=(
                        error_message[:256] if isinstance(error_message, str) else None
                    ),
                ),
                error_kind=rejection_kind,
                error_message=rejection_message,
            )
        response_body = frame.get("body")
        return _RequestResult(
            status=ProviderCallStatus.CONFIRMED,
            receipt=_request_receipt(
                request_id,
                "confirmed",
                visible=visible,
                attempted_at_ms=attempted_at_ms,
                acknowledged_at_ms=acknowledged_at_ms,
                error_code=0,
            ),
            body=response_body if isinstance(response_body, Mapping) else None,
        )

    def _clear_failure(
        self,
        delivery: _Delivery,
        *,
        error_kind: str,
        error_message: str,
    ) -> ProviderCallResult[ChannelDeliveryReceipt]:
        receipt = delivery.receipt()
        if delivery.confirmed:
            return ProviderCallResult(
                status=ProviderCallStatus.PARTIAL,
                value=ChannelDeliveryReceipt(
                    provider_receipt_ref=str(
                        delivery.receipts[-1]["provider_request_id"]
                    )
                ),
                error_kind=error_kind,
                error_message=error_message,
                receipt=receipt,
            )
        return ProviderCallResult(
            status=ProviderCallStatus.FAILED,
            error_kind=error_kind,
            error_message=error_message,
            receipt=receipt,
        )

    async def request_approval(
        self, request: ChannelApprovalRequest, *, timeout: float
    ) -> ApprovalResult:
        request_id = request.approval.request_id
        if timeout <= 0:
            raise TimeoutError("WeCom approval card delivery timed out")
        if request_id in self._approval_task_ids_by_request:
            raise ValueError("WeCom approval request is already pending")
        connection = self._connection
        if connection is None or connection.closed or not self._ready.is_set():
            raise RuntimeError("WeCom channel is not ready for approvals")

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        pending = _PendingApproval(
            request=request,
            task_id=uuid4().hex,
            future=loop.create_future(),
        )
        self._pending_approvals[pending.task_id] = pending
        self._approval_task_ids_by_request[request_id] = pending.task_id
        try:
            try:
                await asyncio.wait_for(
                    self._send_lock.acquire(),
                    timeout=remaining(deadline),
                )
            except TimeoutError as error:
                raise TimeoutError(
                    "WeCom approval card delivery timed out waiting for the send lock"
                ) from error
            try:
                result = await self._request(
                    connection,
                    command="aibot_send_msg",
                    body=visible_message_body(
                        target_id=request.provider_thread_id,
                        target_kind=request.target_kind,
                        message_type="template_card",
                        content=self._approval_card(pending),
                    ),
                    deadline=deadline,
                    rejection_kind="provider_rejected_approval",
                    rejection_message="WeCom rejected the approval card",
                    unknown_kind="approval_outcome_unknown",
                    unknown_message="WeCom approval card outcome is unknown",
                )
            finally:
                self._send_lock.release()
            if result.status is not ProviderCallStatus.CONFIRMED:
                raise RuntimeError(
                    result.error_message or "WeCom approval card delivery failed"
                )
            return await pending.future
        finally:
            self._pending_approvals.pop(pending.task_id, None)
            if self._approval_task_ids_by_request.get(request_id) == pending.task_id:
                self._approval_task_ids_by_request.pop(request_id, None)

    async def _run(self) -> None:
        network_attempt = 0
        auth_attempt = 0
        while not self._stopping.is_set() and not self._degraded:
            try:
                await self._connect_once()
                network_attempt = 0
                auth_attempt = 0
            except asyncio.CancelledError:
                raise
            except _AuthenticationError as error:
                auth_attempt += 1
                self._auth_attempts = auth_attempt
                self._state = "reconnecting"
                self._last_disconnect_kind = "authentication_failed"
                if not self._startup_finished.is_set() or auth_attempt >= 3:
                    self._startup_error = error
                    self._startup_finished.set()
                    return
                await self._backoff(auth_attempt)
            except Exception as error:  # noqa: BLE001
                network_attempt += 1
                self._network_attempts = network_attempt
                self._state = "reconnecting"
                self._last_disconnect_kind = type(error).__name__
                if not self._startup_finished.is_set() and network_attempt >= 6:
                    self._startup_error = RuntimeError(
                        f"WeCom connection failed: {type(error).__name__}"
                    )
                    self._startup_finished.set()
                    return
                await self._backoff(network_attempt)

    async def _authenticate(self, connection: aiohttp.ClientWebSocketResponse) -> None:
        """Subscribe as this bot and confirm the provider accepted it."""

        subscribe_id = f"aibot_subscribe-{uuid4()}"
        await connection.send_str(
            json.dumps(
                {
                    "cmd": "aibot_subscribe",
                    "headers": {"req_id": subscribe_id},
                    "body": {"bot_id": self._bot_id, "secret": self._secret},
                },
                separators=(",", ":"),
            )
        )
        response = await asyncio.wait_for(connection.receive(), timeout=10)
        frame = self._frame(self._message_data(response))
        if self._request_id(frame) != subscribe_id or frame.get("errcode") != 0:
            raise _AuthenticationError("WeCom authentication failed")

    async def _read_frames(self, connection: aiohttp.ClientWebSocketResponse) -> None:
        """Read frames until the socket closes or the provider says it is done."""

        async for response in connection:
            if response.type in {
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSED,
            }:
                break
            if response.type is aiohttp.WSMsgType.ERROR:
                raise ConnectionError("WeCom WebSocket reader failed")
            frame = self._frame(self._message_data(response))
            self._last_frame_at_ms = time_ns() // 1_000_000
            if self._is_disconnected_event(frame):
                self._last_event_type = "disconnected_event"
                self._observe(
                    "wecom.event.received",
                    event_type="disconnected_event",
                )
                self._degraded = True
                self._state = "degraded"
                self._last_disconnect_kind = "disconnected_event"
                await connection.close()
                return
            request_id = self._request_id(frame)
            if request_id.startswith("ping-") and frame.get("errcode") == 0:
                self._heartbeat_ack = request_id
                continue
            pending = self._pending_acks.get(request_id)
            if pending is not None and not pending.done():
                pending.set_result(frame)
                continue
            if frame.get("cmd") in {
                "aibot_msg_callback",
                "aibot_event_callback",
            }:
                await self._receive_message(frame)

    async def _release_connection(self, heartbeat: asyncio.Task[None]) -> None:
        """Let go of a connection, failing whatever was still waiting on it."""

        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
        await self._cancel_approval_card_updates()
        await self._cancel_activity_cards()
        for pending in self._pending_acks.values():
            if not pending.done():
                pending.set_exception(
                    ConnectionError("WeCom connection closed before ack")
                )
        self._pending_acks.clear()
        self._connection = None
        self._ready.clear()
        if not self._stopping.is_set() and not self._degraded:
            self._state = "reconnecting"

    async def _connect_once(self) -> None:
        timeout = aiohttp.ClientTimeout(total=None, connect=10)
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.ws_connect(
                self._websocket_url,
                heartbeat=20,
                autoclose=True,
                autoping=True,
                max_msg_size=2 * 1024 * 1024,
            ) as connection,
        ):
            self._connection = connection
            await self._authenticate(connection)
            self._ready.set()
            self._state = "connected"
            self._network_attempts = 0
            self._auth_attempts = 0
            self._connected_at_ms = time_ns() // 1_000_000
            self._last_frame_at_ms = self._connected_at_ms
            self._last_disconnect_kind = None
            self._connection_generation += 1
            self._startup_finished.set()
            heartbeat = asyncio.create_task(
                self._heartbeat(connection), name="bcn-wecom-heartbeat"
            )
            try:
                await self._read_frames(connection)
                if not self._stopping.is_set() and not self._degraded:
                    raise ConnectionError("WeCom WebSocket closed")
            finally:
                await self._release_connection(heartbeat)

    async def _heartbeat(self, connection: aiohttp.ClientWebSocketResponse) -> None:
        missed = 0
        last_request = ""
        while not self._stopping.is_set():
            await self._wait_for_timer(30)
            if last_request and self._heartbeat_ack != last_request:
                missed += 1
            else:
                missed = 0
            if missed >= 2:
                await connection.close(code=1011, message=b"heartbeat timeout")
                return
            last_request = f"ping-{uuid4()}"
            await connection.send_str(
                json.dumps(
                    {"cmd": "ping", "headers": {"req_id": last_request}},
                    separators=(",", ":"),
                )
            )

    def _route(self, body: Mapping[str, object]) -> _InboundRoute | None:
        """Read where a message belongs, or record why it cannot be taken."""

        provider_message_id = body.get("msgid")
        self._observe(
            "wecom.message.received",
            provider_message_id=(
                provider_message_id
                if isinstance(provider_message_id, str) and provider_message_id
                else None
            ),
            message_type=body.get("msgtype"),
            chat_type=body.get("chattype"),
        )
        if not isinstance(provider_message_id, str) or not provider_message_id:
            self._filter_message("missing_message_id")
            return None
        sender = body.get("from")
        sender_id = sender.get("userid") if isinstance(sender, dict) else None
        if not isinstance(sender_id, str) or not sender_id:
            self._filter_message(
                "missing_sender", provider_message_id=provider_message_id
            )
            return None
        message_type = body.get("msgtype")
        if not isinstance(message_type, str) or not message_type:
            self._filter_message(
                "missing_message_type", provider_message_id=provider_message_id
            )
            return None
        chat_type = body.get("chattype")
        if chat_type == "group":
            conversation = body.get("chatid")
            target_kind = ChannelTargetKind.GROUP
            mentions_agent = True
            target_prefix = "group"
        elif chat_type == "single":
            conversation = sender_id
            target_kind = ChannelTargetKind.DM
            mentions_agent = False
            target_prefix = "dm"
        else:
            self._filter_message(
                "unsupported_chat_type", provider_message_id=provider_message_id
            )
            return None
        if not isinstance(conversation, str) or not conversation:
            self._filter_message(
                "missing_conversation", provider_message_id=provider_message_id
            )
            return None
        return _InboundRoute(
            provider_message_id=provider_message_id,
            sender_id=sender_id,
            message_type=message_type,
            conversation=conversation,
            target_kind=target_kind,
            mentions_agent=mentions_agent,
            target_prefix=target_prefix,
        )

    async def _queue_quoted_message(
        self,
        quote: object,
        *,
        provider_message_id: str,
        conversation: str,
        session_id: str,
        channel_session_id: str,
        canonical_target: str,
        target_kind: ChannelTargetKind,
        received_at_ms: int,
    ) -> str | None:
        """Queue the message being quoted, so the reply has something to point at."""

        if not isinstance(quote, dict):
            return None
        quote_type = quote.get("msgtype")
        if not isinstance(quote_type, str) or not quote_type:
            self._observe(
                "wecom.message.reference_unresolved",
                provider_message_id=provider_message_id,
                reason="missing_message_type",
            )
            return None
        quote_content = await self._content(quote, quote_type)
        reply_to_message_id = str(
            uuid5(
                NAMESPACE_URL,
                f"bcn:wecom:quoted-message:{conversation}:{quote_content.fingerprint}",
            )
        )
        await self._inbound.put(
            Message(
                direction=MessageDirection.INBOUND,
                seq=0,
                message_id=reply_to_message_id,
                session_id=session_id,
                channel_session_id=channel_session_id,
                channel=self.name,
                provider_thread_id=conversation,
                provider_message_id=quote_content.fingerprint,
                received_at_ms=received_at_ms,
                sender=None,
                message_type=quote_type,
                target=canonical_target,
                body=quote_content.body,
                target_kind=target_kind,
                mentions_agent=False,
                notifies_runtime=False,
                attachments=quote_content.attachments,
                metadata={"sender_kind": SenderKind.HUMAN.value},
            )
        )
        return reply_to_message_id

    def _update_approval_card(
        self,
        frame: Mapping[str, object],
        pending: _PendingApproval,
        decision: ApprovalDecision,
    ) -> None:
        """Redraw the card the decision came from, if the connection still stands."""

        request_id = self._request_id(frame)
        connection = self._connection
        if connection is None or connection.closed or not request_id:
            self._approval_card_update_failures += 1
            self._last_approval_card_update_disposition = "connection_unavailable"
            self._observe(
                "wecom.approval.card_update_failed",
                error_type="ConnectionError",
                outcome="transport_failed",
                task_id=pending.task_id,
            )
            return
        task = asyncio.create_task(
            self._send_approval_card_update(
                connection,
                encode_request(
                    "aibot_respond_update_msg",
                    request_id,
                    {
                        "response_type": "update_template_card",
                        "template_card": self._approval_card(
                            pending, decision=decision
                        ),
                    },
                ),
                task_id=pending.task_id,
            ),
            name=f"bcn-wecom-approval-card-update-{pending.task_id}",
        )
        self._approval_card_update_tasks.add(task)
        task.add_done_callback(self._approval_card_update_tasks.discard)

    async def _decide_approval(
        self, frame: Mapping[str, object], event: Mapping[str, object]
    ) -> bool:
        """Settle a pending approval from a card press, or say why it did not."""

        card_event = event.get("template_card_event")
        task_id = card_event.get("task_id") if isinstance(card_event, dict) else None
        event_key = (
            card_event.get("event_key") if isinstance(card_event, dict) else None
        )
        pending = (
            self._pending_approvals.get(task_id) if isinstance(task_id, str) else None
        )
        if (
            pending is not None
            and not pending.future.done()
            and event_key in {_APPROVE_KEY, _REJECT_KEY}
        ):
            decision = (
                ApprovalDecision.APPROVED
                if event_key == _APPROVE_KEY
                else ApprovalDecision.REJECTED
            )
            pending.future.set_result(
                ApprovalResult(
                    request_id=pending.request.approval.request_id,
                    decision=decision,
                    decided_at_ms=time_ns() // 1_000_000,
                )
            )
            self._update_approval_card(frame, pending, decision)
            return True
        reason = (
            "invalid_template_card_event"
            if not isinstance(card_event, dict)
            else "missing_task_id"
            if not isinstance(task_id, str) or not task_id
            else "unknown_task_id"
            if pending is None
            else "already_resolved"
            if pending.future.done()
            else "invalid_event_key"
        )
        self._observe(
            "wecom.approval.event_ignored",
            reason=reason,
            event_keys=sorted(str(key) for key in event),
            card_event_keys=(
                sorted(str(key) for key in card_event)
                if isinstance(card_event, dict)
                else None
            ),
        )
        return False

    async def _receive_event(self, frame: Mapping[str, object]) -> None:
        """Take in a bot event frame, which is only ever an approval decision."""

        body = frame.get("body")
        event = body.get("event") if isinstance(body, dict) else None
        event_type = event.get("eventtype") if isinstance(event, dict) else None
        self._last_event_type = (
            event_type if isinstance(event_type, str) and event_type else "unknown"
        )
        self._observe(
            "wecom.event.received",
            event_type=self._last_event_type,
        )
        if (
            self._last_event_type == "template_card_event"
            and isinstance(event, dict)
            and await self._decide_approval(frame, event)
        ):
            return
        self._ignored_event_frames += 1
        return

    async def _receive_message(self, frame: Mapping[str, object]) -> None:
        command = frame.get("cmd")
        body = frame.get("body")
        if command == "aibot_event_callback":
            await self._receive_event(frame)
            return
        now_ms = time_ns() // 1_000_000
        self._message_frames_received += 1
        self._last_message_frame_at_ms = now_ms
        if not isinstance(body, dict):
            self._filter_message("invalid_body")
            return
        route = self._route(body)
        if route is None:
            return
        provider_message_id = route.provider_message_id
        sender_id = route.sender_id
        message_type = route.message_type
        conversation = route.conversation
        target_kind = route.target_kind
        target_prefix = route.target_prefix
        mentions_agent = route.mentions_agent
        identity = f"wecom:{target_prefix}:{conversation}"
        channel_session_id = str(uuid5(NAMESPACE_URL, identity))
        session_id = str(uuid5(NAMESPACE_URL, f"bcn:{identity}"))
        self._activity_routes[session_id] = (conversation, target_kind)
        canonical_target = f"{target_prefix}:{channel_session_id}"
        received_at_ms = time_ns() // 1_000_000
        content = await self._content(body, message_type)
        metadata: dict[str, object] = {"sender_kind": SenderKind.HUMAN.value}
        create_time = body.get("create_time")
        if isinstance(create_time, int) and not isinstance(create_time, bool):
            metadata["provider_create_time"] = create_time
        reply_to_message_id = await self._queue_quoted_message(
            body.get("quote"),
            provider_message_id=provider_message_id,
            conversation=conversation,
            session_id=session_id,
            channel_session_id=channel_session_id,
            canonical_target=canonical_target,
            target_kind=target_kind,
            received_at_ms=received_at_ms,
        )
        await self._inbound.put(
            Message(
                direction=MessageDirection.INBOUND,
                seq=0,
                message_id=str(
                    uuid5(
                        NAMESPACE_URL,
                        f"bcn:wecom:message:{provider_message_id}",
                    )
                ),
                session_id=session_id,
                channel_session_id=channel_session_id,
                channel=self.name,
                provider_thread_id=conversation,
                provider_message_id=provider_message_id,
                received_at_ms=received_at_ms,
                sender=SenderIdentity(id=sender_id),
                message_type=message_type,
                target=canonical_target,
                body=content.body,
                target_kind=target_kind,
                mentions_agent=mentions_agent,
                attachments=content.attachments,
                reply_to_message_id=reply_to_message_id,
                metadata=metadata,
            )
        )
        self._message_frames_queued += 1
        self._last_message_disposition = "queued"
        self._last_message_filter_reason = None
        self._observe(
            "wecom.message.queued",
            provider_message_id=provider_message_id,
            channel_session_id=channel_session_id,
            session_id=session_id,
            target_kind=target_kind.value,
            message_type=message_type,
            referenced=reply_to_message_id is not None,
        )

    async def _send_approval_card_update(
        self,
        connection: aiohttp.ClientWebSocketResponse,
        payload: str,
        *,
        task_id: str,
    ) -> None:
        self._approval_card_update_attempts += 1
        try:
            await asyncio.wait_for(
                connection.send_str(payload),
                timeout=_CARD_UPDATE_TIMEOUT_SECONDS,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            self._approval_card_update_failures += 1
            self._approval_card_update_unknown += 1
            self._last_approval_card_update_disposition = (
                "transport_error_outcome_unknown"
            )
            self._observe(
                "wecom.approval.card_update_failed",
                error_type=type(error).__name__,
                outcome="unknown",
                task_id=task_id,
            )
            return
        self._approval_card_update_unknown += 1
        self._last_approval_card_update_disposition = "sent_outcome_unknown"
        self._observe(
            "wecom.approval.card_update_sent",
            outcome="unknown",
            task_id=task_id,
        )

    async def _cancel_approval_card_updates(self) -> None:
        tasks = tuple(self._approval_card_update_tasks)
        self._approval_card_update_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _cancel_activity_cards(self) -> None:
        tasks = tuple(self._activity_tasks)
        self._activity_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._activity_reducers.clear()
        self._activity_routes.clear()

    def _filter_message(
        self,
        reason: str,
        *,
        provider_message_id: str | None = None,
    ) -> None:
        self._message_frames_filtered += 1
        self._last_message_disposition = "filtered"
        self._last_message_filter_reason = reason
        self._observe(
            "wecom.message.filtered",
            reason=reason,
            provider_message_id=provider_message_id,
        )

    def _observe(self, event_name: str, **metadata: object) -> None:
        self._logger.info(
            "%s",
            json.dumps(
                {
                    "event_name": event_name,
                    "created_at_ms": time_ns() // 1_000_000,
                    "metadata": {
                        key: value
                        for key, value in metadata.items()
                        if value is not None
                    },
                },
                separators=(",", ":"),
                sort_keys=True,
                default=str,
            ),
        )

    def _approval_card(
        self,
        pending: _PendingApproval,
        *,
        decision: ApprovalDecision | None = None,
    ) -> Mapping[str, object]:
        action = approval_action_text(self._translator, pending.request.approval.action)
        title = self._translator.text("approval.prompt.title").lstrip("#").strip()
        if decision is None:
            buttons = [
                {
                    "text": self._translator.text("approval.button.approve"),
                    "key": _APPROVE_KEY,
                },
                {
                    "text": self._translator.text("approval.button.reject"),
                    "key": _REJECT_KEY,
                },
            ]
        else:
            status_key = (
                "approval.card.status.approved"
                if decision is ApprovalDecision.APPROVED
                else "approval.card.status.rejected"
            )
            buttons = [
                {
                    "text": self._translator.text(status_key),
                    "key": _RESOLVED_KEY,
                }
            ]
        card: dict[str, object] = {
            "card_type": "button_interaction",
            "main_title": {"title": title, "desc": action},
            "button_list": buttons,
            "task_id": pending.task_id,
        }
        description = approval_description_text(
            self._translator, pending.request.approval.details
        )
        if description:
            card["sub_title_text"] = description
        return card

    async def _mixed_content(
        self, body: Mapping[str, object], message_type: str
    ) -> _InboundContent:
        """Read a mixed message as its text runs and the images between them."""

        mixed = body.get("mixed")
        items = mixed.get("msg_item") if isinstance(mixed, dict) else None
        texts: list[str] = []
        attachments: list[InboundAttachment] = []
        fingerprint_items: list[object] = []
        for item in items[:20] if isinstance(items, list) else ():
            if not isinstance(item, dict):
                continue
            if item.get("msgtype") == "text":
                text = item.get("text")
                content = text.get("content") if isinstance(text, dict) else None
                if isinstance(content, str):
                    texts.append(content)
                    fingerprint_items.append({"message_type": "text", "body": content})
            elif item.get("msgtype") == "image":
                image = item.get("image")
                if isinstance(image, dict):
                    attachment, fingerprint = await self._media(image, "image")
                    attachments.append(attachment)
                    fingerprint_items.append(fingerprint)
        return _InboundContent(
            body="\n".join(texts),
            attachments=tuple(attachments),
            fingerprint=self._content_fingerprint(
                {"message_type": message_type, "items": fingerprint_items}
            ),
        )

    async def _content(
        self, body: Mapping[str, object], message_type: str
    ) -> _InboundContent:
        if message_type in {"text", "voice"}:
            part = body.get(message_type)
            content = part.get("content") if isinstance(part, dict) else None
            text = content if isinstance(content, str) else ""
            return _InboundContent(
                body=text,
                attachments=(),
                fingerprint=self._content_fingerprint(
                    {"message_type": message_type, "body": text}
                ),
            )
        if message_type == "mixed":
            return await self._mixed_content(body, message_type)
        if message_type in {"image", "file", "video"}:
            media = body.get(message_type)
            if isinstance(media, dict):
                attachment, fingerprint = await self._media(media, message_type)
                return _InboundContent(
                    body="",
                    attachments=(attachment,),
                    fingerprint=self._content_fingerprint(
                        {"message_type": message_type, "media": fingerprint}
                    ),
                )
        unsupported = f"[unsupported WeCom message type: {message_type}]"
        return _InboundContent(
            body=unsupported,
            attachments=(),
            fingerprint=self._content_fingerprint(
                {"message_type": message_type, "body": unsupported}
            ),
        )

    async def _media(
        self, media: Mapping[str, object], kind: str
    ) -> tuple[InboundAttachment, object]:
        url = media.get("url")
        aes_key = media.get("aeskey")
        if not isinstance(url, str) or not url:
            return (
                self._context.attachments.failed(
                    name=f"{kind}.bin", kind=kind, error="missing_media_url"
                ),
                {"kind": kind, "error": "missing_media_url"},
            )
        try:
            timeout = aiohttp.ClientTimeout(total=60, connect=10)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.get(url, allow_redirects=True) as response,
            ):
                response.raise_for_status()
                content = bytearray()
                async for chunk in response.content.iter_chunked(64 * 1024):
                    content.extend(chunk)
                    if len(content) > _MAX_MEDIA_BYTES:
                        raise ValueError("media_too_large")
                name = self._filename(response.headers.get("Content-Disposition"), kind)
                media_type = response.headers.get("Content-Type")
            plaintext = bytes(content)
            if isinstance(aes_key, str) and aes_key:
                plaintext = self._decrypt(plaintext, aes_key)
            return (
                await self._context.attachments.materialize(
                    plaintext, name=name, kind=kind, media_type=media_type
                ),
                {"kind": kind, "sha256": hashlib.sha256(plaintext).hexdigest()},
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            error_kind = f"media_materialization_failed:{type(error).__name__}"
            source_identity = self._content_fingerprint(
                {
                    "url": url,
                    "aes_key": aes_key if isinstance(aes_key, str) else None,
                }
            )
            return (
                self._context.attachments.failed(
                    name=f"{kind}.bin",
                    kind=kind,
                    error=error_kind,
                ),
                {
                    "kind": kind,
                    "error": error_kind,
                    "source_identity": source_identity,
                },
            )

    @staticmethod
    def _content_fingerprint(value: object) -> str:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _decrypt(content: bytes, aes_key: str) -> bytes:
        key = base64.b64decode(aes_key + "=" * (-len(aes_key) % 4))
        if len(key) != 32 or not content or len(content) % 16:
            raise ValueError("invalid encrypted media")
        decryptor = Cipher(algorithms.AES(key), modes.CBC(key[:16])).decryptor()
        padded = decryptor.update(content) + decryptor.finalize()
        padding = padded[-1]
        if (
            padding < 1
            or padding > 32
            or padded[-padding:] != bytes([padding]) * padding
        ):
            raise ValueError("invalid encrypted media padding")
        return padded[:-padding]

    @staticmethod
    def _filename(value: str | None, kind: str) -> str:
        if value:
            message = EmailMessage()
            message["Content-Disposition"] = value
            filename = message.get_filename()
            if filename:
                return unquote(filename)
        return f"{kind}.bin"

    @staticmethod
    def _frame(raw: str | bytes) -> dict[str, object]:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise TypeError("WeCom frame must be a JSON object")
        return payload

    @staticmethod
    def _message_data(message: aiohttp.WSMessage) -> str | bytes:
        if message.type not in {aiohttp.WSMsgType.TEXT, aiohttp.WSMsgType.BINARY}:
            raise TypeError("WeCom frame must be text or binary")
        if not isinstance(message.data, str | bytes):
            raise TypeError("WeCom frame payload is invalid")
        return message.data

    @staticmethod
    def _request_id(frame: Mapping[str, object]) -> str:
        headers = frame.get("headers")
        request_id = headers.get("req_id") if isinstance(headers, dict) else None
        return request_id if isinstance(request_id, str) else ""

    @staticmethod
    def _is_disconnected_event(frame: Mapping[str, object]) -> bool:
        if frame.get("cmd") != "aibot_event_callback":
            return False
        body = frame.get("body")
        event = body.get("event") if isinstance(body, dict) else None
        return (
            isinstance(event, dict) and event.get("eventtype") == "disconnected_event"
        )

    async def _backoff(self, attempt: int) -> None:
        delay = min(2 ** max(attempt - 1, 0), 30)
        await self._wait_for_timer(delay + random.uniform(0, min(delay * 0.2, 1)))

    async def _wait_for_timer(self, delay_seconds: float) -> None:
        timer_wheel = self._timer_wheel
        if timer_wheel is None:
            raise RuntimeError("WeCom timer wheel is not configured")
        timer = timer_wheel.create(math.ceil(max(0.0, delay_seconds) * 1_000))
        await timer.wait()


class _AuthenticationError(RuntimeError):
    pass


__all__ = ["WeComChannel"]
