from __future__ import annotations

import aiohttp
import pytest

from bazaar_compute_node.contrib.lark.activity import (
    LarkActivityProjector,
    LarkActivityRoute,
)
from bazaar_compute_node.contrib.lark.api import LarkApi
from bazaar_compute_node.core.models import (
    ContentDelta,
    ContentDeltaKind,
    RuntimeEventEnvelope,
    RuntimeEventPayload,
    RuntimeOutputEvent,
    ToolCall,
    ToolCallStarted,
)
from bazaar_compute_node.core.timerwheel import TimerWheel
from bazaar_compute_node.i18n import ENGLISH, create_translator

TEST_SESSION_ID = "session-1"


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


def _api(session: aiohttp.ClientSession, timer_wheel: TimerWheel) -> LarkApi:
    return LarkApi(
        session,
        app_id="app-id",
        app_secret="app-secret",
        base_url="https://open.feishu.test",
        timer_wheel=timer_wheel,
    )


@pytest.mark.asyncio
async def test_lark_activity_filters_non_display_events_without_limiting_activity() -> (
    None
):
    timer_wheel = TimerWheel()
    await timer_wheel.start()
    session = aiohttp.ClientSession()
    degraded: set[tuple[str, str]] = set()
    projector = LarkActivityProjector(
        timer_wheel=timer_wheel,
        translator=create_translator(ENGLISH),
        report_degraded=lambda session_id, turn_id: degraded.add((session_id, turn_id)),
    )
    try:
        api = _api(session, timer_wheel)
        route = LarkActivityRoute("trigger-1", True)
        for _ in range(2048):
            projector.accept(
                _event(ContentDelta(ContentDeltaKind.AGENT_MESSAGE, "working")),
                route=route,
                api=api,
            )
        assert projector.active_turns == 0

        for index in range(1100):
            projector.accept(
                _event(ToolCallStarted(ToolCall(f"call-{index}", "shell"))),
                route=route,
                api=api,
            )
        turn = next(iter(projector._turns.values()))
        assert len(turn.pending) > 1024
        assert degraded == set()
    finally:
        await projector.close()
        await session.close()
        await timer_wheel.close()
