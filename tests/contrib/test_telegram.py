from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import pytest

from bazaar_compute_node.app.attachments import AttachmentMaterializer
from bazaar_compute_node.contrib.telegram.channel import TelegramChannel
from bazaar_compute_node.contrib.telegram.identity import (
    TelegramThreadIdentity,
    parse_provider_thread_id,
)
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


def _configured_channel(
    tmp_path: Path,
    *,
    started_at_s: int = 0,
) -> TelegramChannel:
    channel = _channel(tmp_path)
    channel._bot_id = 123
    channel._bot_username = "runtime_bot"
    channel._started_at_s = started_at_s
    return channel


def test_telegram_thread_identity_is_deterministic_and_round_trips() -> None:
    identity = TelegramThreadIdentity(bot_id=123, chat_id=-100456, topic_id=42)
    same_identity = TelegramThreadIdentity(bot_id=123, chat_id=-100456, topic_id=42)

    assert identity.provider_thread_id == "telegram:123:-100456:42"
    assert parse_provider_thread_id(identity.provider_thread_id) == identity
    assert identity.channel_session_id == same_identity.channel_session_id
    assert identity.session_id == same_identity.session_id
    assert identity.message_id(42) == same_identity.message_id(42)

    assert (
        TelegramThreadIdentity(
            bot_id=124, chat_id=-100456, topic_id=42
        ).channel_session_id
        != identity.channel_session_id
    )
    assert (
        TelegramThreadIdentity(
            bot_id=123, chat_id=-100457, topic_id=42
        ).channel_session_id
        != identity.channel_session_id
    )
    assert (
        TelegramThreadIdentity(
            bot_id=123, chat_id=-100456, topic_id=43
        ).channel_session_id
        != identity.channel_session_id
    )
    assert TelegramThreadIdentity(bot_id=123, chat_id=-100456, topic_id=42).message_id(
        43
    ) != identity.message_id(42)


@pytest.mark.parametrize(
    "identity",
    (
        {"bot_id": 0, "chat_id": 1, "topic_id": 0},
        {"bot_id": 1, "chat_id": 0, "topic_id": 0},
        {"bot_id": 1, "chat_id": 1, "topic_id": -1},
    ),
)
def test_telegram_thread_identity_rejects_invalid_components(
    identity: dict[str, int],
) -> None:
    with pytest.raises(ValueError):
        TelegramThreadIdentity(**identity)


@pytest.mark.parametrize(
    "value",
    (
        "",
        "telegram:123:456",
        "telegram:123:456:not-an-int",
        "other:123:456:0",
        "telegram:0:456:0",
        "telegram:123:0:0",
        "telegram:123:456:-1",
    ),
)
def test_parse_telegram_provider_thread_id_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValueError):
        parse_provider_thread_id(value)


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
    channel = _configured_channel(tmp_path)
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
        "telegram_message_thread_id": 0,
        "telegram_chat_type": "private",
        "sender_is_bot": True,
        "historical": False,
        "activation_reason": "none",
    }
    assert channel.health["messages_queued"] == 1
    assert channel.health["last_update_disposition"] == "message_queued"


@pytest.mark.asyncio
async def test_telegram_forum_topic_preserves_group_identity_and_bot_sender(
    tmp_path: Path,
) -> None:
    channel = _configured_channel(tmp_path)

    await channel._dispatch_update(
        {
            "message": {
                "message_id": 42,
                "date": 1_700_000_000,
                "message_thread_id": 7,
                "chat": {"id": -100456, "type": "supergroup"},
                "from": {"id": 789, "is_bot": True},
                "text": "hello",
            }
        },
        update_id=17,
    )

    message = await _read_inbound(channel)
    identity = TelegramThreadIdentity(bot_id=123, chat_id=-100456, topic_id=7)

    assert message.message_id == identity.message_id(42)
    assert message.session_id == identity.session_id
    assert message.channel_session_id == identity.channel_session_id
    assert message.provider_thread_id == "telegram:123:-100456:7"
    assert message.provider_message_id == "42"
    assert message.target_kind is ChannelTargetKind.GROUP
    assert message.canonical_target == f"group:{identity.channel_session_id}"
    assert message.sender == "789"
    assert message.mentions_agent is False
    assert message.notifies_runtime is True
    assert message.metadata["sender_is_bot"] is True


@pytest.mark.parametrize(
    ("text", "entities"),
    (
        (
            "😀 @runtime_bot",
            [{"type": "mention", "offset": 3, "length": 12}],
        ),
        (
            "say hello",
            [
                {
                    "type": "text_mention",
                    "offset": 4,
                    "length": 5,
                    "user": {"id": 123},
                }
            ],
        ),
        (
            "/ask@runtime_bot",
            [{"type": "bot_command", "offset": 0, "length": 16}],
        ),
    ),
)
@pytest.mark.asyncio
async def test_telegram_explicit_entity_mentions_activate_agent(
    tmp_path: Path,
    text: str,
    entities: list[dict[str, object]],
) -> None:
    channel = _configured_channel(tmp_path)

    await channel._dispatch_update(
        {
            "message": {
                "message_id": 42,
                "date": 1_700_000_000,
                "chat": {"id": -100456, "type": "group"},
                "from": {"id": 789, "is_bot": False},
                "text": text,
                "entities": entities,
            }
        },
        update_id=17,
    )

    message = await _read_inbound(channel)

    assert message.mentions_agent is True
    assert message.notifies_runtime is True
    assert message.metadata["activation_reason"] == "mention"


@pytest.mark.asyncio
async def test_telegram_historical_messages_only_notify_on_activation(
    tmp_path: Path,
) -> None:
    channel = _configured_channel(tmp_path, started_at_s=100)

    await channel._dispatch_update(
        {
            "message": {
                "message_id": 41,
                "date": 99,
                "chat": {"id": -100456, "type": "group"},
                "from": {"id": 789, "is_bot": False},
                "text": "old message",
            }
        },
        update_id=17,
    )
    await channel._dispatch_update(
        {
            "message": {
                "message_id": 42,
                "date": 99,
                "chat": {"id": -100456, "type": "group"},
                "from": {"id": 789, "is_bot": False},
                "text": "@runtime_bot old mention",
                "entities": [{"type": "mention", "offset": 0, "length": 12}],
            }
        },
        update_id=18,
    )

    historical = await _read_inbound(channel)
    activated = await _read_inbound(channel)

    assert historical.notifies_runtime is False
    assert historical.mentions_agent is False
    assert historical.metadata["historical"] is True
    assert activated.notifies_runtime is True
    assert activated.mentions_agent is True
    assert activated.metadata["historical"] is True
    assert activated.metadata["activation_reason"] == "mention"
    assert channel.health["historical_messages_suppressed"] == 1
    assert channel.health["activation_messages"] == 1


@pytest.mark.asyncio
async def test_telegram_reply_to_current_bot_backfills_quoted_message(
    tmp_path: Path,
) -> None:
    channel = _configured_channel(tmp_path, started_at_s=100)

    await channel._dispatch_update(
        {
            "message": {
                "message_id": 20,
                "date": 101,
                "message_thread_id": 7,
                "chat": {"id": -100456, "type": "supergroup"},
                "from": {"id": 789, "is_bot": False},
                "text": "answer",
                "reply_to_message": {
                    "message_id": 10,
                    "date": 99,
                    "message_thread_id": 7,
                    "chat": {"id": -100456, "type": "supergroup"},
                    "from": {"id": 123, "is_bot": True},
                    "text": "bot prompt",
                },
            }
        },
        update_id=17,
    )

    quoted = await _read_inbound(channel)
    current = await _read_inbound(channel)

    identity = TelegramThreadIdentity(bot_id=123, chat_id=-100456, topic_id=7)
    assert quoted.message_id == identity.message_id(10)
    assert quoted.provider_message_id == "10"
    assert quoted.body == "bot prompt"
    assert quoted.sender == "123"
    assert quoted.mentions_agent is False
    assert quoted.notifies_runtime is False
    assert quoted.metadata["quoted_backfill"] is True
    assert current.message_id == identity.message_id(20)
    assert current.reply_to_message_id == quoted.message_id
    assert current.mentions_agent is True
    assert current.notifies_runtime is True
    assert current.metadata["activation_reason"] == "reply_to_bot"
    assert channel.health["quoted_messages_queued"] == 1


@pytest.mark.asyncio
async def test_telegram_current_bot_top_level_message_is_filtered(
    tmp_path: Path,
) -> None:
    channel = _configured_channel(tmp_path)
    channel._bot_username = "example_bot"

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
