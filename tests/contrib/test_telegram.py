from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast
from uuid import NAMESPACE_URL, uuid5

import pytest

from bazaar_compute_node.app.attachments import AttachmentMaterializer
from bazaar_compute_node.contrib.telegram.api import (
    TelegramBotApi,
    TelegramTransportError,
)
from bazaar_compute_node.contrib.telegram.attachments import (
    TelegramAttachmentSource,
    attachment_sources,
    materialize_attachments,
)
from bazaar_compute_node.contrib.telegram.channel import TelegramChannel
from bazaar_compute_node.contrib.telegram.identity import (
    TelegramThreadIdentity,
    parse_provider_thread_id,
)
from bazaar_compute_node.contrib.telegram.markdown import RichMessageRenderer
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


class _FakeTelegramApi:
    def __init__(
        self,
        *,
        files: dict[str, dict[str, object]],
        downloads: dict[str, tuple[bytes, ...]],
        download_error: BaseException | None = None,
    ) -> None:
        self.files = files
        self.downloads = downloads
        self.download_error = download_error
        self.file_requests: list[str] = []
        self.download_requests: list[str] = []

    async def get_file(self, file_id: str) -> dict[str, object]:
        self.file_requests.append(file_id)
        return self.files[file_id]

    async def download_file(self, file_path: str) -> AsyncIterator[bytes]:
        self.download_requests.append(file_path)
        if self.download_error is not None:
            raise self.download_error
        for chunk in self.downloads[file_path]:
            yield chunk


def _telegram_api(fake: _FakeTelegramApi) -> TelegramBotApi:
    return cast(TelegramBotApi, fake)


def test_telegram_rich_renderer_preserves_blocks_and_mentions() -> None:
    renderer = RichMessageRenderer(bot_id=123, bot_username="Runtime_Bot")

    view = renderer.render(
        {
            "blocks": [
                {"type": "paragraph", "text": "intro"},
                {"type": "heading", "size": 2, "text": "title"},
                {
                    "type": "list",
                    "items": [
                        {
                            "label": "item",
                            "blocks": [{"type": "paragraph", "text": "value"}],
                        },
                        {
                            "label": "done",
                            "has_checkbox": True,
                            "is_checked": True,
                            "blocks": [{"type": "paragraph", "text": "checked"}],
                        },
                    ],
                },
                {
                    "type": "blockquote",
                    "blocks": [{"type": "paragraph", "text": "quoted"}],
                },
                {
                    "type": "table",
                    "cells": [
                        [{"text": "name"}, {"text": "value"}],
                        [{"text": "n"}, {"text": "v"}],
                    ],
                },
                {
                    "type": "details",
                    "summary": "more",
                    "blocks": [{"type": "paragraph", "text": "detail"}],
                },
            ]
        }
    )

    assert view.body == (
        "intro\n\n"
        "## title\n\n"
        "item value\n"
        "done [x] checked\n\n"
        "> quoted\n\n"
        "| name | value |\n"
        "| --- | --- |\n"
        "| n | v |\n\n"
        "<details>\n"
        "<summary>more</summary>\n\n"
        "detail\n\n"
        "</details>"
    )

    mention_view = renderer.render(
        {
            "blocks": [
                {
                    "type": "paragraph",
                    "text": [
                        {"type": "mention", "username": "@runtime_bot"},
                        {"type": "bold", "text": " hello"},
                    ],
                }
            ]
        }
    )
    assert mention_view.body == "@runtime_bot** hello**"
    assert mention_view.mentions_agent is True


def test_telegram_attachment_sources_select_largest_photo_and_names() -> None:
    sources = attachment_sources(
        {
            "message_id": 7,
            "photo": [
                {"file_id": "small", "file_size": 10, "width": 100, "height": 100},
                {"file_id": "large", "file_size": 100, "width": 50, "height": 50},
            ],
            "video": {
                "file_id": "video",
                "file_name": "clip.mp4",
                "mime_type": "video/mp4",
            },
            "audio": {"file_id": "audio", "file_name": "song.mp3"},
            "voice": {"file_id": "voice"},
            "video_note": {"file_id": "note"},
            "document": {
                "file_id": "document",
                "file_name": "report.pdf",
                "mime_type": "application/pdf",
                "file_size": 12,
            },
        }
    )

    assert [source.file_id for source in sources] == [
        "large",
        "video",
        "audio",
        "voice",
        "note",
        "document",
    ]
    assert sources[0].name == "photo-7.jpg"
    assert sources[1].name == "clip.mp4"
    assert sources[-1].name == "report.pdf"
    assert sources[-1].media_type == "application/pdf"


@pytest.mark.asyncio
async def test_telegram_materializes_stream_and_reports_provider_failures(
    tmp_path: Path,
) -> None:
    fake = _FakeTelegramApi(
        files={"document": {"file_path": "documents/report.pdf"}},
        downloads={"documents/report.pdf": (b"hello", b" world")},
    )
    materializer = _context(tmp_path).attachments
    source = TelegramAttachmentSource(
        file_id="document",
        kind="document",
        name="report.pdf",
        media_type="application/pdf",
        file_size=11,
    )

    ready, too_large = await materialize_attachments(
        _telegram_api(fake),
        materializer,
        (
            source,
            TelegramAttachmentSource(
                file_id="large",
                kind="document",
                name="large.bin",
                file_size=20 * 1024 * 1024 + 1,
            ),
        ),
    )

    assert ready.state == "ready"
    assert ready.name == "report.pdf"
    assert ready.relative_path is not None
    assert (tmp_path / ready.relative_path).read_bytes() == b"hello world"
    assert too_large.state == "failed"
    assert too_large.error == "telegram_file_too_large"
    assert fake.file_requests == ["document"]
    assert fake.download_requests == ["documents/report.pdf"]

    failing_api = _FakeTelegramApi(
        files={"voice": {"file_path": "voice.ogg"}},
        downloads={},
        download_error=TelegramTransportError("downloadFile", "ConnectionError"),
    )
    (failed,) = await materialize_attachments(
        _telegram_api(failing_api),
        materializer,
        (
            TelegramAttachmentSource(
                file_id="voice",
                kind="voice",
                name="voice.ogg",
            ),
        ),
    )
    assert failed.state == "failed"
    assert failed.error == "telegram_attachment_failed:TelegramTransportError"


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
        "rich_message": False,
    }
    assert channel.health["messages_queued"] == 1
    assert channel.health["last_update_disposition"] == "message_queued"


@pytest.mark.asyncio
async def test_telegram_rich_message_is_rendered_and_mention_activates(
    tmp_path: Path,
) -> None:
    channel = _configured_channel(tmp_path)

    await channel._dispatch_update(
        {
            "message": {
                "message_id": 43,
                "date": 1_700_000_000,
                "chat": {"id": -100456, "type": "group"},
                "from": {"id": 789, "is_bot": False},
                "rich_message": {
                    "blocks": [
                        {
                            "type": "paragraph",
                            "text": [
                                {
                                    "type": "mention",
                                    "username": "runtime_bot",
                                },
                                {"type": "text", "text": " hello"},
                            ],
                        },
                        {"type": "heading", "size": 2, "text": "Title"},
                    ]
                },
            }
        },
        update_id=19,
    )

    message = await _read_inbound(channel)

    assert message.body == "@runtime_bot hello\n\n## Title"
    assert message.message_type == "rich_message"
    assert message.mentions_agent is True
    assert message.notifies_runtime is True
    assert message.metadata["rich_message"] is True


@pytest.mark.asyncio
async def test_telegram_media_message_materializes_selected_attachment(
    tmp_path: Path,
) -> None:
    channel = _configured_channel(tmp_path)
    fake = _FakeTelegramApi(
        files={"large-photo": {"file_path": "photos/large.jpg"}},
        downloads={"photos/large.jpg": (b"image-bytes",)},
    )
    channel._api = _telegram_api(fake)

    await channel._dispatch_update(
        {
            "message": {
                "message_id": 44,
                "date": 1_700_000_000,
                "chat": {"id": 456, "type": "private"},
                "from": {"id": 789, "is_bot": False},
                "caption": "look at this",
                "photo": [
                    {"file_id": "small-photo", "file_size": 5},
                    {"file_id": "large-photo", "file_size": 10},
                ],
            }
        },
        update_id=20,
    )

    message = await _read_inbound(channel)

    assert fake.file_requests == ["large-photo"]
    assert len(message.attachments) == 1
    attachment = message.attachments[0]
    assert attachment.state == "ready"
    assert attachment.name == "photo-44.jpg"
    assert attachment.relative_path is not None
    assert (tmp_path / attachment.relative_path).read_bytes() == b"image-bytes"
    assert message.body == "look at this"
    assert message.message_type == "caption"
    assert channel.health["attachment_failures"] == 0


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
async def test_telegram_reply_backfills_quoted_media(
    tmp_path: Path,
) -> None:
    channel = _configured_channel(tmp_path, started_at_s=100)
    fake = _FakeTelegramApi(
        files={"quoted-photo": {"file_path": "photos/quoted.jpg"}},
        downloads={"photos/quoted.jpg": (b"quoted-image",)},
    )
    channel._api = _telegram_api(fake)

    await channel._dispatch_update(
        {
            "message": {
                "message_id": 21,
                "date": 101,
                "message_thread_id": 7,
                "chat": {"id": -100456, "type": "supergroup"},
                "from": {"id": 789, "is_bot": False},
                "text": "answer",
                "reply_to_message": {
                    "message_id": 11,
                    "date": 99,
                    "message_thread_id": 7,
                    "chat": {"id": -100456, "type": "supergroup"},
                    "from": {"id": 123, "is_bot": True},
                    "caption": "quoted image",
                    "photo": [{"file_id": "quoted-photo", "file_size": 13}],
                },
            }
        },
        update_id=21,
    )

    quoted = await _read_inbound(channel)
    current = await _read_inbound(channel)

    assert quoted.body == "quoted image"
    assert quoted.metadata["quoted_backfill"] is True
    assert len(quoted.attachments) == 1
    quoted_attachment = quoted.attachments[0]
    assert quoted_attachment.state == "ready"
    assert quoted_attachment.relative_path is not None
    assert (tmp_path / quoted_attachment.relative_path).read_bytes() == b"quoted-image"
    assert current.body == "answer"
    assert current.reply_to_message_id == quoted.message_id
    assert current.attachments == ()


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
