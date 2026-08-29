from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

import bazaar_compute_node.contrib.telegram.channel as telegram_channel_module
from bazaar_compute_node.app.attachments import AttachmentMaterializer
from bazaar_compute_node.contrib.telegram.channel import TelegramChannel
from bazaar_compute_node.core.channel import ChannelContext, ChannelIdentity
from bazaar_compute_node.core.models import SenderIdentity, SenderKind

TEST_BOT_ID = 1_000_000_001
TEST_USER_ID = 1_000_000_002
TEST_OTHER_BOT_ID = 1_000_000_003
TEST_CHAT_ID = -1_000_000_004
TEST_BOT_USERNAME = "test-bot"
TEST_BOT_FIRST_NAME = "Test Bot"
TEST_USER_USERNAME = "test-user"
TEST_OTHER_BOT_USERNAME = "test-other-bot"
TEST_CHAT_USERNAME = "test-channel"


class _FakeSession:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _FakeApi:
    async def get_me(self, *, timeout: float) -> dict[str, object]:
        assert timeout > 0
        return {
            "id": TEST_BOT_ID,
            "is_bot": True,
            "username": TEST_BOT_USERNAME,
            "first_name": TEST_BOT_FIRST_NAME,
        }

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
            {
                "from": {
                    "id": TEST_USER_ID,
                    "username": TEST_USER_USERNAME,
                    "first_name": "Hanchin",
                    "last_name": "Hsieh",
                    "is_bot": False,
                }
            },
            SenderIdentity(
                id=str(TEST_USER_ID),
                name=TEST_USER_USERNAME,
                display_name="Hanchin Hsieh",
            ),
        ),
        (
            {
                "from": {
                    "id": TEST_USER_ID,
                    "username": TEST_USER_USERNAME,
                    "first_name": "Hanchin",
                    "is_bot": False,
                }
            },
            SenderIdentity(
                id=str(TEST_USER_ID),
                name=TEST_USER_USERNAME,
                display_name="Hanchin",
            ),
        ),
        (
            {
                "from": {
                    "id": TEST_USER_ID,
                    "first_name": "Hanchin",
                    "is_bot": False,
                }
            },
            SenderIdentity(id=str(TEST_USER_ID), display_name="Hanchin"),
        ),
        (
            {
                "from": {
                    "id": TEST_USER_ID,
                    "username": TEST_USER_USERNAME,
                    "is_bot": False,
                }
            },
            SenderIdentity(id=str(TEST_USER_ID), name=TEST_USER_USERNAME),
        ),
        (
            {
                "from": {
                    "id": TEST_OTHER_BOT_ID,
                    "username": TEST_OTHER_BOT_USERNAME,
                    "is_bot": True,
                }
            },
            SenderIdentity(
                id=str(TEST_OTHER_BOT_ID),
                name=TEST_OTHER_BOT_USERNAME,
            ),
        ),
        (
            {"from": {"id": TEST_USER_ID, "is_bot": False}},
            SenderIdentity(id=str(TEST_USER_ID)),
        ),
        (
            {
                "sender_chat": {
                    "id": TEST_CHAT_ID,
                    "username": TEST_CHAT_USERNAME,
                    "title": "Release Channel",
                }
            },
            SenderIdentity(
                id=str(TEST_CHAT_ID),
                name=TEST_CHAT_USERNAME,
                display_name="Release Channel",
            ),
        ),
        (
            {"sender_chat": {"id": TEST_CHAT_ID}},
            SenderIdentity(id=str(TEST_CHAT_ID)),
        ),
        (
            {
                "from": {
                    "id": TEST_USER_ID,
                    "username": TEST_USER_USERNAME,
                },
                "sender_chat": {
                    "id": TEST_CHAT_ID,
                    "username": TEST_CHAT_USERNAME,
                },
            },
            SenderIdentity(id=str(TEST_USER_ID), name=TEST_USER_USERNAME),
        ),
        ({}, None),
    ),
)
def test_telegram_sender_keeps_the_handle_and_the_profile_name_apart(
    message: dict[str, Any],
    expected: SenderIdentity | None,
) -> None:
    assert TelegramChannel._sender(message) == expected


@pytest.mark.parametrize(
    ("message", "expected"),
    (
        (
            {"from": {"id": TEST_USER_ID, "is_bot": False}},
            SenderKind.HUMAN,
        ),
        (
            {"from": {"id": TEST_OTHER_BOT_ID, "is_bot": True}},
            SenderKind.AGENT,
        ),
        ({"from": {"id": TEST_USER_ID}}, SenderKind.UNKNOWN),
        ({"sender_chat": {"id": TEST_CHAT_ID}}, SenderKind.UNKNOWN),
        ({}, SenderKind.UNKNOWN),
    ),
)
def test_telegram_sender_kind_preserves_unknown_provider_identity(
    message: dict[str, Any],
    expected: SenderKind,
) -> None:
    assert TelegramChannel._sender_kind(message) is expected


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
        bot_first_name = channel.health["bot_first_name"]
        identity = channel.get_identity()
        assert isinstance(bot_id, int)
        assert isinstance(bot_username, str)
        assert isinstance(bot_first_name, str)
        assert identity == ChannelIdentity(
            id=str(bot_id),
            name=f"{bot_username}({bot_first_name})",
        )
        message = {
            "message_id": 2,
            "date": channel._started_at_s,
            "chat": {"id": TEST_USER_ID, "type": "private"},
            "from": {
                "id": TEST_USER_ID,
                "is_bot": False,
                "username": TEST_USER_USERNAME,
            },
            "text": "Current message",
            "reply_to_message": {
                "message_id": 1,
                "date": channel._started_at_s,
                "chat": {"id": TEST_USER_ID, "type": "private"},
                "from": {
                    "id": TEST_OTHER_BOT_ID,
                    "is_bot": True,
                    "username": TEST_OTHER_BOT_USERNAME,
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
        assert quoted.sender == SenderIdentity(
            id=str(TEST_OTHER_BOT_ID),
            name=TEST_OTHER_BOT_USERNAME,
        )
        assert quoted.sender_kind is SenderKind.AGENT
        assert current.sender == SenderIdentity(
            id=str(TEST_USER_ID),
            name=TEST_USER_USERNAME,
        )
        assert current.sender_kind is SenderKind.HUMAN
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
