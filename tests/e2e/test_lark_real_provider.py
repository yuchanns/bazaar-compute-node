from __future__ import annotations

import asyncio
import os
from pathlib import Path
from time import monotonic, time_ns
from uuid import uuid4

import pytest

from bazaar_compute_node.app.attachments import AttachmentMaterializer
from bazaar_compute_node.contrib.lark.channel import LarkChannel
from bazaar_compute_node.contrib.lark.plugin import LarkBuilder
from bazaar_compute_node.core.channel import ChannelContext
from bazaar_compute_node.core.models import (
    RuntimeEventEnvelope,
    RuntimeEventPayload,
    RuntimeOutputEvent,
    TokenUsage,
    ToolCall,
    ToolCallCompleted,
    ToolCallStarted,
    TurnCompleted,
    UsageUpdated,
)
from bazaar_compute_node.core.timerwheel import TimerWheel

pytestmark = pytest.mark.e2e


async def _referenced_paths() -> set[str]:
    return set()


def _channel(tmp_path: Path, timer_wheel: TimerWheel) -> LarkChannel:
    app_id = os.environ.get("BCN_LARK_APP_ID")
    app_secret = os.environ.get("BCN_LARK_APP_SECRET")
    if not app_id or not app_secret:
        pytest.skip(
            "BCN_LARK_APP_ID and BCN_LARK_APP_SECRET are required for Lark provider verification"
        )
    region = os.environ.get("BCN_LARK_REGION", "feishu")
    options: dict[str, object] = {
        "app_id": app_id,
        "app_secret_env": "BCN_LARK_APP_SECRET",
        "region": region,
    }
    channel = LarkBuilder().build(
        ChannelContext(
            agent_id="agent-lark-e2e",
            attachments=AttachmentMaterializer(lambda: tmp_path, _referenced_paths),
            options=options,
            workspace=lambda: tmp_path,
            timer_wheel=timer_wheel,
        )
    )
    assert isinstance(channel, LarkChannel)
    return channel


def _required_text(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} is required for Lark activity verification")
    return value


def _activity_event(
    session_id: str,
    turn_id: str,
    payload: RuntimeEventPayload,
) -> RuntimeOutputEvent:
    return RuntimeOutputEvent(
        envelope=RuntimeEventEnvelope(
            session_id=session_id,
            runtime_session_id=f"runtime-{uuid4()}",
            turn_id=turn_id,
            provider_turn_id=None,
            occurred_at_ms=time_ns() // 1_000_000,
        ),
        payload=payload,
    )


@pytest.mark.asyncio
async def test_lark_real_provider_lifecycle_identity(tmp_path: Path) -> None:
    timer_wheel = TimerWheel()
    await timer_wheel.start()
    channel: LarkChannel | None = None
    try:
        channel = _channel(tmp_path, timer_wheel)
        assert channel.get_identity() is None
        await channel.start(timeout=60)
        identity = channel.get_identity()
        assert identity is not None
        assert identity.id
        assert channel.health["bot_open_id"] == identity.id
        generation = channel.health["connection_generation"]
        assert isinstance(generation, int)
        assert generation >= 1
    finally:
        if channel is not None:
            await channel.stop(timeout=10)
        await timer_wheel.close()

    assert channel is not None
    assert channel.get_identity() is None
    assert channel.health["state"] == "stopped"


@pytest.mark.asyncio
async def test_lark_real_provider_keeps_one_activity_card_per_turn(
    tmp_path: Path,
) -> None:
    trigger_message_id = _required_text("BCN_LARK_E2E_TRIGGER_MESSAGE_ID")
    timer_wheel = TimerWheel()
    await timer_wheel.start()
    channel: LarkChannel | None = None
    try:
        channel = _channel(tmp_path, timer_wheel)
        await channel.start(timeout=60)
        session_id = f"session-{uuid4()}"
        turn_id = f"turn-{uuid4()}"
        channel._stream_routes[session_id] = trigger_message_id

        channel.accept_turn_event(
            _activity_event(
                session_id,
                turn_id,
                ToolCallStarted(ToolCall(f"call-{uuid4()}", "provider-check")),
            ),
            session_id=session_id,
        )
        async with asyncio.timeout(60):
            while channel.health["activity_cards_created"] == 0:
                await asyncio.sleep(0.05)
        created_at = monotonic()

        channel.accept_turn_event(
            _activity_event(
                session_id,
                turn_id,
                UsageUpdated(TokenUsage(input_tokens=13, output_tokens=4)),
            ),
            session_id=session_id,
        )
        channel.accept_turn_event(
            _activity_event(
                session_id,
                turn_id,
                ToolCallCompleted(ToolCall(f"call-{uuid4()}", "provider-check")),
            ),
            session_id=session_id,
        )
        channel.accept_turn_event(
            _activity_event(session_id, turn_id, TurnCompleted("turn.completed")),
            session_id=session_id,
        )
        async with asyncio.timeout(60):
            while channel.health["activity_turns"]:
                await asyncio.sleep(0.05)

        assert monotonic() - created_at >= 0.2
        assert channel.health["activity_cards_created"] == 1
        updated = channel.health["activity_elements_updated"]
        assert isinstance(updated, int)
        assert updated >= 1
        assert channel.health["activity_failures"] == 0
    finally:
        if channel is not None:
            await channel.stop(timeout=10)
        await timer_wheel.close()
