from __future__ import annotations

import asyncio
import json
import math
import random
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from time import monotonic, time_ns
from typing import Any

import aiohttp

from ...core.timerwheel import TimerWheel
from .api import ClientConfig, LarkApi, LarkApiError, LarkEndpoint, LarkTransportError
from .frame import (
    MAX_FRAME_BYTES,
    Frame,
    FrameDecodeError,
    Header,
    decode_frame,
    encode_frame,
    header_values,
)

CONTROL_METHOD = 0
DATA_METHOD = 1
HEADER_TYPE = "type"
HEADER_MESSAGE_ID = "message_id"
HEADER_SUM = "sum"
HEADER_SEQ = "seq"
HEADER_TRACE_ID = "trace_id"
HEADER_BIZ_RT = "biz_rt"
MESSAGE_EVENT = "event"
MESSAGE_CARD = "card"
MESSAGE_PING = "ping"
MESSAGE_PONG = "pong"

MAX_RAW_EVENT_BYTES = MAX_FRAME_BYTES
MAX_RAW_EVENTS = 256
MAX_FRAGMENT_MESSAGES = 128
MAX_FRAGMENT_COUNT = 64
MAX_FRAGMENT_AGE_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class LarkAck:
    """Optional provider ACK payload returned by a message handler."""

    code: int = 200
    payload: bytes | None = None
    accepted: bool = True
    post_ack: Callable[[], Awaitable[None]] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.code, int) or isinstance(self.code, bool):
            raise TypeError("Lark ACK code must be an integer")
        if self.code < 100 or self.code > 599:
            raise ValueError("Lark ACK code must be an HTTP-style status code")
        if self.payload is not None and not isinstance(self.payload, bytes):
            raise TypeError("Lark ACK payload must be bytes")
        if not isinstance(self.accepted, bool):
            raise TypeError("Lark ACK accepted flag must be boolean")
        if self.post_ack is not None and not callable(self.post_ack):
            raise TypeError("Lark ACK post-ACK callback must be callable")


MessageHandlerResult = bool | LarkAck | None
MessageHandler = Callable[
    [str, Mapping[str, object], Frame],
    Awaitable[MessageHandlerResult] | MessageHandlerResult,
]


@dataclass(slots=True)
class _Fragments:
    total: int
    parts: dict[int, bytes]
    size_bytes: int
    updated_at: float


class LarkTransport:
    def __init__(
        self,
        api: LarkApi,
        *,
        timer_wheel: TimerWheel,
        on_message: MessageHandler | None = None,
    ) -> None:
        self._api = api
        self._timer_wheel = timer_wheel
        self._on_message = on_message
        self._runner: asyncio.Task[None] | None = None
        self._connection: Any | None = None
        self._endpoint: LarkEndpoint | None = None
        self._send_lock = asyncio.Lock()
        self._stopping = asyncio.Event()
        self._ready = asyncio.Event()
        self._post_ack_tasks: set[asyncio.Task[None]] = set()
        self._startup_error: BaseException | None = None
        self._startup_deadline = 0.0
        self._state = "stopped"
        self._connection_generation = 0
        self._connected_at_ms: int | None = None
        self._last_event_at_ms: int | None = None
        self._last_disconnect_kind: str | None = None
        self._events_received = 0
        self._messages_queued = 0
        self._messages_filtered = 0
        self._message_mapping_failures = 0
        self._last_message_disposition: str | None = None
        self._last_message_filter_reason: str | None = None
        self._fragments: OrderedDict[str, _Fragments] = OrderedDict()

    @property
    def health(self) -> Mapping[str, object]:
        return {
            "state": self._state,
            "connection_generation": self._connection_generation,
            "connected_at_ms": self._connected_at_ms,
            "last_event_at_ms": self._last_event_at_ms,
            "last_disconnect_kind": self._last_disconnect_kind,
            "events_received": self._events_received,
            "messages_queued": self._messages_queued,
            "messages_filtered": self._messages_filtered,
            "message_mapping_failures": self._message_mapping_failures,
            "last_message_disposition": self._last_message_disposition,
            "last_message_filter_reason": self._last_message_filter_reason,
            "fragment_messages": len(self._fragments),
        }

    @property
    def state(self) -> str:
        return self._state

    async def start(self, *, timeout: float) -> None:
        if self._runner is not None:
            if not self._runner.done():
                return
            self._runner = None
        if timeout <= 0:
            raise TimeoutError("Lark transport startup deadline expired")
        loop = asyncio.get_running_loop()
        self._startup_deadline = loop.time() + timeout
        self._stopping.clear()
        self._ready.clear()
        self._startup_error = None
        self._state = "starting"
        self._runner = asyncio.create_task(self._run(), name="bcn-lark-transport")
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=timeout)
        except BaseException:
            await self._abort_runner()
            raise
        if self._startup_error is not None:
            error = self._startup_error
            await self._abort_runner()
            raise error

    async def stop(self, *, timeout: float) -> None:
        self._state = "stopping"
        self._stopping.set()
        post_ack_tasks = tuple(self._post_ack_tasks)
        for task in post_ack_tasks:
            task.cancel()
        if post_ack_tasks:
            await asyncio.gather(*post_ack_tasks, return_exceptions=True)
        self._post_ack_tasks.clear()
        connection = self._connection
        if connection is not None:
            await _close_connection(connection)
        runner = self._runner
        if runner is not None:
            try:
                await asyncio.wait_for(runner, timeout=max(0.0, timeout))
            except TimeoutError:
                runner.cancel()
                await asyncio.gather(runner, return_exceptions=True)
        self._runner = None
        self._connection = None
        self._endpoint = None
        self._ready.clear()
        self._fragments.clear()
        self._state = "stopped"

    async def _abort_runner(self) -> None:
        self._stopping.set()
        connection = self._connection
        if connection is not None:
            await _close_connection(connection)
        runner = self._runner
        if runner is not None:
            runner.cancel()
            await asyncio.gather(runner, return_exceptions=True)
        self._runner = None
        self._connection = None
        self._endpoint = None
        self._state = "stopped"

    async def _run(self) -> None:
        first_connection = True
        reconnect_attempts = 0
        while not self._stopping.is_set():
            try:
                endpoint = await self._get_endpoint(first_connection)
                await self._serve(endpoint)
                if self._stopping.is_set():
                    return
                first_connection = False
                reconnect_attempts = 0
                raise LarkTransportError("websocket", "closed")
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001
                if self._ready.is_set():
                    first_connection = False
                await self._clear_connection(error)
                if self._stopping.is_set():
                    return
                if first_connection:
                    if self._startup_should_fail(error):
                        self._startup_error = error
                        self._state = "failed"
                        self._ready.set()
                        return
                    if asyncio.get_running_loop().time() >= self._startup_deadline:
                        self._startup_error = TimeoutError(
                            "Lark transport startup deadline expired"
                        )
                        self._state = "failed"
                        self._ready.set()
                        return
                    await self._wait_for_timer(
                        _delay_ms(
                            min(
                                0.25,
                                max(
                                    0.0,
                                    self._startup_deadline
                                    - asyncio.get_running_loop().time(),
                                ),
                            )
                        ),
                    )
                    continue

                self._state = "reconnecting"
                self._last_disconnect_kind = _error_kind(error)
                if _is_authentication_error(error):
                    self._state = "degraded"
                    return
                reconnect_attempts += 1
                config = (
                    self._endpoint.client_config if self._endpoint else ClientConfig()
                )
                if (
                    config.reconnect_count >= 0
                    and reconnect_attempts > config.reconnect_count
                ):
                    self._state = "degraded"
                    return
                await self._wait_for_timer(
                    _delay_ms(random.uniform(0.0, config.reconnect_nonce)),
                )
                await self._wait_for_timer(_delay_ms(config.reconnect_interval))

    async def _get_endpoint(self, first_connection: bool) -> LarkEndpoint:
        if first_connection:
            timeout = max(
                0.0, self._startup_deadline - asyncio.get_running_loop().time()
            )
        else:
            timeout = 30.0
        endpoint = await self._api.get_endpoint(timeout=timeout)
        self._endpoint = endpoint
        return endpoint

    async def _serve(self, endpoint: LarkEndpoint) -> None:
        session = self._api.session
        request = session.ws_connect(
            endpoint.url,
            autoping=True,
            autoclose=True,
            max_msg_size=MAX_FRAME_BYTES,
        )
        connection = await request
        self._connection = connection
        self._state = "connected"
        self._last_disconnect_kind = None
        self._connected_at_ms = time_ns() // 1_000_000
        self._connection_generation += 1
        if not self._ready.is_set():
            self._ready.set()

        heartbeat = asyncio.create_task(
            self._heartbeat(connection, endpoint),
            name="bcn-lark-heartbeat",
        )
        try:
            async for message in connection:
                if self._stopping.is_set():
                    return
                message_type = getattr(message, "type", None)
                if message_type in {
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSED,
                }:
                    return
                if message_type is aiohttp.WSMsgType.ERROR:
                    raise LarkTransportError("websocket", "receive_error")
                data = message.data if message_type is not None else message
                if isinstance(data, bytes):
                    await self._handle_binary(data)
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

    async def _heartbeat(self, connection: Any, endpoint: LarkEndpoint) -> None:
        while not self._stopping.is_set():
            await self._wait_for_timer(
                _delay_ms(self._current_config(endpoint).ping_interval)
            )
            if self._stopping.is_set():
                return
            frame = Frame(
                SeqID=0,
                LogID=0,
                service=endpoint.service_id,
                method=CONTROL_METHOD,
                headers=[Header(key=HEADER_TYPE, value=MESSAGE_PING)],
            )
            await self._send_frame(connection, frame)

    async def _handle_binary(self, raw: bytes) -> None:
        frame = decode_frame(raw)
        if frame.method == CONTROL_METHOD:
            await self._handle_control(frame)
        elif frame.method == DATA_METHOD:
            await self._handle_data(frame)
        else:
            raise FrameDecodeError("frame method is unsupported")

    async def _handle_control(self, frame: Frame) -> None:
        message_type = _required_header(frame.headers, HEADER_TYPE)
        if message_type == MESSAGE_PING:
            connection = self._connection
            if connection is None:
                raise LarkTransportError("pong", "connection_closed")
            await self._send_frame(
                connection,
                Frame(
                    SeqID=frame.SeqID,
                    LogID=frame.LogID,
                    service=frame.service,
                    method=CONTROL_METHOD,
                    headers=[Header(key=HEADER_TYPE, value=MESSAGE_PONG)],
                    payload=frame.payload,
                    LogIDNew=frame.LogIDNew,
                ),
            )
            return
        if message_type not in {MESSAGE_PING, MESSAGE_PONG}:
            raise FrameDecodeError("control frame type is unsupported")
        if message_type == MESSAGE_PONG and frame.payload:
            try:
                payload = json.loads(frame.payload.decode("utf-8"))
                if not isinstance(payload, Mapping):
                    raise TypeError("client config is not an object")
                config = ClientConfig.from_payload(payload)
            except (
                UnicodeDecodeError,
                json.JSONDecodeError,
                TypeError,
                ValueError,
            ) as error:
                raise FrameDecodeError(
                    "pong contains an invalid ClientConfig"
                ) from error
            endpoint = self._endpoint
            if endpoint is not None:
                self._endpoint = LarkEndpoint(
                    url=endpoint.url,
                    service_id=endpoint.service_id,
                    device_id=endpoint.device_id,
                    client_config=config,
                )

    async def _handle_data(self, frame: Frame) -> None:
        message_type = _required_header(frame.headers, HEADER_TYPE)
        if message_type not in {MESSAGE_EVENT, MESSAGE_CARD}:
            raise FrameDecodeError("data frame type is unsupported")
        message_id = _required_header(frame.headers, HEADER_MESSAGE_ID)
        total = _parse_fragment_header(frame.headers, HEADER_SUM)
        sequence = _parse_fragment_header(frame.headers, HEADER_SEQ)
        if total < 1 or total > MAX_FRAGMENT_COUNT:
            raise FrameDecodeError("fragment count is outside the allowed range")
        if sequence < 0 or sequence >= total:
            raise FrameDecodeError("fragment sequence is outside the allowed range")
        payload = frame.payload or b""
        if total > 1:
            payload = self._add_fragment(message_id, total, sequence, payload)
            if payload is None:
                return
        if len(payload) > MAX_RAW_EVENT_BYTES:
            raise FrameDecodeError("event payload exceeds the size limit")
        try:
            decoded = json.loads(payload.decode("utf-8"))
        except ValueError:
            self._message_mapping_failures += 1
            await self._send_ack(frame, code=500, started_at=monotonic())
            self._last_message_disposition = "failed"
            return
        if not isinstance(decoded, Mapping):
            self._message_mapping_failures += 1
            await self._send_ack(frame, code=500, started_at=monotonic())
            self._last_message_disposition = "failed"
            return

        started_at = monotonic()
        self._events_received += 1
        self._last_event_at_ms = time_ns() // 1_000_000
        ack_sent = False
        try:
            handler = self._on_message
            if handler is None:
                self._messages_filtered += 1
                self._last_message_disposition = "filtered"
                self._last_message_filter_reason = "handler_unavailable"
                await self._send_ack(frame, code=200, started_at=started_at)
                return

            direct_response = _is_direct_response(message_type, decoded)
            if not direct_response:
                await self._send_ack(frame, code=200, started_at=started_at)
                ack_sent = True
            result = handler(message_type, decoded, frame)
            if asyncio.iscoroutine(result) or isinstance(result, Awaitable):
                result = await result
            ack_code = 200
            ack_payload: bytes | None = None
            if isinstance(result, LarkAck):
                accepted = result.accepted
                if direct_response:
                    ack_code = result.code
                    ack_payload = result.payload
            else:
                accepted = result is not False
            if not accepted:
                self._messages_filtered += 1
                self._last_message_disposition = "filtered"
                self._last_message_filter_reason = "handler_filtered"
            else:
                self._messages_queued += 1
                self._last_message_disposition = "queued"
                self._last_message_filter_reason = None
            if direct_response:
                await self._send_ack(
                    frame,
                    code=ack_code,
                    payload=ack_payload,
                    started_at=started_at,
                )
                ack_sent = True
            if isinstance(result, LarkAck) and result.post_ack is not None:
                post_ack_task = asyncio.create_task(
                    _run_post_ack(result.post_ack),
                    name="bcn-lark-post-ack",
                )
                self._post_ack_tasks.add(post_ack_task)
                post_ack_task.add_done_callback(self._post_ack_tasks.discard)
        except Exception:  # noqa: BLE001
            self._message_mapping_failures += 1
            self._last_message_disposition = "failed"
            if not ack_sent:
                await self._send_ack(frame, code=500, started_at=started_at)

    async def _send_ack(
        self,
        frame: Frame,
        *,
        code: int,
        payload: bytes | None = None,
        started_at: float,
    ) -> None:
        headers = [
            Header(key=header.key, value=header.value) for header in frame.headers
        ]
        headers.append(
            Header(
                key=HEADER_BIZ_RT,
                value=str(max(0, int((monotonic() - started_at) * 1000))),
            )
        )
        ack = Frame(
            SeqID=frame.SeqID,
            LogID=frame.LogID,
            service=frame.service,
            method=frame.method,
            headers=headers,
            payload_encoding=frame.payload_encoding,
            payload_type=frame.payload_type,
            payload=(
                payload
                if payload is not None
                else json.dumps({"code": code}, separators=(",", ":")).encode("utf-8")
            ),
            LogIDNew=frame.LogIDNew,
        )
        connection = self._connection
        if connection is None:
            raise LarkTransportError("ack", "connection_closed")
        await self._send_frame(connection, ack)

    async def _send_frame(self, connection: Any, frame: Frame) -> None:
        encoded = encode_frame(frame)
        async with self._send_lock:
            await connection.send_bytes(encoded)

    def _add_fragment(
        self,
        message_id: str,
        total: int,
        sequence: int,
        payload: bytes,
    ) -> bytes | None:
        if total < 2 or total > MAX_FRAGMENT_COUNT:
            raise FrameDecodeError("fragment count is outside the allowed range")
        if sequence < 0 or sequence >= total:
            raise FrameDecodeError("fragment sequence is outside the allowed range")
        now = monotonic()
        self._purge_fragments(now)
        fragments = self._fragments.get(message_id)
        if fragments is None:
            if len(self._fragments) >= MAX_FRAGMENT_MESSAGES:
                self._fragments.popitem(last=False)
            fragments = _Fragments(total, {}, 0, now)
            self._fragments[message_id] = fragments
        elif fragments.total != total:
            raise FrameDecodeError("fragment count changed for a message")
        previous = fragments.parts.get(sequence)
        if previous is not None:
            fragments.size_bytes -= len(previous)
        fragments.parts[sequence] = payload
        fragments.size_bytes += len(payload)
        fragments.updated_at = now
        self._fragments.move_to_end(message_id)
        if fragments.size_bytes > MAX_FRAME_BYTES:
            self._fragments.pop(message_id, None)
            raise FrameDecodeError("fragmented message exceeds the size limit")
        if len(fragments.parts) != total:
            return None
        self._fragments.pop(message_id, None)
        return b"".join(fragments.parts[index] for index in range(total))

    def _purge_fragments(self, now: float) -> None:
        expired = [
            message_id
            for message_id, fragments in self._fragments.items()
            if now - fragments.updated_at > MAX_FRAGMENT_AGE_SECONDS
        ]
        for message_id in expired:
            self._fragments.pop(message_id, None)

    def _current_config(self, endpoint: LarkEndpoint) -> ClientConfig:
        current = self._endpoint
        return current.client_config if current is not None else endpoint.client_config

    async def _clear_connection(self, error: Exception) -> None:
        connection = self._connection
        self._connection = None
        if connection is not None:
            await _close_connection(connection)
        self._last_disconnect_kind = _error_kind(error)

    def _startup_should_fail(self, error: Exception) -> bool:
        return isinstance(
            error, (LarkApiError, ValueError)
        ) or _is_authentication_error(error)

    async def _wait_for_timer(self, delay_ms: int) -> None:
        timer = self._timer_wheel.create(delay_ms)
        await timer.wait()


def _is_direct_response(
    message_type: str,
    payload: Mapping[str, object],
) -> bool:
    if message_type == MESSAGE_CARD:
        return True
    header = payload.get("header")
    if not isinstance(header, Mapping):
        return False
    return header.get("event_type") in {
        "card.action.trigger",
        "p2.card.action.trigger",
    }


def _delay_ms(seconds: float) -> int:
    return math.ceil(max(0.0, seconds) * 1_000)


def _required_header(headers: list[Header], key: str) -> str:
    values = header_values(headers, key)
    if len(values) != 1 or not values[0]:
        raise FrameDecodeError(f"frame is missing a unique {key} header")
    return values[0]


def _parse_fragment_header(headers: list[Header], key: str) -> int:
    values = header_values(headers, key)
    if len(values) != 1:
        raise FrameDecodeError(f"frame contains duplicate {key} headers")
    try:
        value = int(values[0])
    except ValueError:
        raise FrameDecodeError(f"frame {key} header is not an integer") from None
    return value


def _is_authentication_error(error: Exception) -> bool:
    if not isinstance(error, LarkApiError):
        return False
    return error.http_status in {401, 403} or error.provider_code in {
        403,
        514,
        1000040344,
        1000040350,
    }


def _error_kind(error: BaseException) -> str:
    if isinstance(error, LarkApiError):
        return f"provider_{error.provider_code or error.http_status}"
    if isinstance(error, LarkTransportError):
        return error.error_kind
    if isinstance(error, FrameDecodeError):
        return "frame_decode"
    return type(error).__name__


async def _run_post_ack(callback: Callable[[], Awaitable[None]]) -> None:
    try:
        await callback()
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        return


async def _close_connection(connection: Any) -> None:
    try:
        result = connection.close()
        if asyncio.iscoroutine(result) or isinstance(result, Awaitable):
            await result
    except aiohttp.ClientError:
        return
    except OSError:
        return


__all__ = [
    "CONTROL_METHOD",
    "DATA_METHOD",
    "HEADER_BIZ_RT",
    "HEADER_MESSAGE_ID",
    "HEADER_SEQ",
    "HEADER_SUM",
    "HEADER_TRACE_ID",
    "HEADER_TYPE",
    "MAX_FRAGMENT_AGE_SECONDS",
    "MAX_FRAGMENT_COUNT",
    "MAX_FRAGMENT_MESSAGES",
    "MAX_RAW_EVENTS",
    "MESSAGE_CARD",
    "MESSAGE_EVENT",
    "MESSAGE_PING",
    "MESSAGE_PONG",
    "LarkAck",
    "LarkTransport",
]
