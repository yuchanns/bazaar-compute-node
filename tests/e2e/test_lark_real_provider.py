from __future__ import annotations

import os
from pathlib import Path

import pytest

from bazaar_compute_node.app.attachments import AttachmentMaterializer
from bazaar_compute_node.contrib.lark.channel import LarkChannel
from bazaar_compute_node.contrib.lark.plugin import LarkBuilder
from bazaar_compute_node.core.channel import ChannelContext
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
    channel = LarkBuilder().build(
        ChannelContext(
            agent_id="agent-lark-e2e",
            attachments=AttachmentMaterializer(lambda: tmp_path, _referenced_paths),
            options={
                "app_id": app_id,
                "app_secret_env": "BCN_LARK_APP_SECRET",
                "region": region,
            },
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
