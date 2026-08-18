from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import bazaar_compute_node.contrib.telegram.channel as telegram_channel_module
from bazaar_compute_node.app.attachments import AttachmentMaterializer
from bazaar_compute_node.contrib.telegram.channel import TelegramChannel
from bazaar_compute_node.core.channel import ChannelContext, ChannelIdentity


class _FakeSession:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _FakeApi:
    async def get_me(self, *, timeout: float) -> dict[str, object]:
        assert timeout > 0
        return {"id": 8688828365, "is_bot": True, "username": "gobugobot"}

    async def get_updates(
        self,
        *,
        offset: int | None,
    ) -> list[dict[str, object]]:
        del offset
        await asyncio.Event().wait()
        raise AssertionError("blocked Telegram polling unexpectedly resumed")


@pytest.mark.asyncio
async def test_telegram_identity_exists_only_during_started_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def referenced_paths() -> set[str]:
        return set()

    fake_session = _FakeSession()
    fake_api = _FakeApi()
    monkeypatch.setattr(
        telegram_channel_module.aiohttp,
        "ClientSession",
        lambda: fake_session,
    )
    monkeypatch.setattr(
        telegram_channel_module,
        "TelegramBotApi",
        lambda _session, *, token: fake_api,
    )
    channel = TelegramChannel(
        ChannelContext(
            agent_id="agent-test",
            attachments=AttachmentMaterializer(lambda: tmp_path, referenced_paths),
            options={},
            workspace=lambda: tmp_path,
        ),
        token="token",
    )

    assert channel.get_identity() is None
    await channel.start(timeout=1)
    try:
        assert channel.get_identity() == ChannelIdentity(
            id="8688828365",
            name="gobugobot",
        )
    finally:
        await channel.stop(timeout=1)

    assert channel.get_identity() is None
    assert fake_session.closed
