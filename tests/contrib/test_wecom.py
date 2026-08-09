from pathlib import Path

import pytest

from bazaar_compute_node.app.attachments import AttachmentMaterializer
from bazaar_compute_node.contrib.wecom.channel import WeComChannel
from bazaar_compute_node.contrib.wecom.markdown import split_markdown
from bazaar_compute_node.core.channel import ChannelContext
from bazaar_compute_node.core.models import InboundMessage


def test_wecom_markdown_split_preserves_unicode_and_block_boundaries() -> None:
    content = ("Heading\n\nParagraph with \u4f60\u597d.\n\n" * 20).rstrip()

    chunks = split_markdown(content, limit=128)

    assert len(chunks) > 1
    assert "".join(chunks) == content
    assert all(len(chunk.encode("utf-8")) <= 128 for chunk in chunks)


def test_wecom_markdown_split_closes_and_reopens_fenced_blocks() -> None:
    content = "```python\n" + ("print('\u4f60\u597d')\n" * 40) + "```"

    chunks = split_markdown(content, limit=96)

    assert len(chunks) > 1
    assert all(len(chunk.encode("utf-8")) <= 96 for chunk in chunks)
    assert all(chunk.startswith("```python\n") for chunk in chunks)
    assert all(chunk.endswith("```") for chunk in chunks)


def test_wecom_filename_decodes_provider_content_disposition() -> None:
    assert (
        WeComChannel._filename('attachment; filename="%E6%8A%A5%E5%91%8A.pdf"', "file")
        == "\u62a5\u544a.pdf"
    )
    assert (
        WeComChannel._filename(
            "attachment; filename*=UTF-8''%E6%8A%A5%E5%91%8A.pdf", "file"
        )
        == "\u62a5\u544a.pdf"
    )
    assert WeComChannel._filename(None, "video") == "video.bin"


@pytest.mark.asyncio
async def test_wecom_does_not_emit_provider_events_as_inbound_messages(
    tmp_path: Path,
) -> None:
    async def inbound_exists(_channel: str, _provider_message_id: str) -> bool:
        return False

    async def referenced_paths() -> set[str]:
        return set()

    channel = WeComChannel(
        ChannelContext(
            attachments=AttachmentMaterializer(
                lambda: tmp_path,
                referenced_paths,
            ),
            inbound_exists=inbound_exists,
            options={},
            workspace=lambda: tmp_path,
        ),
        bot_id="bot-id",
        secret="secret",
        websocket_url="wss://example.invalid",
    )
    for message_id, event_type in (
        ("event-1", "enter_chat"),
        ("event-2", "future_event"),
    ):
        await channel._receive_message(
            {
                "cmd": "aibot_event_callback",
                "headers": {"req_id": f"request-{message_id}"},
                "body": {
                    "msgid": message_id,
                    "create_time": 123,
                    "aibotid": "bot-id",
                    "from": {"userid": "user-id"},
                    "msgtype": "event",
                    "event": {"eventtype": event_type},
                },
            }
        )

    assert channel.health["ignored_event_frames"] == 2
    assert channel._inbound.empty()


@pytest.mark.asyncio
async def test_wecom_does_not_persist_inbound_request_id(tmp_path: Path) -> None:
    async def inbound_exists(_channel: str, _provider_message_id: str) -> bool:
        return False

    async def referenced_paths() -> set[str]:
        return set()

    channel = WeComChannel(
        ChannelContext(
            attachments=AttachmentMaterializer(lambda: tmp_path, referenced_paths),
            inbound_exists=inbound_exists,
            options={},
            workspace=lambda: tmp_path,
        ),
        bot_id="bot-id",
        secret="secret",
        websocket_url="wss://example.invalid",
    )
    await channel._receive_message(
        {
            "cmd": "aibot_msg_callback",
            "headers": {"req_id": "inbound-request-id"},
            "body": {
                "msgid": "message-1",
                "create_time": 123,
                "aibotid": "bot-id",
                "from": {"userid": "user-id"},
                "chattype": "single",
                "msgtype": "text",
                "text": {"content": "hello"},
            },
        }
    )

    inbound = channel._inbound.get_nowait()
    assert isinstance(inbound, InboundMessage)
    assert inbound.provider_payload_ref is None
