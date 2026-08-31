from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from bazaar_compute_node.app.attachments import AttachmentMaterializer
from bazaar_compute_node.contrib.lark.activity import (
    LarkActivityProjector,
    LarkActivityRoute,
)
from bazaar_compute_node.contrib.lark.api import LarkApi
from bazaar_compute_node.contrib.lark.channel import LarkChannel
from bazaar_compute_node.contrib.lark.identity import (
    LarkBotIdentity,
    LarkThreadIdentity,
)
from bazaar_compute_node.contrib.lark.transport import LarkTransport
from bazaar_compute_node.core.channel import ChannelContext, ChannelSendRequest
from bazaar_compute_node.core.models import (
    ChannelTargetKind,
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
from bazaar_compute_node.core.outcomes import ProviderCallStatus
from bazaar_compute_node.core.timerwheel import TimerWheel
from bazaar_compute_node.i18n import ENGLISH, create_translator

TEST_SESSION_ID = "session-1"


@dataclass(slots=True)
class _OpenApiState:
    requests: list[tuple[str, str, Mapping[str, object]]] = field(default_factory=list)
    card_count: int = 0
    rate_limit_adds: int = 0
    reject_adds: bool = False


@asynccontextmanager
async def _open_api(
    state: _OpenApiState,
) -> AsyncIterator[tuple[LarkApi, TimerWheel, str]]:
    async def handler(request: web.Request) -> web.Response:
        payload = await request.json()
        assert isinstance(payload, Mapping)
        state.requests.append((request.method, request.path, payload))
        if request.path.endswith("tenant_access_token/internal"):
            return web.json_response(
                {"code": 0, "tenant_access_token": "token", "expire": 3600}
            )
        if request.path == "/open-apis/cardkit/v1/cards":
            state.card_count += 1
            return web.json_response(
                {"code": 0, "data": {"card_id": f"card-{state.card_count}"}}
            )
        if request.path.endswith("/reply"):
            return web.json_response(
                {"code": 0, "data": {"message_id": f"message-{state.card_count}"}}
            )
        if request.method == "POST" and request.path.endswith("/elements"):
            if state.reject_adds:
                return web.json_response(
                    {"code": 230001, "msg": "rejected"}, status=400
                )
            if state.rate_limit_adds:
                state.rate_limit_adds -= 1
                return web.json_response(
                    {"code": 230020, "msg": "rate limited"}, status=429
                )
        if request.path == "/open-apis/im/v1/messages":
            return web.json_response({"code": 0, "data": {"message_id": "final-1"}})
        return web.json_response({"code": 0, "data": {}})

    application = web.Application()
    application.router.add_route("*", "/{path:.*}", handler)
    server = TestServer(application)
    timer_wheel = TimerWheel()
    await server.start_server()
    await timer_wheel.start()
    session = aiohttp.ClientSession()
    api = LarkApi(
        session,
        app_id="app-id",
        app_secret="app-secret",
        base_url=str(server.make_url("/")).rstrip("/"),
        timer_wheel=timer_wheel,
    )
    try:
        yield api, timer_wheel, str(server.make_url("/")).rstrip("/")
    finally:
        await api.stop()
        await session.close()
        await timer_wheel.close()
        await server.close()


def _event(
    payload: RuntimeEventPayload, *, turn_id: str = "turn-1"
) -> RuntimeOutputEvent:
    return RuntimeOutputEvent(
        envelope=RuntimeEventEnvelope(
            session_id=TEST_SESSION_ID,
            runtime_session_id="runtime-1",
            turn_id=turn_id,
            provider_turn_id=None,
            occurred_at_ms=1,
        ),
        payload=payload,
    )


@pytest.mark.asyncio
async def test_lark_activity_disabled_does_not_project_turn_events(
    tmp_path: Path,
) -> None:
    state = _OpenApiState()
    async with _open_api(state) as (api, timer_wheel, base_url):

        async def referenced_paths() -> set[str]:
            return set()

        channel = LarkChannel(
            ChannelContext(
                agent_id="agent-test",
                attachments=AttachmentMaterializer(lambda: tmp_path, referenced_paths),
                options={},
                workspace=lambda: tmp_path,
                translator=create_translator(ENGLISH),
            ),
            app_id="app-id",
            app_secret="app-secret",
            region="feishu",
            base_url=base_url,
            timer_wheel=timer_wheel,
        )
        channel._api = api
        channel._stream_routes[TEST_SESSION_ID] = "trigger-1"

        channel.accept_turn_event(
            _event(ToolCallStarted(ToolCall("call-1", "shell"))),
            session_id=TEST_SESSION_ID,
        )
        await asyncio.sleep(0)

        assert channel.health["activity_enabled"] is False
        assert channel.health["activity_turns"] == 0
        assert channel.health["activity_tasks_pending"] == 0
        assert state.requests == []


def _projector(
    timer_wheel: TimerWheel,
    degraded: set[str],
) -> LarkActivityProjector:
    return LarkActivityProjector(
        timer_wheel=timer_wheel,
        translator=create_translator(ENGLISH),
        report_degraded=degraded.add,
    )


def _accept(
    projector: LarkActivityProjector,
    api: LarkApi,
    payload: RuntimeEventPayload,
) -> None:
    projector.accept(
        _event(payload),
        route=LarkActivityRoute("trigger-1", True),
        api=api,
    )


@pytest.mark.asyncio
async def test_lark_activity_is_lazy_and_consumes_unpaired_compaction() -> None:
    state = _OpenApiState()
    async with _open_api(state) as (api, timer_wheel, _):
        projector = _projector(timer_wheel, set())
        _accept(projector, api, ContextCompactionCompleted("compact-1"))
        _accept(projector, api, TurnCompleted("turn.completed"))
        await projector.wait_terminal(TEST_SESSION_ID, timeout=1)

        assert state.requests == []

        _accept(projector, api, ContextCompactionStarted("compact-2"))
        _accept(projector, api, ToolCallStarted(ToolCall("call-1", "shell")))
        _accept(projector, api, TurnCompleted("turn.completed"))
        await projector.wait_terminal(TEST_SESSION_ID, timeout=2)

        additions = [
            json.loads(str(payload["elements"]))[0]
            for method, path, payload in state.requests
            if method == "POST" and path.endswith("/elements")
        ]
        assert "Context compaction" in additions[0]["content"]
        assert "⌛️" in additions[0]["content"]
        assert "Tool call" in additions[1]["content"]
        assert "shell" in additions[1]["content"]

        _accept(
            projector,
            api,
            ToolCallCompleted(ToolCall("call-2", "reader", output="done")),
        )
        _accept(projector, api, TurnCompleted("turn.completed"))
        await projector.wait_terminal(TEST_SESSION_ID, timeout=2)

        last_addition = json.loads(
            str(
                next(
                    payload["elements"]
                    for method, path, payload in reversed(state.requests)
                    if method == "POST" and path.endswith("/elements")
                )
            )
        )[0]
        assert "reader" in last_addition["content"]
        assert "Tool call" in last_addition["content"]
        assert projector.active_turns == 0


@pytest.mark.asyncio
async def test_lark_activity_serializes_updates_and_reuses_retry_identity() -> None:
    state = _OpenApiState(rate_limit_adds=2)
    async with _open_api(state) as (api, timer_wheel, _):
        projector = _projector(timer_wheel, set())
        _accept(
            projector,
            api,
            ToolCallStarted(ToolCall("call-1", "shell")),
        )
        _accept(
            projector,
            api,
            ToolCallStarted(ToolCall("call-1", "shell")),
        )
        _accept(
            projector,
            api,
            ToolCallCompleted(ToolCall("call-1", "shell")),
        )
        _accept(projector, api, TurnCompleted("turn.completed"))
        await projector.wait_terminal(TEST_SESSION_ID, timeout=4)

        operations = [
            payload
            for _, path, payload in state.requests
            if "cardkit" in path and path != "/open-apis/cardkit/v1/cards"
        ]
        sequences = [payload["sequence"] for payload in operations]
        assert all(isinstance(sequence, int) for sequence in sequences)
        assert sequences == [1, 1, 1, 2, 3]
        assert len({str(payload["uuid"]) for payload in operations[:3]}) == 1
        assert str(operations[3]["uuid"]) != str(operations[4]["uuid"])
        rendered = "\n".join(
            str(payload.get("elements", payload.get("element", "")))
            for payload in operations
        )
        assert "shell" in rendered
        assert "Tool call" in rendered
        assert projector.rate_limit_retries == 2


@pytest.mark.asyncio
async def test_lark_activity_marks_retry_exhaustion_on_the_card() -> None:
    state = _OpenApiState(rate_limit_adds=4)
    degraded: set[str] = set()
    async with _open_api(state) as (api, timer_wheel, _):
        projector = _projector(timer_wheel, degraded)
        _accept(projector, api, ToolCallStarted(ToolCall("call-1", "shell")))
        _accept(projector, api, TurnCompleted("turn.completed"))
        await projector.wait_terminal(TEST_SESSION_ID, timeout=4)

        additions = [
            payload
            for method, path, payload in state.requests
            if method == "POST" and path.endswith("/elements")
        ]
        assert [payload["sequence"] for payload in additions] == [1, 1, 1, 1, 1]
        assert len({str(payload["uuid"]) for payload in additions[:4]}) == 1
        assert additions[4]["uuid"] != additions[3]["uuid"]
        incomplete = json.loads(str(additions[4]["elements"]))[0]
        assert "may be incomplete" in incomplete["content"]
        assert degraded == set()


@pytest.mark.asyncio
async def test_lark_activity_reports_a_queue_overflow_without_a_writable_card() -> None:
    state = _OpenApiState()
    degraded: set[str] = set()
    async with _open_api(state) as (api, timer_wheel, _):
        projector = _projector(timer_wheel, degraded)
        for index in range(1025):
            _accept(
                projector,
                api,
                ContextCompactionStarted(f"compact-{index}"),
            )
        _accept(projector, api, TurnCompleted("turn.completed"))
        await projector.wait_terminal(TEST_SESSION_ID, timeout=2)

        assert state.requests == []
        assert degraded == {TEST_SESSION_ID}


@pytest.mark.asyncio
async def test_lark_activity_continues_and_updates_the_original_card() -> None:
    state = _OpenApiState()
    async with _open_api(state) as (api, timer_wheel, _):
        projector = _projector(timer_wheel, set())
        for index in range(181):
            _accept(
                projector,
                api,
                ToolCallStarted(ToolCall(f"call-{index}", f"tool-{index}")),
            )
        _accept(
            projector,
            api,
            ToolCallCompleted(ToolCall("call-0", "tool-0", output="done")),
        )
        _accept(projector, api, TurnCompleted("turn.completed"))
        await projector.wait_terminal(TEST_SESSION_ID, timeout=25)

        assert state.card_count == 2
        old_card_update = next(
            payload
            for method, path, payload in state.requests
            if method == "PUT" and "/cards/card-1/elements/i000001" in path
        )
        assert "Tool call" in json.loads(str(old_card_update["element"]))["content"]
        second_card_add = next(
            payload
            for method, path, payload in state.requests
            if method == "POST" and "/cards/card-2/elements" in path
        )
        assert second_card_add["sequence"] == 1


@pytest.mark.asyncio
async def test_lark_activity_failure_marks_the_final_reply(tmp_path: Path) -> None:
    state = _OpenApiState(reject_adds=True)
    async with _open_api(state) as (api, timer_wheel, base_url):

        async def referenced_paths() -> set[str]:
            return set()

        context = ChannelContext(
            agent_id="agent-test",
            attachments=AttachmentMaterializer(lambda: tmp_path, referenced_paths),
            options={},
            workspace=lambda: tmp_path,
            translator=create_translator(ENGLISH),
        )
        channel = LarkChannel(
            context,
            app_id="app-id",
            app_secret="app-secret",
            region="feishu",
            base_url=base_url,
            timer_wheel=timer_wheel,
            activity=True,
        )
        transport = LarkTransport(
            api,
            timer_wheel=timer_wheel,
            on_message=channel._handle_event,
        )
        transport._state = "connected"
        channel._api = api
        channel._identity = LarkBotIdentity("ou_bot")
        channel._transport = transport
        channel._state = "connected"
        channel._stream_routes[TEST_SESSION_ID] = "trigger-1"

        channel.accept_turn_event(
            _event(ToolCallStarted(ToolCall("call-1", "shell"))),
            session_id=TEST_SESSION_ID,
        )
        channel.accept_turn_event(
            _event(TurnCompleted("turn.completed")),
            session_id=TEST_SESSION_ID,
        )
        await channel._activity.wait_terminal(TEST_SESSION_ID, timeout=2)
        result = await channel.send(
            ChannelSendRequest(
                session_id=TEST_SESSION_ID,
                body="Finished",
                attachments=(),
                target_kind=ChannelTargetKind.DM,
                provider_thread_id=LarkThreadIdentity(
                    "ou_bot", "oc_chat"
                ).provider_thread_id,
            ),
            timeout=3,
        )

        assert result.status is ProviderCallStatus.CONFIRMED
        final = next(
            payload
            for method, path, payload in state.requests
            if method == "POST" and path == "/open-apis/im/v1/messages"
        )
        assert "Some activity details could not be displayed" in str(final["content"])
        await channel._activity.close()
