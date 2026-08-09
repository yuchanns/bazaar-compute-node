from pathlib import Path

import pytest

from bazaar_compute_node.app.attachments import AttachmentMaterializer
from bazaar_compute_node.contrib.wecom.channel import WeComChannel
from bazaar_compute_node.core.channel import ChannelContext


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
