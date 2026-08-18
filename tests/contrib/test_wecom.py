import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from bazaar_compute_node.app.attachments import AttachmentMaterializer
from bazaar_compute_node.contrib.wecom.channel import WeComChannel
from bazaar_compute_node.contrib.wecom.markdown import split_markdown
from bazaar_compute_node.contrib.wecom.outbound import (
    CHUNK_SIZE,
    AttachmentReader,
    encode_request,
    media_type_for_filename,
    prepare_attachments,
    visible_message_body,
)
from bazaar_compute_node.core.channel import (
    ChannelApprovalRequest,
    ChannelContext,
    ChannelSendRequest,
)
from bazaar_compute_node.core.models import (
    ApprovalDecision,
    ApprovalRequest,
    ChannelTargetKind,
    FreshCheckState,
    InboundMessage,
    OutboundAttachment,
    OutboundDeliveryState,
    OutboundMessage,
)
from bazaar_compute_node.core.outcomes import ProviderCallStatus


def test_wecom_does_not_claim_provider_identity(tmp_path: Path) -> None:
    async def referenced_paths() -> set[str]:
        return set()

    channel = WeComChannel(
        ChannelContext(
            agent_id="agent-test",
            attachments=AttachmentMaterializer(lambda: tmp_path, referenced_paths),
            options={},
            workspace=lambda: tmp_path,
        ),
        bot_id="bot-id",
        secret="secret",
        websocket_url="wss://example.invalid",
    )

    assert channel.get_identity() is None


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
async def test_wecom_approval_uses_nested_request_identity(tmp_path: Path) -> None:
    async def referenced_paths() -> set[str]:
        return set()

    channel = WeComChannel(
        ChannelContext(
            agent_id="agent-test",
            attachments=AttachmentMaterializer(lambda: tmp_path, referenced_paths),
            options={},
            workspace=lambda: tmp_path,
        ),
        bot_id="bot-id",
        secret="secret",
        websocket_url="wss://example.invalid",
    )
    approval = ApprovalRequest(
        request_id="approval-1",
        session_id="session-1",
        runtime_session_id="runtime-1",
        action="command_execution",
        created_at_ms=1,
    )

    result = await channel.request_approval(
        ChannelApprovalRequest(
            approval=approval,
            target_kind=ChannelTargetKind.DM,
            provider_thread_id="thread-1",
        ),
        timeout=1,
    )

    assert result.request_id == approval.request_id
    assert result.decision is ApprovalDecision.APPROVED


def test_wecom_outbound_request_codec_uses_explicit_chat_type() -> None:
    body = visible_message_body(
        target_id="group-id",
        target_kind=ChannelTargetKind.GROUP,
        message_type="file",
        content={"media_id": "media-id"},
    )

    frame = json.loads(encode_request("aibot_send_msg", "request-id", body))

    assert frame == {
        "cmd": "aibot_send_msg",
        "headers": {"req_id": "request-id"},
        "body": {
            "chatid": "group-id",
            "chat_type": 2,
            "msgtype": "file",
            "file": {"media_id": "media-id"},
        },
    }
    assert (
        visible_message_body(
            target_id="user-id",
            target_kind=ChannelTargetKind.DM,
            message_type="markdown",
            content={"content": "hello"},
        )["chat_type"]
        == 1
    )


@pytest.mark.parametrize(
    ("name", "content", "expected_type"),
    (
        ("photo.png", b"media-content", "image"),
        ("photo.JPEG", b"media-content", "image"),
        ("recording.amr", b"media-content", "voice"),
        ("clip.mp4", b"media-content", "video"),
        ("report.pdf", b"media-content", "file"),
        ("photo.webp", b"media-content", "file"),
    ),
)
def test_wecom_prepares_provider_media_type(
    tmp_path: Path, name: str, content: bytes, expected_type: str
) -> None:
    source = tmp_path / name
    source.write_bytes(content)
    descriptor = OutboundAttachment(
        name=name,
        relative_path=name,
        media_type=None,
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )

    prepared = prepare_attachments(tmp_path, (descriptor,))[0]

    assert prepared.media_type == expected_type
    assert prepared.size_bytes == len(content)
    assert prepared.md5 == hashlib.md5(content, usedforsecurity=False).hexdigest()


@pytest.mark.parametrize(
    ("name", "expected_type"),
    (
        ("photo.png", "image"),
        ("photo.JPEG", "image"),
        ("recording.amr", "voice"),
        ("clip.mp4", "video"),
        ("report.pdf", "file"),
    ),
)
def test_wecom_maps_supported_filename_formats(name: str, expected_type: str) -> None:
    assert media_type_for_filename(name)[0] == expected_type


def test_wecom_prepares_zero_based_bounded_chunks(tmp_path: Path) -> None:
    content = b"a" * (CHUNK_SIZE + 1)
    source = tmp_path / "report.pdf"
    source.write_bytes(content)
    descriptor = OutboundAttachment(
        name=source.name,
        relative_path=source.name,
        media_type="application/pdf",
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )

    prepared = prepare_attachments(tmp_path, (descriptor,))[0]
    reader = AttachmentReader.open(prepared)
    try:
        chunks = tuple(reader.read_chunk() for _ in range(prepared.total_chunks))
    finally:
        reader.close()

    assert tuple(map(len, chunks)) == (CHUNK_SIZE, 1)
    assert b"".join(chunks) == content


@pytest.mark.parametrize(
    ("name", "size_bytes", "message"),
    (
        ("empty.pdf", 4, "at least 5 bytes"),
        (
            "recording.amr",
            2 * 1024 * 1024 + 1,
            "voice attachment exceeds its size limit",
        ),
    ),
)
def test_wecom_rejects_invalid_attachment_before_upload(
    tmp_path: Path,
    name: str,
    size_bytes: int,
    message: str,
) -> None:
    content = b"x" * size_bytes
    source = tmp_path / name
    source.write_bytes(content)
    descriptor = OutboundAttachment(
        name=name,
        relative_path=name,
        media_type=None,
        size_bytes=size_bytes,
        sha256=hashlib.sha256(content).hexdigest(),
    )

    with pytest.raises(ValueError, match=message):
        prepare_attachments(tmp_path, (descriptor,))


def test_wecom_prepares_current_attachment_content(tmp_path: Path) -> None:
    source = tmp_path / "report.pdf"
    source.write_bytes(b"changed")
    descriptor = OutboundAttachment(
        name=source.name,
        relative_path=source.name,
        media_type="application/pdf",
        size_bytes=len(b"initial"),
        sha256=hashlib.sha256(b"initial").hexdigest(),
    )

    prepared = prepare_attachments(tmp_path, (descriptor,))[0]

    assert prepared.size_bytes == len(b"changed")
    assert prepared.md5 == hashlib.md5(b"changed", usedforsecurity=False).hexdigest()


def test_wecom_delivery_receipt_tracks_visible_parts_and_upload_requests() -> None:
    receipt = WeComChannel._delivery_receipt(
        2,
        1,
        [
            {
                "provider_request_id": "send-1",
                "state": "confirmed",
                "part_type": "markdown",
                "ordinal": 1,
            }
        ],
        [
            {
                "provider_request_id": "upload-1",
                "state": "failed",
                "stage": "init",
                "attachment_ordinal": 1,
            }
        ],
    )

    assert receipt == {
        "total_parts": 2,
        "confirmed_parts": 1,
        "parts": (
            {
                "provider_request_id": "send-1",
                "state": "confirmed",
                "part_type": "markdown",
                "ordinal": 1,
            },
        ),
        "uploads": (
            {
                "provider_request_id": "upload-1",
                "state": "failed",
                "stage": "init",
                "attachment_ordinal": 1,
            },
        ),
        "provider_receipt_ref": "send-1",
    }


def test_wecom_failure_after_visible_part_is_partial(tmp_path: Path) -> None:
    async def referenced_paths() -> set[str]:
        return set()

    channel = WeComChannel(
        ChannelContext(
            agent_id="agent-test",
            attachments=AttachmentMaterializer(lambda: tmp_path, referenced_paths),
            options={},
            workspace=lambda: tmp_path,
        ),
        bot_id="bot-id",
        secret="secret",
        websocket_url="wss://example.invalid",
    )

    result = channel._clear_failure(
        total=2,
        receipts=[
            {
                "provider_request_id": "send-1",
                "state": "confirmed",
                "part_type": "markdown",
                "ordinal": 1,
            }
        ],
        upload_receipts=[
            {
                "provider_request_id": "upload-1",
                "state": "failed",
                "stage": "init",
                "attachment_ordinal": 1,
            }
        ],
        confirmed=1,
        error_kind="provider_rejected_upload",
        error_message="upload failed",
    )

    assert result.status is ProviderCallStatus.PARTIAL
    assert result.value is not None
    assert result.value.provider_receipt_ref == "send-1"


def test_wecom_failure_before_visible_part_is_failed(tmp_path: Path) -> None:
    async def referenced_paths() -> set[str]:
        return set()

    channel = WeComChannel(
        ChannelContext(
            agent_id="agent-test",
            attachments=AttachmentMaterializer(lambda: tmp_path, referenced_paths),
            options={},
            workspace=lambda: tmp_path,
        ),
        bot_id="bot-id",
        secret="secret",
        websocket_url="wss://example.invalid",
    )

    result = channel._clear_failure(
        total=1,
        receipts=[],
        upload_receipts=[
            {
                "provider_request_id": "upload-1",
                "state": "unknown",
                "stage": "finish",
                "attachment_ordinal": 1,
            }
        ],
        confirmed=0,
        error_kind="upload_unknown",
        error_message="upload outcome is unknown",
    )

    assert result.status is ProviderCallStatus.FAILED
    assert result.value is None


@pytest.mark.asyncio
async def test_wecom_send_lock_timeout_does_not_block_later_delivery(
    tmp_path: Path,
) -> None:
    async def referenced_paths() -> set[str]:
        return set()

    channel = WeComChannel(
        ChannelContext(
            agent_id="agent-test",
            attachments=AttachmentMaterializer(lambda: tmp_path, referenced_paths),
            options={},
            workspace=lambda: tmp_path,
        ),
        bot_id="bot-id",
        secret="secret",
        websocket_url="wss://example.invalid",
    )
    outbound = OutboundMessage(
        outbound_message_id="outbound-1",
        command_id="command-1",
        session_id="session-1",
        channel_session_id="channel-session-1",
        target="dm:user-id",
        body="hello",
        state=OutboundDeliveryState.PENDING,
        fresh_check_state=FreshCheckState.PASSED,
        created_at_ms=1,
        snapshot_seq=1,
        current_inbound_seq=1,
    )
    request = ChannelSendRequest(
        outbound=outbound,
        target_kind=ChannelTargetKind.DM,
        provider_thread_id="user-id",
    )
    await channel._send_lock.acquire()

    result = await channel.send(request, timeout=0.001)

    assert result.status is ProviderCallStatus.FAILED
    assert result.error_kind == "delivery_timeout"
    channel._send_lock.release()
    await asyncio.wait_for(channel._send_lock.acquire(), timeout=0.1)
    channel._send_lock.release()


@pytest.mark.asyncio
async def test_wecom_does_not_emit_provider_events_as_inbound_messages(
    tmp_path: Path,
) -> None:
    async def referenced_paths() -> set[str]:
        return set()

    channel = WeComChannel(
        ChannelContext(
            agent_id="agent-test",
            attachments=AttachmentMaterializer(
                lambda: tmp_path,
                referenced_paths,
            ),
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
    async def referenced_paths() -> set[str]:
        return set()

    channel = WeComChannel(
        ChannelContext(
            agent_id="agent-test",
            attachments=AttachmentMaterializer(lambda: tmp_path, referenced_paths),
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


@pytest.mark.asyncio
async def test_wecom_emits_quoted_text_before_the_current_message(
    tmp_path: Path,
) -> None:
    async def referenced_paths() -> set[str]:
        return set()

    channel = WeComChannel(
        ChannelContext(
            agent_id="agent-test",
            attachments=AttachmentMaterializer(lambda: tmp_path, referenced_paths),
            options={},
            workspace=lambda: tmp_path,
        ),
        bot_id="bot-id",
        secret="secret",
        websocket_url="wss://example.invalid",
    )

    def callback(message_id: str, current_text: str) -> dict[str, object]:
        return {
            "cmd": "aibot_msg_callback",
            "headers": {"req_id": f"request-{message_id}"},
            "body": {
                "msgid": message_id,
                "create_time": 123,
                "aibotid": "bot-id",
                "from": {"userid": "user-id"},
                "chattype": "single",
                "msgtype": "text",
                "text": {"content": current_text},
                "quote": {
                    "msgtype": "text",
                    "text": {"content": "The original quoted text."},
                },
            },
        }

    await channel._receive_message(
        callback("message-with-quote", "Can you see the quoted text?")
    )

    referenced = channel._inbound.get_nowait()
    current = channel._inbound.get_nowait()
    assert isinstance(referenced, InboundMessage)
    assert isinstance(current, InboundMessage)
    assert referenced.body == "The original quoted text."
    assert referenced.sender is None
    assert referenced.notifies_runtime is False
    assert referenced.mentions_agent is False
    assert current.body == "Can you see the quoted text?"
    assert current.message_id != current.provider_message_id
    assert current.reply_to_message_id == referenced.message_id
    assert current.session_id == referenced.session_id
    assert current.canonical_target == referenced.canonical_target
    assert "has_quote" not in current.metadata
    assert len(referenced.provider_message_id) == 64

    await channel._receive_message(
        callback("another-message-with-quote", "Can you still see it?")
    )
    repeated_reference = channel._inbound.get_nowait()
    another_current = channel._inbound.get_nowait()
    assert isinstance(repeated_reference, InboundMessage)
    assert isinstance(another_current, InboundMessage)
    assert repeated_reference.provider_message_id == referenced.provider_message_id
    assert repeated_reference.message_id == referenced.message_id
    assert another_current.message_id != current.message_id
    assert another_current.reply_to_message_id == referenced.message_id
