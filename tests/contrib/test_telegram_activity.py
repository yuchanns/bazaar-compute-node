from __future__ import annotations

import aiohttp
import pytest

from bazaar_compute_node.contrib.telegram.activity import (
    TelegramActivityProjector,
    _ActivityTurn,
)
from bazaar_compute_node.contrib.telegram.api import TelegramBotApi
from bazaar_compute_node.contrib.telegram.identity import TelegramThreadIdentity
from bazaar_compute_node.core.models import (
    ContentDelta,
    ContentDeltaKind,
    RuntimeEventEnvelope,
    RuntimeEventPayload,
    RuntimeOutputEvent,
    ToolCall,
    ToolCallStarted,
    TurnFailed,
)
from bazaar_compute_node.i18n import ENGLISH, create_translator

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


@pytest.mark.asyncio
async def test_telegram_activity_filters_events_without_limiting_queue() -> None:
    projector = TelegramActivityProjector(
        timer_wheel=None,
        translator=create_translator(ENGLISH),
    )
    identity = TelegramThreadIdentity(TEST_BOT_ID, TEST_CHAT_ID, 0)
    session = aiohttp.ClientSession()
    try:
        api = TelegramBotApi(session, token="token")
        for _ in range(2048):
            projector.accept(
                _event(ContentDelta(ContentDeltaKind.AGENT_MESSAGE, "working")),
                identity=identity,
                api=api,
            )
        assert projector.active_turns == 0

        for index in range(1100):
            projector.accept(
                _event(ToolCallStarted(ToolCall(f"call-{index}", "shell"))),
                identity=identity,
                api=api,
            )
        turn = next(iter(projector._turns.values()))
        assert turn.queue.qsize() > 1024
    finally:
        await projector.close()
        await session.close()


def test_telegram_activity_escapes_a_provider_error_in_the_overview() -> None:
    projector = TelegramActivityProjector(
        timer_wheel=None,
        translator=create_translator(ENGLISH),
    )
    turn = _ActivityTurn(identity=TelegramThreadIdentity(TEST_BOT_ID, TEST_CHAT_ID, 0))
    turn.reducer.apply(
        TurnFailed(
            event_name="bcn.turn.failed",
            error_kind="provider_failed",
            error_message="failed on _main_ [see](docs) `run`",
        )
    )

    markdown = projector._render(turn)

    assert markdown is not None
    assert "\\_main\\_" in markdown
    assert "\\[see\\]\\(docs\\)" in markdown
    assert "\\`run\\`" in markdown
