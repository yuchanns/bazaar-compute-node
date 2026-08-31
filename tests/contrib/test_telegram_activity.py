from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

import bazaar_compute_node.contrib.telegram.api as telegram_api_module
from bazaar_compute_node.app.attachments import AttachmentMaterializer
from bazaar_compute_node.contrib.telegram.activity import (
    MAX_RICH_MARKDOWN_BYTES,
    TelegramActivityProjector,
)
from bazaar_compute_node.contrib.telegram.api import TelegramBotApi
from bazaar_compute_node.contrib.telegram.identity import TelegramThreadIdentity
from bazaar_compute_node.contrib.telegram.outbound import TelegramOutboundChannel
from bazaar_compute_node.core.channel import ChannelContext
from bazaar_compute_node.core.models import (
    ContentDelta,
    ContentDeltaKind,
    ContextCompactionCompleted,
    ContextCompactionStarted,
    RuntimeEventEnvelope,
    RuntimeEventPayload,
    RuntimeOutputEvent,
    ToolCall,
    ToolCallCompleted,
    ToolCallStarted,
    TurnCompleted,
)
from bazaar_compute_node.core.timerwheel import TimerWheel
from bazaar_compute_node.i18n import ENGLISH, SIMPLIFIED_CHINESE, create_translator

TEST_BOT_ID = 1_000_000_001
TEST_CHAT_ID = -1_000_000_004
TEST_TOPIC_ID = 23
TEST_SESSION_ID = "session-1"


def _event(
    payload: RuntimeEventPayload, *, turn_id: str = "turn-1"
) -> RuntimeOutputEvent:
    return RuntimeOutputEvent(
        envelope=RuntimeEventEnvelope(
            session_id=TEST_SESSION_ID,
            runtime_session_id="runtime-1",
            turn_id=turn_id,
            provider_turn_id="provider-turn-1",
            occurred_at_ms=1,
        ),
        payload=payload,
    )


async def _wait_for_requests(
    requests: list[tuple[str, Mapping[str, Any]]],
    count: int,
    *,
    timeout: float = 2,
) -> None:
    async with asyncio.timeout(timeout):
        while len(requests) < count:
            await asyncio.sleep(0)
    await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_telegram_activity_is_lazy_debounced_and_updates_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[tuple[str, Mapping[str, Any]]] = []

    async def telegram(request: web.Request) -> web.Response:
        method = request.match_info["method"]
        payload = await request.json()
        assert isinstance(payload, Mapping)
        requests.append((method, payload))
        return web.json_response(
            {"ok": True, "result": {"message_id": payload.get("message_id", 101)}}
        )

    application = web.Application()
    application.router.add_post("/bottoken/{method}", telegram)
    server = TestServer(application)
    await server.start_server()
    monkeypatch.setattr(
        telegram_api_module,
        "_API_BASE_URL",
        str(server.make_url("/")).rstrip("/"),
    )
    identity = TelegramThreadIdentity(
        bot_id=TEST_BOT_ID,
        chat_id=TEST_CHAT_ID,
        topic_id=TEST_TOPIC_ID,
    )

    async def referenced_paths() -> set[str]:
        return set()

    channel = TelegramOutboundChannel(
        ChannelContext(
            agent_id="agent-test",
            attachments=AttachmentMaterializer(lambda: tmp_path, referenced_paths),
            options={},
            workspace=lambda: tmp_path,
        ),
        token="token",
    )
    projector = channel._activity
    try:
        async with aiohttp.ClientSession() as session:
            api = TelegramBotApi(session, token="token")
            channel._api = api
            channel._session = session
            channel._bot_id = TEST_BOT_ID
            channel._stream_routes[TEST_SESSION_ID] = identity
            channel.accept_turn_event(
                _event(
                    ContentDelta(ContentDeltaKind.AGENT_MESSAGE, "hello"),
                    turn_id="turn-0",
                ),
                session_id=TEST_SESSION_ID,
            )
            channel.accept_turn_event(
                _event(TurnCompleted("turn.completed"), turn_id="turn-0"),
                session_id=TEST_SESSION_ID,
            )
            await asyncio.sleep(0.05)
            assert requests == []

            channel.accept_turn_event(
                _event(
                    ToolCallStarted(
                        ToolCall(
                            call_id="call-1",
                            name="shell",
                            input={
                                "token": "plain-secret",
                                "argument": "sk-secretvalue",
                                "long": "word " * 200,
                            },
                        )
                    )
                ),
                session_id=TEST_SESSION_ID,
            )
            await _wait_for_requests(requests, 1)
            method, payload = requests[0]
            markdown = payload["rich_message"]["markdown"]
            assert method == "sendRichMessage"
            assert payload["message_thread_id"] == TEST_TOPIC_ID
            assert isinstance(markdown, str)
            assert "Activity" in markdown
            assert "Tool call" in markdown
            assert "shell" in markdown

            channel.accept_turn_event(
                _event(
                    ToolCallCompleted(
                        ToolCall(call_id="call-1", name="shell", output="done")
                    )
                ),
                session_id=TEST_SESSION_ID,
            )
            await _wait_for_requests(requests, 2)
            await asyncio.sleep(0.05)
            assert [method for method, _ in requests] == [
                "sendRichMessage",
                "editMessageText",
            ]
            final_markdown = requests[-1][1]["rich_message"]["markdown"]
            assert isinstance(final_markdown, str)
            assert "✅" in final_markdown
            assert "Tool call" in final_markdown
            assert "shell" in final_markdown
            await channel.stop(timeout=1)
            assert projector.active_turns == 0
            assert projector.tasks_pending == 0
    finally:
        await projector.close()
        await server.close()


@pytest.mark.asyncio
async def test_telegram_activity_localizes_compaction_events_without_pairing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[tuple[str, Mapping[str, Any]]] = []

    async def telegram(request: web.Request) -> web.Response:
        method = request.match_info["method"]
        payload = await request.json()
        assert isinstance(payload, Mapping)
        requests.append((method, payload))
        return web.json_response(
            {"ok": True, "result": {"message_id": payload.get("message_id", 151)}}
        )

    application = web.Application()
    application.router.add_post("/bottoken/{method}", telegram)
    server = TestServer(application)
    await server.start_server()
    monkeypatch.setattr(
        telegram_api_module,
        "_API_BASE_URL",
        str(server.make_url("/")).rstrip("/"),
    )
    projector = TelegramActivityProjector(
        timer_wheel=None,
        translator=create_translator(SIMPLIFIED_CHINESE),
    )
    identity = TelegramThreadIdentity(TEST_BOT_ID, TEST_CHAT_ID, 0)
    try:
        async with aiohttp.ClientSession() as session:
            api = TelegramBotApi(session, token="token")
            projector.accept(
                _event(ContextCompactionStarted("compaction-1")),
                identity=identity,
                api=api,
            )
            await _wait_for_requests(requests, 1)
            markdown = requests[-1][1]["rich_message"]["markdown"]
            assert "活动" in markdown
            assert "上下文压缩" in markdown

            projector.accept(
                _event(ContextCompactionCompleted("compaction-1")),
                identity=identity,
                api=api,
            )
            projector.accept(
                _event(TurnCompleted("turn.completed")),
                identity=identity,
                api=api,
            )
            await _wait_for_requests(requests, 2, timeout=0.5)
            markdown = requests[-1][1]["rich_message"]["markdown"]
            assert "✅" in markdown
            assert "上下文压缩" in markdown

            projector.accept(
                _event(ContextCompactionCompleted(), turn_id="turn-2"),
                identity=identity,
                api=api,
            )
            await _wait_for_requests(requests, 3)
            markdown = requests[-1][1]["rich_message"]["markdown"]
            assert "✅" in markdown
            assert "上下文压缩" in markdown
    finally:
        await projector.close()
        await server.close()


@pytest.mark.asyncio
async def test_telegram_activity_terminal_flushes_without_debounce_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[tuple[str, Mapping[str, Any]]] = []

    async def telegram(request: web.Request) -> web.Response:
        method = request.match_info["method"]
        payload = await request.json()
        assert isinstance(payload, Mapping)
        requests.append((method, payload))
        return web.json_response(
            {"ok": True, "result": {"message_id": payload.get("message_id", 201)}}
        )

    application = web.Application()
    application.router.add_post("/bottoken/{method}", telegram)
    server = TestServer(application)
    await server.start_server()
    monkeypatch.setattr(
        telegram_api_module,
        "_API_BASE_URL",
        str(server.make_url("/")).rstrip("/"),
    )
    projector = TelegramActivityProjector(
        timer_wheel=None,
        translator=create_translator(ENGLISH),
    )
    identity = TelegramThreadIdentity(TEST_BOT_ID, TEST_CHAT_ID, 0)
    try:
        async with aiohttp.ClientSession() as session:
            api = TelegramBotApi(session, token="token")
            projector.accept(
                _event(ToolCallStarted(ToolCall("call-1", "shell"))),
                identity=identity,
                api=api,
            )
            await _wait_for_requests(requests, 1)
            projector.accept(
                _event(ToolCallCompleted(ToolCall("call-1", "shell"))),
                identity=identity,
                api=api,
            )
            projector.accept(
                _event(TurnCompleted("turn.completed")),
                identity=identity,
                api=api,
            )
            await _wait_for_requests(requests, 2, timeout=0.5)
            assert requests[-1][0] == "editMessageText"
            async with asyncio.timeout(0.5):
                while projector.active_turns:
                    await asyncio.sleep(0)
    finally:
        await projector.close()
        await server.close()


@pytest.mark.asyncio
async def test_telegram_activity_continues_and_updates_an_older_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[tuple[str, Mapping[str, Any]]] = []
    next_message_id = 300

    async def telegram(request: web.Request) -> web.Response:
        nonlocal next_message_id
        method = request.match_info["method"]
        payload = await request.json()
        assert isinstance(payload, Mapping)
        requests.append((method, payload))
        if method == "sendRichMessage":
            next_message_id += 1
            message_id = next_message_id
        else:
            message_id = payload["message_id"]
        return web.json_response({"ok": True, "result": {"message_id": message_id}})

    application = web.Application()
    application.router.add_post("/bottoken/{method}", telegram)
    server = TestServer(application)
    await server.start_server()
    monkeypatch.setattr(
        telegram_api_module,
        "_API_BASE_URL",
        str(server.make_url("/")).rstrip("/"),
    )
    projector = TelegramActivityProjector(
        timer_wheel=None,
        translator=create_translator(ENGLISH),
    )
    identity = TelegramThreadIdentity(TEST_BOT_ID, TEST_CHAT_ID, 0)
    try:
        async with aiohttp.ClientSession() as session:
            api = TelegramBotApi(session, token="token")
            projector.accept(
                _event(ToolCallStarted(ToolCall("call-0", "shell"))),
                identity=identity,
                api=api,
            )
            await _wait_for_requests(requests, 1)
            first_message_id = requests[0][1].get("message_id", 301)
            for index in range(1, 128):
                projector.accept(
                    _event(ToolCallStarted(ToolCall(f"call-{index}", "shell"))),
                    identity=identity,
                    api=api,
                )
            await _wait_for_requests(requests, 3)
            sent = [
                payload for method, payload in requests if method == "sendRichMessage"
            ]
            assert len(sent) == 2
            assert all(
                len(payload["rich_message"]["markdown"].encode("utf-8"))
                <= MAX_RICH_MARKDOWN_BYTES
                for payload in sent
            )

            projector.accept(
                _event(ToolCallCompleted(ToolCall("call-0", "shell"))),
                identity=identity,
                api=api,
            )
            await _wait_for_requests(requests, 4)
            method, payload = requests[-1]
            assert method == "editMessageText"
            assert payload["message_id"] == first_message_id
            assert "Tool call" in payload["rich_message"]["markdown"]
    finally:
        await projector.close()
        await server.close()


@pytest.mark.asyncio
async def test_telegram_activity_retries_rate_limited_updates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = 0

    async def telegram(request: web.Request) -> web.Response:
        nonlocal requests
        await request.json()
        requests += 1
        if requests == 1:
            return web.json_response(
                {
                    "ok": False,
                    "error_code": 429,
                    "description": "Too Many Requests",
                    "parameters": {"retry_after": 0},
                },
                status=429,
            )
        return web.json_response({"ok": True, "result": {"message_id": 401}})

    application = web.Application()
    application.router.add_post("/bottoken/{method}", telegram)
    server = TestServer(application)
    await server.start_server()
    monkeypatch.setattr(
        telegram_api_module,
        "_API_BASE_URL",
        str(server.make_url("/")).rstrip("/"),
    )
    timer_wheel = TimerWheel()
    await timer_wheel.start()
    projector = TelegramActivityProjector(
        timer_wheel=timer_wheel,
        translator=create_translator(ENGLISH),
    )
    identity = TelegramThreadIdentity(TEST_BOT_ID, TEST_CHAT_ID, 0)
    try:
        async with aiohttp.ClientSession() as session:
            projector.accept(
                _event(ToolCallStarted(ToolCall("call-1", "shell"))),
                identity=identity,
                api=TelegramBotApi(session, token="token"),
            )
            async with asyncio.timeout(1):
                while projector.messages_sent == 0:
                    await asyncio.sleep(0)
            assert requests == 2
            assert projector.rate_limit_retries == 1
    finally:
        await projector.close()
        await timer_wheel.close()
        await server.close()
