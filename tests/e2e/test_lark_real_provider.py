from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest

from bazaar_compute_node.app.attachments import AttachmentMaterializer
from bazaar_compute_node.contrib.lark.channel import LarkChannel
from bazaar_compute_node.contrib.lark.identity import LarkThreadIdentity
from bazaar_compute_node.contrib.lark.plugin import LarkBuilder
from bazaar_compute_node.core.channel import (
    ChannelContext,
    ChannelSendRequest,
    ChannelTargetKind,
)
from bazaar_compute_node.core.outcomes import ProviderCallStatus
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
async def test_lark_real_provider_delivers_a_message(tmp_path: Path) -> None:
    """Send through the real provider, so the reply route is covered by fact."""

    chat_id = os.environ.get("BCN_LARK_TEST_CHAT_ID")
    if not chat_id:
        pytest.skip("BCN_LARK_TEST_CHAT_ID is required to verify Lark delivery")
    timer_wheel = TimerWheel()
    await timer_wheel.start()
    channel: LarkChannel | None = None
    try:
        channel = _channel(tmp_path, timer_wheel)
        await channel.start(timeout=60)
        identity = channel.get_identity()
        assert identity is not None
        bot_open_id = identity.id
        assert bot_open_id is not None
        thread = LarkThreadIdentity(bot_open_id=bot_open_id, chat_id=chat_id)
        nonce = uuid4()
        result = await channel.send(
            ChannelSendRequest(
                session_id=f"session-{nonce}",
                body=f"BCN Lark provider verification\n\nRun: `{nonce}`",
                attachments=(),
                target_kind=ChannelTargetKind.DM,
                provider_thread_id=thread.provider_thread_id,
            ),
            timeout=60,
        )
        assert result.status is ProviderCallStatus.CONFIRMED, result.error_message
        assert result.value is not None
        assert result.value.provider_message_id
    finally:
        if channel is not None:
            await channel.stop(timeout=10)
        await timer_wheel.close()
