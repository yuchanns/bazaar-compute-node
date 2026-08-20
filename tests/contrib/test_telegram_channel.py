from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

import bazaar_compute_node.contrib.telegram.channel as telegram_channel_module
from bazaar_compute_node.app.attachments import AttachmentMaterializer
from bazaar_compute_node.contrib.telegram.channel import TelegramChannel
from bazaar_compute_node.core.channel import ChannelContext, ChannelIdentity
from bazaar_compute_node.core.models import SenderIdentity


class _FakeSession:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _FakeApi:
    async def get_me(self, *, timeout: float) -> dict[str, object]:
        assert timeout > 0
        return {"id": 8688828365, "is_bot": True, "username": "test-bot"}

    async def get_updates(
        self,
        *,
        offset: int | None,
    ) -> list[dict[str, object]]:
        del offset
        await asyncio.Event().wait()
        raise AssertionError("blocked Telegram polling unexpectedly resumed")


@pytest.mark.parametrize(
    ("message", "expected"),
    (
        (
            {"from": {"id": 1956760814, "username": "realyuchanns", "is_bot": False}},
            SenderIdentity(id="1956760814", name="realyuchanns"),
        ),
        (
            {"from": {"id": 6820994803, "username": "bkaiBot", "is_bot": True}},
            SenderIdentity(id="6820994803", name="bkaiBot"),
        ),
        (
            {"from": {"id": 1956760814, "is_bot": False}},
            SenderIdentity(id="1956760814"),
        ),
        (
            {"sender_chat": {"id": -100123, "username": "projectUpdates"}},
            SenderIdentity(id="-100123", name="projectUpdates"),
        ),
        ({"sender_chat": {"id": -100123}}, SenderIdentity(id="-100123")),
        (
            {
                "from": {"id": 1956760814, "username": "realyuchanns"},
                "sender_chat": {"id": -100123, "username": "projectUpdates"},
            },
            SenderIdentity(id="1956760814", name="realyuchanns"),
        ),
        ({}, None),
    ),
)
def test_telegram_sender_prefers_username_with_id_fallback(
    message: dict[str, Any],
    expected: SenderIdentity | None,
) -> None:
    assert TelegramChannel._sender(message) == expected


@pytest.mark.asyncio
async def test_telegram_lifecycle_identity_and_inbound_speaker_projection(
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

    def build_api(*args: object, **kwargs: object) -> _FakeApi:
        del args, kwargs
        return fake_api

    monkeypatch.setattr(telegram_channel_module, "TelegramBotApi", build_api)
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
        bot_id = channel.health["bot_id"]
        bot_username = channel.health["bot_username"]
        identity = channel.get_identity()
        assert isinstance(bot_id, int)
        assert isinstance(bot_username, str)
        assert identity == ChannelIdentity(id=str(bot_id), name=bot_username)
        message = {
            "message_id": 2,
            "date": channel._started_at_s,
            "chat": {"id": 1956760814, "type": "private"},
            "from": {
                "id": 1956760814,
                "is_bot": False,
                "username": "realyuchanns",
            },
            "text": "Current message",
            "reply_to_message": {
                "message_id": 1,
                "date": channel._started_at_s,
                "chat": {"id": 1956760814, "type": "private"},
                "from": {
                    "id": 6820994803,
                    "is_bot": True,
                    "username": "bkaiBot",
                },
                "text": "Quoted message",
            },
        }
        await channel._handle_message(message, update_id=1)

        received = []
        async for inbound in channel.receive():
            received.append(inbound)
            if len(received) == 2:
                break

        quoted, current = received
        assert quoted.sender == SenderIdentity(id="6820994803", name="bkaiBot")
        assert current.sender == SenderIdentity(id="1956760814", name="realyuchanns")
        assert current.reply_to_message_id == quoted.message_id

        filtered_before = channel._message_updates_filtered
        await channel._handle_message(
            {"from": {"id": bot_id, "username": bot_username}},
            update_id=2,
        )
        assert channel._message_updates_filtered == filtered_before + 1
        assert channel._last_update_disposition == "current_bot_message"
        assert channel._inbound.empty()
    finally:
        await channel.stop(timeout=1)

    assert channel.get_identity() is None
    assert fake_session.closed
