from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import pytest

from bazaar_compute_node.app.attachments import AttachmentMaterializer
from bazaar_compute_node.contrib.telegram.channel import TelegramChannel
from bazaar_compute_node.contrib.telegram.plugin import TelegramBuilder
from bazaar_compute_node.core.channel import ChannelContext
from bazaar_compute_node.core.models import ChannelTargetKind, InboundMessage


async def _referenced_paths() -> set[str]:
    return set()


def _context(tmp_path: Path) -> ChannelContext:
    return ChannelContext(
        attachments=AttachmentMaterializer(lambda: tmp_path, _referenced_paths),
        options={},
        workspace=lambda: tmp_path,
    )


def _channel(tmp_path: Path) -> TelegramChannel:
    return TelegramChannel(_context(tmp_path), token="telegram-test-token")


async def _read_inbound(channel: TelegramChannel) -> InboundMessage:
    item = await asyncio.wait_for(channel._inbound.get(), timeout=1)
    assert isinstance(item, InboundMessage)
    return item


def test_telegram_builder_requires_bot_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("BCN_TELEGRAM_BOT_TOKEN", raising=False)

    with pytest.raises(ValueError, match="BCN_TELEGRAM_BOT_TOKEN is required"):
        TelegramBuilder().build(_context(tmp_path))


def test_telegram_builder_builds_channel(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("BCN_TELEGRAM_BOT_TOKEN", "telegram-test-token")

    channel = TelegramBuilder().build(_context(tmp_path))

    assert channel.name == "telegram"
    assert channel.health["state"] == "stopped"


@pytest.mark.asyncio
async def test_telegram_private_message_is_normalized(tmp_path: Path) -> None:
    channel = _channel(tmp_path)
    channel._bot_id = 123
    channel._bot_username = "example_bot"

    await channel._dispatch_update(
        {
            "message": {
                "message_id": 42,
                "date": 1_700_000_000,
                "chat": {"id": 456, "type": "private"},
                "from": {"id": 789, "is_bot": True},
                "text": "hello",
            }
        },
        update_id=17,
    )

    message = await _read_inbound(channel)
    thread_identity = "telegram:bot:123:chat:456:topic:0"
    channel_session_id = str(uuid5(NAMESPACE_URL, thread_identity))

    assert message.message_id == str(
        uuid5(
            NAMESPACE_URL,
            "telegram:bot:123:chat:456:message:42",
        )
    )
    assert message.session_id == str(uuid5(NAMESPACE_URL, f"bcn:{thread_identity}"))
    assert message.channel_session_id == channel_session_id
    assert message.channel == "telegram"
    assert message.provider_thread_id == "telegram:123:456:0"
    assert message.provider_message_id == "42"
    assert message.sender == "789"
    assert message.message_type == "text"
    assert message.canonical_target == f"dm:{channel_session_id}"
    assert message.body == "hello"
    assert message.target_kind is ChannelTargetKind.DM
    assert message.mentions_agent is False
    assert message.notifies_runtime is True
    assert message.provider_time_ms == 1_700_000_000_000
    assert message.metadata == {
        "telegram_update_id": 17,
        "telegram_chat_id": 456,
        "telegram_chat_type": "private",
    }
    assert channel.health["messages_queued"] == 1
    assert channel.health["last_update_disposition"] == "message_queued"


@pytest.mark.asyncio
async def test_telegram_current_bot_top_level_message_is_filtered(
    tmp_path: Path,
) -> None:
    channel = _channel(tmp_path)
    channel._bot_id = 123

    await channel._dispatch_update(
        {
            "message": {
                "message_id": 42,
                "date": 1_700_000_000,
                "chat": {"id": 456, "type": "private"},
                "from": {"id": 123, "is_bot": True},
                "text": "outbound echo",
            }
        },
        update_id=17,
    )

    assert channel.health["message_updates_received"] == 1
    assert channel.health["message_updates_filtered"] == 1
    assert channel.health["messages_queued"] == 0
    assert channel.health["last_update_disposition"] == "current_bot_message"


@pytest.mark.asyncio
async def test_telegram_callback_update_is_dispatched_to_health(
    tmp_path: Path,
) -> None:
    channel = _channel(tmp_path)

    await channel._dispatch_update(
        {"callback_query": {"id": "callback-1"}},
        update_id=18,
    )

    assert channel.health["callback_updates_received"] == 1
    assert channel.health["last_update_disposition"] == "callback_query"


@pytest.mark.asyncio
async def test_telegram_start_rejects_expired_deadline(tmp_path: Path) -> None:
    channel = _channel(tmp_path)

    with pytest.raises(TimeoutError, match="startup deadline expired"):
        await channel.start(timeout=0)

    assert channel.health["state"] == "stopped"


@pytest.mark.asyncio
async def test_telegram_stop_closes_receive_stream(tmp_path: Path) -> None:
    channel = _channel(tmp_path)

    await channel.stop(timeout=1)

    assert channel.health["state"] == "stopped"
    with pytest.raises(StopAsyncIteration):
        await anext(channel.receive())
