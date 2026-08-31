from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from bazaar_compute_node.contrib.lark.api import (
    ClientConfig,
    LarkApi,
    LarkApiError,
    LarkEndpoint,
)
from bazaar_compute_node.contrib.lark.frame import (
    Frame,
    Header,
    decode_frame,
    encode_frame,
)
from bazaar_compute_node.contrib.lark.transport import (
    DATA_METHOD,
    HEADER_MESSAGE_ID,
    HEADER_SEQ,
    HEADER_SUM,
    HEADER_TYPE,
    MESSAGE_CARD,
    TRANSPORT_HEARTBEAT_SECONDS,
    LarkAck,
    LarkTransport,
)
from bazaar_compute_node.core.timerwheel import TimerWheel


def _card_frame(message_id: str) -> bytes:
    return encode_frame(
        Frame(
            SeqID=1,
            LogID=2,
            service=3,
            method=DATA_METHOD,
            headers=[
                Header(key=HEADER_TYPE, value=MESSAGE_CARD),
                Header(key=HEADER_MESSAGE_ID, value=message_id),
                Header(key=HEADER_SUM, value="1"),
                Header(key=HEADER_SEQ, value="0"),
            ],
            payload=json.dumps({"session": message_id}).encode(),
        )
    )


def _message_id(frame: Frame) -> str:
    return next(
        header.value for header in frame.headers if header.key == HEADER_MESSAGE_ID
    )


@pytest.mark.asyncio
async def test_lark_cardkit_requests_and_card_reference_reply() -> None:
    requests: list[tuple[str, str, Mapping[str, object], str | None]] = []
    rate_limited = False

    async def open_api(request: web.Request) -> web.Response:
        nonlocal rate_limited
        payload = await request.json()
        assert isinstance(payload, Mapping)
        requests.append(
            (
                request.method,
                request.path,
                payload,
                request.headers.get("Authorization"),
            )
        )
        if request.path.endswith("tenant_access_token/internal"):
            return web.json_response(
                {"code": 0, "tenant_access_token": "tenant-token", "expire": 3600}
            )
        if request.path == "/open-apis/cardkit/v1/cards":
            if rate_limited:
                return web.json_response(
                    {"code": 230020, "msg": "rate limited"}, status=429
                )
            return web.json_response({"code": 0, "data": {"card_id": "card-1"}})
        if request.path.endswith("/reply"):
            return web.json_response({"code": 0, "data": {"message_id": "message-1"}})
        return web.json_response({"code": 0, "data": {}})

    application = web.Application()
    application.router.add_route("*", "/{path:.*}", open_api)
    server = TestServer(application)
    timer_wheel = TimerWheel()
    await server.start_server()
    await timer_wheel.start()
    try:
        async with aiohttp.ClientSession() as session:
            api = LarkApi(
                session,
                app_id="app-id",
                app_secret="app-secret",
                base_url=str(server.make_url("/")).rstrip("/"),
                timer_wheel=timer_wheel,
            )
            card_id = await api.create_card(
                {"schema": "2.0", "body": {"elements": []}}, timeout=1
            )
            await api.add_card_elements(
                card_id,
                [{"tag": "markdown", "content": "开始", "element_id": "i000001"}],
                uuid="add-uuid",
                sequence=1,
                timeout=1,
            )
            await api.update_card_element(
                card_id,
                "i000001",
                {"tag": "markdown", "content": "完成", "element_id": "i000001"},
                uuid="update-uuid",
                sequence=2,
                timeout=1,
            )
            message_id = await api.reply_card(
                message_id="trigger-message",
                card_id=card_id,
                reply_in_thread=True,
                uuid="reply-uuid",
                timeout=1,
            )

            assert card_id == "card-1"
            assert message_id == "message-1"
            cardkit_requests = [
                request for request in requests if "cardkit" in request[1]
            ]
            assert [request[:2] for request in cardkit_requests] == [
                ("POST", "/open-apis/cardkit/v1/cards"),
                ("POST", "/open-apis/cardkit/v1/cards/card-1/elements"),
                ("PUT", "/open-apis/cardkit/v1/cards/card-1/elements/i000001"),
            ]
            assert json.loads(str(cardkit_requests[0][2]["data"])) == {
                "schema": "2.0",
                "body": {"elements": []},
            }
            assert cardkit_requests[1][2]["type"] == "append"
            assert cardkit_requests[1][2]["uuid"] == "add-uuid"
            assert cardkit_requests[1][2]["sequence"] == 1
            assert (
                json.loads(str(cardkit_requests[1][2]["elements"]))[0]["element_id"]
                == "i000001"
            )
            assert cardkit_requests[2][2]["uuid"] == "update-uuid"
            assert cardkit_requests[2][2]["sequence"] == 2
            assert json.loads(str(cardkit_requests[2][2]["element"]))["content"] == (
                "完成"
            )
            reply = next(
                request for request in requests if request[1].endswith("/reply")
            )
            assert reply[2]["msg_type"] == "interactive"
            assert json.loads(str(reply[2]["content"])) == {
                "type": "card",
                "data": {"card_id": "card-1"},
            }
            assert reply[2]["reply_in_thread"] is True
            assert reply[2]["uuid"] == "reply-uuid"
            assert all(request[3] == "Bearer tenant-token" for request in requests[1:])

            rate_limited = True
            with pytest.raises(LarkApiError) as captured:
                await api.create_card({"schema": "2.0"}, timeout=1)
            assert captured.value.http_status == 429
            assert captured.value.provider_code == 230020
            await api.stop()
    finally:
        await timer_wheel.close()
        await server.close()


@pytest.mark.asyncio
async def test_lark_transport_preserves_inbound_order_without_waiting_for_post_ack() -> (
    None
):
    release_first = asyncio.Event()
    first_started = asyncio.Event()
    second_started = asyncio.Event()
    post_ack_started = asyncio.Event()
    release_post_ack = asyncio.Event()
    fourth_started = asyncio.Event()
    acknowledgements = {
        message_id: asyncio.Event()
        for message_id in ("first", "second", "post", "fourth")
    }
    all_acknowledged = asyncio.Event()
    release_server = asyncio.Event()

    async def websocket(request: web.Request) -> web.WebSocketResponse:
        connection = web.WebSocketResponse()
        await connection.prepare(request)
        for message_id in acknowledgements:
            await connection.send_bytes(_card_frame(message_id))
        async for message in connection:
            if message.type is not aiohttp.WSMsgType.BINARY:
                continue
            message_id = _message_id(decode_frame(message.data))
            acknowledgements[message_id].set()
            if all(event.is_set() for event in acknowledgements.values()):
                all_acknowledged.set()
                await release_server.wait()
                await connection.close()
        return connection

    async def handler(
        message_type: str,
        payload: Mapping[str, object],
        frame: Frame,
    ) -> LarkAck:
        del message_type, payload
        message_id = _message_id(frame)
        if message_id == "first":
            first_started.set()
            await release_first.wait()
        elif message_id == "second":
            second_started.set()
        elif message_id == "post":

            async def post_ack() -> None:
                await acknowledgements["post"].wait()
                post_ack_started.set()
                await release_post_ack.wait()

            return LarkAck(post_ack=post_ack)
        elif message_id == "fourth":
            fourth_started.set()
        return LarkAck()

    application = web.Application()
    application.router.add_get("/ws", websocket)
    server = TestServer(application)
    timer_wheel = TimerWheel()
    await server.start_server()
    await timer_wheel.start()
    try:
        async with aiohttp.ClientSession() as session:
            api = LarkApi(
                session,
                app_id="app-id",
                app_secret="app-secret",
                base_url=str(server.make_url("/")).rstrip("/"),
                timer_wheel=timer_wheel,
            )
            transport = LarkTransport(api, timer_wheel=timer_wheel, on_message=handler)
            endpoint = LarkEndpoint(
                url=str(server.make_url("/ws")),
                service_id=3,
                device_id="device-1",
                client_config=ClientConfig(),
            )
            serve = asyncio.create_task(transport._serve(endpoint))
            await asyncio.wait_for(first_started.wait(), timeout=1)
            await asyncio.sleep(0.02)
            assert not second_started.is_set()
            release_first.set()
            await asyncio.wait_for(second_started.wait(), timeout=1)
            await asyncio.wait_for(post_ack_started.wait(), timeout=1)
            await asyncio.wait_for(fourth_started.wait(), timeout=1)
            await asyncio.wait_for(all_acknowledged.wait(), timeout=1)
            connection = transport._connection
            assert connection is not None
            assert connection._heartbeat == TRANSPORT_HEARTBEAT_SECONDS
            release_post_ack.set()
            release_server.set()
            await asyncio.wait_for(serve, timeout=1)
            assert transport.health["connection_tasks"] == 0
    finally:
        release_first.set()
        release_post_ack.set()
        release_server.set()
        await timer_wheel.close()
        await server.close()


@pytest.mark.asyncio
async def test_lark_transport_reconnect_and_stop_cancel_connection_tasks() -> None:
    connection_number = 0
    reconnect_post_started = asyncio.Event()
    reconnect_post_cancelled = asyncio.Event()
    stop_post_started = asyncio.Event()
    stop_post_cancelled = asyncio.Event()

    async def websocket(request: web.Request) -> web.WebSocketResponse:
        nonlocal connection_number
        connection_number += 1
        message_id = ("reconnect", "clean", "stop")[connection_number - 1]
        connection = web.WebSocketResponse()
        await connection.prepare(request)
        await connection.send_bytes(_card_frame(message_id))
        await connection.receive_bytes()
        if message_id == "reconnect":
            await reconnect_post_started.wait()
        if message_id != "stop":
            await connection.close()
        else:
            await connection.receive()
        return connection

    async def handler(
        message_type: str,
        payload: Mapping[str, object],
        frame: Frame,
    ) -> LarkAck:
        del message_type, payload
        message_id = _message_id(frame)
        if message_id == "reconnect":

            async def post_ack() -> None:
                reconnect_post_started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    reconnect_post_cancelled.set()

            return LarkAck(post_ack=post_ack)
        if message_id == "stop":

            async def post_ack() -> None:
                stop_post_started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    stop_post_cancelled.set()

            return LarkAck(post_ack=post_ack)
        return LarkAck()

    application = web.Application()
    application.router.add_get("/ws", websocket)
    server = TestServer(application)
    timer_wheel = TimerWheel()
    await server.start_server()
    await timer_wheel.start()
    try:
        async with aiohttp.ClientSession() as session:
            api = LarkApi(
                session,
                app_id="app-id",
                app_secret="app-secret",
                base_url=str(server.make_url("/")).rstrip("/"),
                timer_wheel=timer_wheel,
            )
            transport = LarkTransport(api, timer_wheel=timer_wheel, on_message=handler)
            endpoint = LarkEndpoint(
                url=str(server.make_url("/ws")),
                service_id=3,
                device_id="device-1",
                client_config=ClientConfig(),
            )
            await asyncio.wait_for(transport._serve(endpoint), timeout=1)
            await asyncio.wait_for(reconnect_post_cancelled.wait(), timeout=1)
            assert reconnect_post_started.is_set()
            assert transport.health["connection_tasks"] == 0

            await asyncio.wait_for(transport._serve(endpoint), timeout=1)
            assert transport.health["connection_tasks"] == 0

            serve = asyncio.create_task(transport._serve(endpoint))
            transport._runner = serve
            await asyncio.wait_for(stop_post_started.wait(), timeout=1)
            await transport.stop(timeout=1)
            await asyncio.wait_for(stop_post_cancelled.wait(), timeout=1)
            assert transport.health["connection_tasks"] == 0
    finally:
        await timer_wheel.close()
        await server.close()
