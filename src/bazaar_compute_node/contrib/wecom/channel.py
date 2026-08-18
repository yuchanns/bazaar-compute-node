from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import random
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from email.message import Message
from time import time_ns
from urllib.parse import unquote
from uuid import NAMESPACE_URL, uuid4, uuid5

import aiohttp
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

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
    InboundMessage,
    SenderIdentity,
)
from ...core.outcomes import ProviderCallResult, ProviderCallStatus
from ...core.runtime import RuntimeStreamItem
from .markdown import split_markdown
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


@dataclass(frozen=True, slots=True)
class _RequestResult:
    status: ProviderCallStatus
    receipt: Mapping[str, object]
    body: Mapping[str, object] | None = None
    error_kind: str | None = None
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class _InboundContent:
    body: str
    attachments: tuple[InboundAttachment, ...]
    fingerprint: str


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
        self._bot_id = bot_id
        self._secret = secret
        self._websocket_url = websocket_url
        self._inbound: asyncio.Queue[InboundMessage | object] = asyncio.Queue()
        self._ready = asyncio.Event()
        self._stopping = asyncio.Event()
        self._startup_finished = asyncio.Event()
        self._startup_error: Exception | None = None
        self._runner: asyncio.Task[None] | None = None
        self._connection: aiohttp.ClientWebSocketResponse | None = None
        self._send_lock = asyncio.Lock()
        self._pending_acks: dict[str, asyncio.Future[Mapping[str, object]]] = {}
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
        }

    def get_identity(self) -> ChannelIdentity | None:
        return None

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

    async def receive(self) -> AsyncIterator[InboundMessage]:
        while True:
            item = await self._inbound.get()
            if item is _STOP:
                return
            if not isinstance(item, InboundMessage):
                raise TypeError("WeCom inbound queue contained an invalid message")
            yield item

    def accept_turn_event(
        self,
        item: RuntimeStreamItem,
        *,
        session_id: str,
    ) -> None:
        return None

    async def send(
        self, request: ChannelSendRequest, *, timeout: float
    ) -> ProviderCallResult[ChannelDeliveryReceipt]:
        message = request.outbound
        target_id = request.provider_thread_id
        try:
            batches = (
                split_markdown(message.body, limit=_MAX_MARKDOWN_BYTES)
                if message.body.strip()
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
        receipts: list[dict[str, object]] = []
        upload_receipts: list[dict[str, object]] = []
        confirmed = 0
        total_parts = len(batches) + len(message.attachments)
        if total_parts == 0:
            return ProviderCallResult(
                status=ProviderCallStatus.FAILED,
                error_kind="empty_message",
                error_message="WeCom outbound message must not be empty",
            )
        try:
            await asyncio.wait_for(
                self._send_lock.acquire(), timeout=max(0.0, deadline - loop.time())
            )
        except TimeoutError:
            return self._clear_failure(
                total=total_parts,
                receipts=receipts,
                upload_receipts=upload_receipts,
                confirmed=confirmed,
                error_kind="delivery_timeout",
                error_message="WeCom delivery timed out waiting for the send lock",
            )
        try:
            try:
                attachments = await asyncio.wait_for(
                    asyncio.to_thread(
                        prepare_attachments,
                        self._context.workspace(),
                        message.attachments,
                    ),
                    timeout=max(0.0, deadline - loop.time()),
                )
            except TimeoutError:
                return self._clear_failure(
                    total=total_parts,
                    receipts=receipts,
                    upload_receipts=upload_receipts,
                    confirmed=confirmed,
                    error_kind="delivery_timeout",
                    error_message="WeCom attachment preflight timed out",
                )
            except (OSError, ValueError) as error:
                return self._clear_failure(
                    total=total_parts,
                    receipts=receipts,
                    upload_receipts=upload_receipts,
                    confirmed=confirmed,
                    error_kind="invalid_attachment",
                    error_message=str(error),
                )
            for batch in batches:
                if loop.time() >= deadline:
                    return self._clear_failure(
                        total=total_parts,
                        receipts=receipts,
                        upload_receipts=upload_receipts,
                        confirmed=confirmed,
                        error_kind="delivery_timeout",
                        error_message=(
                            "WeCom delivery timed out between batches"
                            if confirmed
                            else "WeCom delivery timed out before sending"
                        ),
                    )
                connection = self._connection
                if connection is None or connection.closed or not self._ready.is_set():
                    return self._clear_failure(
                        total=total_parts,
                        receipts=receipts,
                        upload_receipts=upload_receipts,
                        confirmed=confirmed,
                        error_kind="connection_unavailable",
                        error_message=(
                            "WeCom connection became unavailable between batches"
                            if confirmed
                            else "WeCom connection is unavailable"
                        ),
                    )
                result = await self._send_batch(
                    connection,
                    target_id=target_id,
                    target_kind=request.target_kind,
                    content=batch,
                    deadline=deadline,
                )
                receipt = dict(result.receipt)
                receipt.update({"part_type": "markdown", "ordinal": len(receipts) + 1})
                receipts.append(receipt)
                if result.status is ProviderCallStatus.CONFIRMED:
                    confirmed += 1
                    continue
                if result.status is ProviderCallStatus.FAILED:
                    return self._clear_failure(
                        total=total_parts,
                        receipts=receipts,
                        upload_receipts=upload_receipts,
                        confirmed=confirmed,
                        error_kind=result.error_kind or "provider_rejected_batch",
                        error_message=result.error_message
                        or "WeCom rejected the outbound message",
                    )
                return ProviderCallResult(
                    status=ProviderCallStatus.UNKNOWN,
                    error_kind=result.error_kind or "ack_unknown",
                    error_message=result.error_message
                    or "WeCom acknowledgement outcome is unknown",
                    receipt=self._delivery_receipt(
                        total_parts, confirmed, receipts, upload_receipts
                    ),
                )

            for attachment_ordinal, attachment in enumerate(attachments, start=1):
                if loop.time() >= deadline:
                    return self._clear_failure(
                        total=total_parts,
                        receipts=receipts,
                        upload_receipts=upload_receipts,
                        confirmed=confirmed,
                        error_kind="delivery_timeout",
                        error_message="WeCom delivery timed out between parts",
                    )
                connection = self._connection
                if connection is None or connection.closed or not self._ready.is_set():
                    return self._clear_failure(
                        total=total_parts,
                        receipts=receipts,
                        upload_receipts=upload_receipts,
                        confirmed=confirmed,
                        error_kind="connection_unavailable",
                        error_message="WeCom connection became unavailable between parts",
                    )
                upload = await self._upload_attachment(
                    connection,
                    attachment=attachment,
                    attachment_ordinal=attachment_ordinal,
                    deadline=deadline,
                )
                upload_receipts.extend(dict(item) for item in upload.receipts)
                if upload.error_kind is not None:
                    return self._clear_failure(
                        total=total_parts,
                        receipts=receipts,
                        upload_receipts=upload_receipts,
                        confirmed=confirmed,
                        error_kind=upload.error_kind,
                        error_message=upload.error_message
                        or "WeCom attachment upload failed",
                    )
                if upload.media_id is None:
                    raise AssertionError("successful WeCom upload requires media_id")
                result = await self._send_media(
                    connection,
                    target_id=target_id,
                    target_kind=request.target_kind,
                    attachment=attachment,
                    media_id=upload.media_id,
                    deadline=deadline,
                )
                receipt = dict(result.receipt)
                receipt.update(
                    {
                        "part_type": attachment.media_type,
                        "ordinal": len(receipts) + 1,
                        "attachment_name": attachment.descriptor.name,
                    }
                )
                receipts.append(receipt)
                if result.status is ProviderCallStatus.CONFIRMED:
                    confirmed += 1
                    continue
                if result.status is ProviderCallStatus.FAILED:
                    return self._clear_failure(
                        total=total_parts,
                        receipts=receipts,
                        upload_receipts=upload_receipts,
                        confirmed=confirmed,
                        error_kind=result.error_kind or "provider_rejected_part",
                        error_message=result.error_message
                        or "WeCom rejected an outbound attachment",
                    )
                return ProviderCallResult(
                    status=ProviderCallStatus.UNKNOWN,
                    error_kind=result.error_kind or "ack_unknown",
                    error_message=result.error_message
                    or "WeCom acknowledgement outcome is unknown",
                    receipt=self._delivery_receipt(
                        total_parts, confirmed, receipts, upload_receipts
                    ),
                )
        finally:
            self._send_lock.release()

        return ProviderCallResult(
            status=ProviderCallStatus.CONFIRMED,
            value=ChannelDeliveryReceipt(
                provider_receipt_ref=str(receipts[-1]["provider_request_id"])
            ),
            receipt=self._delivery_receipt(
                total_parts, confirmed, receipts, upload_receipts
            ),
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

    async def _upload_attachment(
        self,
        connection: aiohttp.ClientWebSocketResponse,
        *,
        attachment: PreparedAttachment,
        attachment_ordinal: int,
        deadline: float,
    ) -> UploadResult:
        receipts: list[dict[str, object]] = []
        init = await self._request(
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
            rejection_kind="provider_rejected_upload",
            rejection_message="WeCom rejected attachment upload initialization",
            unknown_kind="upload_unknown",
            unknown_message="WeCom attachment upload outcome is unknown",
            visible=False,
        )
        init_receipt = dict(init.receipt)
        init_receipt.update({"stage": "init", "attachment_ordinal": attachment_ordinal})
        receipts.append(init_receipt)
        if init.status is not ProviderCallStatus.CONFIRMED:
            return UploadResult(
                media_id=None,
                receipts=tuple(receipts),
                error_kind=init.error_kind or "upload_unknown",
                error_message=init.error_message,
            )
        upload_id = init.body.get("upload_id") if init.body is not None else None
        if not isinstance(upload_id, str) or not upload_id:
            receipts[-1]["state"] = "invalid_ack"
            return UploadResult(
                media_id=None,
                receipts=tuple(receipts),
                error_kind="invalid_upload_ack",
                error_message="WeCom upload initialization omitted upload_id",
            )

        try:
            reader = await asyncio.to_thread(AttachmentReader.open, attachment)
        except (OSError, ValueError) as error:
            return UploadResult(
                media_id=None,
                receipts=tuple(receipts),
                error_kind="attachment_read_failed",
                error_message=str(error),
            )
        try:
            for chunk_index in range(attachment.total_chunks):
                try:
                    chunk = await asyncio.to_thread(reader.read_chunk)
                except OSError as error:
                    return UploadResult(
                        media_id=None,
                        receipts=tuple(receipts),
                        error_kind="attachment_read_failed",
                        error_message=str(error),
                    )
                if not chunk:
                    return UploadResult(
                        media_id=None,
                        receipts=tuple(receipts),
                        error_kind="attachment_read_failed",
                        error_message="WeCom attachment ended before all chunks were read",
                    )
                result = await self._request(
                    connection,
                    command="aibot_upload_media_chunk",
                    body={
                        "upload_id": upload_id,
                        "chunk_index": chunk_index,
                        "base64_data": base64.b64encode(chunk).decode("ascii"),
                    },
                    deadline=deadline,
                    rejection_kind="provider_rejected_upload",
                    rejection_message="WeCom rejected an attachment upload chunk",
                    unknown_kind="upload_unknown",
                    unknown_message="WeCom attachment upload outcome is unknown",
                    visible=False,
                )
                chunk_receipt = dict(result.receipt)
                chunk_receipt.update(
                    {
                        "stage": "chunk",
                        "attachment_ordinal": attachment_ordinal,
                        "chunk_index": chunk_index,
                    }
                )
                receipts.append(chunk_receipt)
                if result.status is not ProviderCallStatus.CONFIRMED:
                    return UploadResult(
                        media_id=None,
                        receipts=tuple(receipts),
                        error_kind=result.error_kind or "upload_unknown",
                        error_message=result.error_message,
                    )
        finally:
            await asyncio.to_thread(reader.close)

        finish = await self._request(
            connection,
            command="aibot_upload_media_finish",
            body={"upload_id": upload_id},
            deadline=deadline,
            rejection_kind="provider_rejected_upload",
            rejection_message="WeCom rejected attachment upload completion",
            unknown_kind="upload_unknown",
            unknown_message="WeCom attachment upload outcome is unknown",
            visible=False,
        )
        finish_receipt = dict(finish.receipt)
        finish_receipt.update(
            {"stage": "finish", "attachment_ordinal": attachment_ordinal}
        )
        receipts.append(finish_receipt)
        if finish.status is not ProviderCallStatus.CONFIRMED:
            return UploadResult(
                media_id=None,
                receipts=tuple(receipts),
                error_kind=finish.error_kind or "upload_unknown",
                error_message=finish.error_message,
            )
        media_id = finish.body.get("media_id") if finish.body is not None else None
        if not isinstance(media_id, str) or not media_id:
            receipts[-1]["state"] = "invalid_ack"
            return UploadResult(
                media_id=None,
                receipts=tuple(receipts),
                error_kind="invalid_upload_ack",
                error_message="WeCom upload completion omitted media_id",
            )
        return UploadResult(media_id=media_id, receipts=tuple(receipts))

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
            return _RequestResult(
                status=ProviderCallStatus.UNKNOWN,
                receipt={
                    "provider_request_id": request_id,
                    "state": "unknown",
                    "visible": visible,
                    "attempted_at_ms": attempted_at_ms,
                    "error_type": type(error).__name__,
                },
                error_kind=unknown_kind,
                error_message=unknown_message,
            )
        try:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError
            frame = await asyncio.wait_for(future, timeout=remaining)
        except asyncio.CancelledError:
            raise
        except (TimeoutError, ConnectionError) as error:
            return _RequestResult(
                status=ProviderCallStatus.UNKNOWN,
                receipt={
                    "provider_request_id": request_id,
                    "state": "unknown",
                    "visible": visible,
                    "attempted_at_ms": attempted_at_ms,
                    "error_type": type(error).__name__,
                },
                error_kind=unknown_kind,
                error_message=unknown_message,
            )
        finally:
            self._pending_acks.pop(request_id, None)

        acknowledged_at_ms = time_ns() // 1_000_000
        error_code = frame.get("errcode")
        error_message = frame.get("errmsg")
        if not isinstance(error_code, int) or isinstance(error_code, bool):
            return _RequestResult(
                status=ProviderCallStatus.UNKNOWN,
                receipt={
                    "provider_request_id": request_id,
                    "state": "unknown",
                    "visible": visible,
                    "attempted_at_ms": attempted_at_ms,
                    "acknowledged_at_ms": acknowledged_at_ms,
                    "error_type": "InvalidAcknowledgement",
                },
                error_kind=unknown_kind,
                error_message=unknown_message,
            )
        if error_code != 0:
            return _RequestResult(
                status=ProviderCallStatus.FAILED,
                receipt={
                    "provider_request_id": request_id,
                    "state": "failed",
                    "visible": visible,
                    "attempted_at_ms": attempted_at_ms,
                    "acknowledged_at_ms": acknowledged_at_ms,
                    "error_code": error_code,
                    "error_message": (
                        error_message[:256] if isinstance(error_message, str) else None
                    ),
                },
                error_kind=rejection_kind,
                error_message=rejection_message,
            )
        response_body = frame.get("body")
        return _RequestResult(
            status=ProviderCallStatus.CONFIRMED,
            receipt={
                "provider_request_id": request_id,
                "state": "confirmed",
                "visible": visible,
                "attempted_at_ms": attempted_at_ms,
                "acknowledged_at_ms": acknowledged_at_ms,
                "error_code": 0,
            },
            body=response_body if isinstance(response_body, Mapping) else None,
        )

    @staticmethod
    def _delivery_receipt(
        total: int,
        confirmed: int,
        receipts: list[dict[str, object]],
        upload_receipts: list[dict[str, object]],
    ) -> Mapping[str, object]:
        return {
            "total_parts": total,
            "confirmed_parts": confirmed,
            "parts": tuple(receipts),
            "uploads": tuple(upload_receipts),
            "provider_receipt_ref": (
                receipts[-1]["provider_request_id"] if receipts else None
            ),
        }

    def _clear_failure(
        self,
        *,
        total: int,
        receipts: list[dict[str, object]],
        upload_receipts: list[dict[str, object]],
        confirmed: int,
        error_kind: str,
        error_message: str,
    ) -> ProviderCallResult[ChannelDeliveryReceipt]:
        receipt = self._delivery_receipt(total, confirmed, receipts, upload_receipts)
        if confirmed:
            return ProviderCallResult(
                status=ProviderCallStatus.PARTIAL,
                value=ChannelDeliveryReceipt(
                    provider_receipt_ref=str(receipts[-1]["provider_request_id"])
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
        return ApprovalResult(
            request_id=request.approval.request_id,
            decision=ApprovalDecision.APPROVED,
            decided_at_ms=time_ns() // 1_000_000,
        )

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
                if not self._stopping.is_set() and not self._degraded:
                    raise ConnectionError("WeCom WebSocket closed")
            finally:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
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

    async def _heartbeat(self, connection: aiohttp.ClientWebSocketResponse) -> None:
        missed = 0
        last_request = ""
        while not self._stopping.is_set():
            await asyncio.sleep(30)
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

    async def _receive_message(self, frame: Mapping[str, object]) -> None:
        command = frame.get("cmd")
        body = frame.get("body")
        if command == "aibot_event_callback":
            self._ignored_event_frames += 1
            event = body.get("event") if isinstance(body, dict) else None
            event_type = event.get("eventtype") if isinstance(event, dict) else None
            self._last_event_type = (
                event_type if isinstance(event_type, str) and event_type else "unknown"
            )
            self._observe(
                "wecom.event.received",
                event_type=self._last_event_type,
            )
            return
        now_ms = time_ns() // 1_000_000
        self._message_frames_received += 1
        self._last_message_frame_at_ms = now_ms
        if not isinstance(body, dict):
            self._filter_message("invalid_body")
            return
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
            return
        chat_type = body.get("chattype")
        sender = body.get("from")
        sender_id = sender.get("userid") if isinstance(sender, dict) else None
        if not isinstance(sender_id, str) or not sender_id:
            self._filter_message(
                "missing_sender", provider_message_id=provider_message_id
            )
            return
        message_type = body.get("msgtype")
        if not isinstance(message_type, str) or not message_type:
            self._filter_message(
                "missing_message_type", provider_message_id=provider_message_id
            )
            return
        chat_id = body.get("chatid")
        if chat_type == "group":
            conversation = chat_id
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
            return
        if not isinstance(conversation, str) or not conversation:
            self._filter_message(
                "missing_conversation", provider_message_id=provider_message_id
            )
            return
        identity = f"wecom:{target_prefix}:{conversation}"
        channel_session_id = str(uuid5(NAMESPACE_URL, identity))
        session_id = str(uuid5(NAMESPACE_URL, f"bcn:{identity}"))
        canonical_target = f"{target_prefix}:{channel_session_id}"
        received_at_ms = time_ns() // 1_000_000
        content = await self._content(body, message_type)
        metadata: dict[str, object] = {}
        create_time = body.get("create_time")
        if isinstance(create_time, int) and not isinstance(create_time, bool):
            metadata["provider_create_time"] = create_time
        reply_to_message_id = None
        quote = body.get("quote")
        if isinstance(quote, dict):
            quote_type = quote.get("msgtype")
            if isinstance(quote_type, str) and quote_type:
                quote_content = await self._content(quote, quote_type)
                quote_provider_message_id = quote_content.fingerprint
                reply_to_message_id = str(
                    uuid5(
                        NAMESPACE_URL,
                        "bcn:wecom:quoted-message:"
                        f"{conversation}:{quote_content.fingerprint}",
                    )
                )
                await self._inbound.put(
                    InboundMessage(
                        seq=0,
                        message_id=reply_to_message_id,
                        session_id=session_id,
                        channel_session_id=channel_session_id,
                        channel=self.name,
                        provider_thread_id=conversation,
                        provider_message_id=quote_provider_message_id,
                        received_at_ms=received_at_ms,
                        sender=None,
                        message_type=quote_type,
                        canonical_target=canonical_target,
                        body=quote_content.body,
                        target_kind=target_kind,
                        mentions_agent=False,
                        notifies_runtime=False,
                        attachments=quote_content.attachments,
                    )
                )
            else:
                self._observe(
                    "wecom.message.reference_unresolved",
                    provider_message_id=provider_message_id,
                    reason="missing_message_type",
                )
        await self._inbound.put(
            InboundMessage(
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
                canonical_target=canonical_target,
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
            mixed = body.get("mixed")
            items = mixed.get("msg_item") if isinstance(mixed, dict) else None
            texts: list[str] = []
            attachments: list[InboundAttachment] = []
            fingerprint_items: list[object] = []
            if isinstance(items, list):
                for item in items[:20]:
                    if not isinstance(item, dict):
                        continue
                    if item.get("msgtype") == "text":
                        text = item.get("text")
                        content = (
                            text.get("content") if isinstance(text, dict) else None
                        )
                        if isinstance(content, str):
                            texts.append(content)
                            fingerprint_items.append(
                                {"message_type": "text", "body": content}
                            )
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
            message = Message()
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
        await asyncio.sleep(delay + random.uniform(0, min(delay * 0.2, 1)))


class _AuthenticationError(RuntimeError):
    pass


__all__ = ["WeComChannel"]
