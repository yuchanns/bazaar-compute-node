from __future__ import annotations

import asyncio
import base64
import json
import random
from collections.abc import AsyncIterator, Mapping
from email.message import Message
from time import time_ns
from urllib.parse import unquote
from uuid import NAMESPACE_URL, uuid4, uuid5

import aiohttp
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from ...core.channel import ChannelContext, ChannelDeliveryReceipt, IChannel
from ...core.models import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalResult,
    ChannelTargetKind,
    InboundAttachment,
    InboundMessage,
    OutboundMessage,
)
from ...core.outcomes import ProviderCallResult, ProviderCallStatus

_STOP = object()
_MAX_MEDIA_BYTES = 25 * 1024 * 1024


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
        self._seen_message_ids: set[str] = set()
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
        }

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

    async def send(
        self, message: OutboundMessage, *, timeout: float
    ) -> ProviderCallResult[ChannelDeliveryReceipt]:
        return ProviderCallResult(
            status=ProviderCallStatus.FAILED,
            error_kind="outbound_not_implemented",
            error_message="WeCom outbound delivery is implemented in Task 5B",
        )

    async def request_approval(
        self, request: ApprovalRequest, *, timeout: float
    ) -> ApprovalResult:
        return ApprovalResult(
            request_id=request.request_id,
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
                        self._degraded = True
                        self._state = "degraded"
                        self._last_disconnect_kind = "disconnected_event"
                        await connection.close()
                        return
                    request_id = self._request_id(frame)
                    if request_id.startswith("ping-") and frame.get("errcode") == 0:
                        self._heartbeat_ack = request_id
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
        body = frame.get("body")
        if not isinstance(body, dict):
            return
        if frame.get("cmd") == "aibot_event_callback" or body.get("msgtype") == "event":
            self._ignored_event_frames += 1
            return
        provider_message_id = body.get("msgid")
        if not isinstance(provider_message_id, str) or not provider_message_id:
            return
        if (
            provider_message_id in self._seen_message_ids
            or await self._context.inbound_exists(self.name, provider_message_id)
        ):
            return
        self._seen_message_ids.add(provider_message_id)
        chat_type = body.get("chattype")
        sender = body.get("from")
        sender_id = sender.get("userid") if isinstance(sender, dict) else None
        if not isinstance(sender_id, str) or not sender_id:
            return
        message_type = body.get("msgtype")
        if not isinstance(message_type, str) or not message_type:
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
            return
        if not isinstance(conversation, str) or not conversation:
            return
        text, attachments = await self._content(body, message_type)
        account = body.get("aibotid")
        account_scope = (
            account if isinstance(account, str) and account else self._bot_id
        )
        identity = f"wecom:{account_scope}:{target_prefix}:{conversation}"
        channel_session_id = str(uuid5(NAMESPACE_URL, identity))
        session_id = str(uuid5(NAMESPACE_URL, f"bcn:{identity}"))
        metadata: dict[str, object] = {}
        create_time = body.get("create_time")
        if isinstance(create_time, int) and not isinstance(create_time, bool):
            metadata["provider_create_time"] = create_time
        if isinstance(body.get("quote"), dict):
            metadata["has_quote"] = True
        request_id = self._request_id(frame)
        await self._inbound.put(
            InboundMessage(
                seq=0,
                message_id=provider_message_id,
                session_id=session_id,
                channel_session_id=channel_session_id,
                channel=self.name,
                provider_message_id=provider_message_id,
                received_at_ms=time_ns() // 1_000_000,
                sender_id=sender_id,
                sender_display_name=sender_id,
                message_type=message_type,
                canonical_target=f"{target_prefix}:{conversation}",
                body=text,
                target_kind=target_kind,
                mentions_agent=mentions_agent,
                attachments=tuple(attachments),
                provider_payload_ref=request_id or None,
                metadata=metadata,
            )
        )

    async def _content(
        self, body: Mapping[str, object], message_type: str
    ) -> tuple[str, list[InboundAttachment]]:
        if message_type in {"text", "voice"}:
            part = body.get(message_type)
            content = part.get("content") if isinstance(part, dict) else None
            return (content if isinstance(content, str) else "", [])
        if message_type == "mixed":
            mixed = body.get("mixed")
            items = mixed.get("msg_item") if isinstance(mixed, dict) else None
            texts: list[str] = []
            attachments: list[InboundAttachment] = []
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
                    elif item.get("msgtype") == "image":
                        image = item.get("image")
                        if isinstance(image, dict):
                            attachments.append(await self._media(image, "image"))
            return ("\n".join(texts), attachments)
        if message_type in {"image", "file", "video"}:
            media = body.get(message_type)
            if isinstance(media, dict):
                return ("", [await self._media(media, message_type)])
        return (f"[unsupported WeCom message type: {message_type}]", [])

    async def _media(self, media: Mapping[str, object], kind: str) -> InboundAttachment:
        url = media.get("url")
        aes_key = media.get("aeskey")
        if not isinstance(url, str) or not url:
            return self._context.attachments.failed(
                name=f"{kind}.bin", kind=kind, error="missing_media_url"
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
            return await self._context.attachments.materialize(
                plaintext, name=name, kind=kind, media_type=media_type
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            return self._context.attachments.failed(
                name=f"{kind}.bin",
                kind=kind,
                error=f"media_materialization_failed:{type(error).__name__}",
            )

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
