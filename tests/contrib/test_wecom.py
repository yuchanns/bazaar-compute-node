import asyncio
import hashlib
import json
import socket
from pathlib import Path
from typing import cast

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from bazaar_compute_node.app.attachments import AttachmentMaterializer
from bazaar_compute_node.contrib.wecom.channel import (
    WeComChannel,
    _Delivery,
)
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
    ChannelIdentity,
    ChannelSendRequest,
)
from bazaar_compute_node.core.models import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalResult,
    ChannelTargetKind,
    Message,
    OutboundAttachment,
    SenderIdentity,
    SenderKind,
)
from bazaar_compute_node.core.outcomes import ProviderCallStatus
from bazaar_compute_node.core.timerwheel import TimerWheel
from bazaar_compute_node.core.utils.markdown import split_markdown, utf8_bytes


def test_wecom_exposes_provider_id_without_display_name(tmp_path: Path) -> None:
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

    assert channel.get_identity() == ChannelIdentity(id="bot-id")


def test_wecom_markdown_split() -> None:
    # unicode and block boundaries survive a split
    content = ("Heading\n\nParagraph with \u4f60\u597d.\n\n" * 20).rstrip()

    chunks = split_markdown(content, limit=128, measure=utf8_bytes)

    assert len(chunks) > 1
    assert "".join(chunks) == content
    assert all(len(chunk.encode("utf-8")) <= 128 for chunk in chunks)

    # a fenced block is closed and reopened across parts
    content = "```python\n" + ("print('\u4f60\u597d')\n" * 40) + "```"

    chunks = split_markdown(content, limit=96, measure=utf8_bytes)

    assert len(chunks) > 1
    assert all(len(chunk.encode("utf-8")) <= 96 for chunk in chunks)
    assert all(chunk.startswith("```python\n") for chunk in chunks)
    assert all(chunk.endswith("```") for chunk in chunks)


def test_wecom_markdown_closes_a_fence_that_opens_a_continuation() -> None:
    # a closing fence can land at the start of a continuation part; the reopened
    # fence has already put it at a line boundary, so it closes the block there
    content = "```py \n```py~~~```"

    chunks = split_markdown(content, limit=12, measure=utf8_bytes)

    assert chunks[-1] == "```py\n```"
    assert all(chunk.startswith("```py") for chunk in chunks[1:])


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
@pytest.mark.parametrize(
    ("event_key", "decision", "status"),
    (
        ("bcn_approve", ApprovalDecision.APPROVED, "Approved"),
        ("bcn_reject", ApprovalDecision.REJECTED, "Rejected"),
    ),
)
async def test_wecom_approval_card_event_updates_card_and_wakes_request(
    tmp_path: Path,
    event_key: str,
    decision: ApprovalDecision,
    status: str,
) -> None:
    loop = asyncio.get_running_loop()
    sent_card: asyncio.Future[dict[str, object]] = loop.create_future()
    updated_card: asyncio.Future[dict[str, object]] = loop.create_future()

    async def websocket(request: web.Request) -> web.WebSocketResponse:
        connection = web.WebSocketResponse()
        await connection.prepare(request)
        subscribe = await connection.receive_json()
        await connection.send_json(
            {"headers": subscribe["headers"], "errcode": 0, "errmsg": "ok"}
        )
        frame = await connection.receive_json()
        sent_card.set_result(frame)
        await connection.send_json(
            {"headers": frame["headers"], "errcode": 0, "errmsg": "ok"}
        )
        task_id = frame["body"]["template_card"]["task_id"]
        await connection.send_json(
            {
                "cmd": "aibot_event_callback",
                "headers": {"req_id": "card-event-request"},
                "body": {
                    "event": {
                        "eventtype": "template_card_event",
                        "template_card_event": {
                            "card_type": "button_interaction",
                            "event_key": event_key,
                            "task_id": task_id,
                        },
                    }
                },
            }
        )
        updated_card.set_result(await connection.receive_json())
        await connection.receive()
        return connection

    application = web.Application()
    application.router.add_get("/ws", websocket)
    server = TestServer(application)
    timer_wheel = TimerWheel()
    await server.start_server()
    await timer_wheel.start()

    async def referenced_paths_2() -> set[str]:
        return set()

    channel = WeComChannel(
        ChannelContext(
            agent_id="agent-test",
            attachments=AttachmentMaterializer(lambda: tmp_path, referenced_paths_2),
            options={},
            workspace=lambda: tmp_path,
            timer_wheel=timer_wheel,
        ),
        bot_id="bot-id",
        secret="secret",
        websocket_url=str(server.make_url("/ws")),
    )
    try:
        await channel.start(timeout=1)
        approval = ApprovalRequest(
            request_id="approval-1",
            session_id="session-1",
            runtime_session_id="runtime-1",
            action="command_execution",
            created_at_ms=1,
            details={"reason": "Run the requested command."},
        )
        result = await channel.request_approval(
            ChannelApprovalRequest(
                approval=approval,
                target_kind=ChannelTargetKind.DM,
                provider_thread_id="user-id",
            ),
            timeout=1,
        )
        sent = await asyncio.wait_for(sent_card, timeout=1)
        updated = await asyncio.wait_for(updated_card, timeout=1)
    finally:
        await channel.stop(timeout=1)
        await timer_wheel.close()
        await server.close()

    body = cast(dict[str, object], sent["body"])
    card = cast(dict[str, object], body["template_card"])
    main_title = cast(dict[str, object], card["main_title"])
    buttons = cast(list[dict[str, object]], card["button_list"])
    assert sent["cmd"] == "aibot_send_msg"
    assert body["chatid"] == "user-id"
    assert body["chat_type"] == 1
    assert card["card_type"] == "button_interaction"
    assert main_title["desc"] == "command execution"
    assert card["sub_title_text"] == "Run the requested command."
    assert [button["key"] for button in buttons] == [
        "bcn_approve",
        "bcn_reject",
    ]
    assert result.request_id == approval.request_id
    assert result.decision is decision
    body = cast(dict[str, object], updated["body"])
    terminal = cast(dict[str, object], body["template_card"])
    buttons = cast(list[dict[str, object]], terminal["button_list"])
    assert updated["cmd"] == "aibot_respond_update_msg"
    assert cast(dict[str, object], updated["headers"])["req_id"] == (
        "card-event-request"
    )
    assert body["response_type"] == "update_template_card"
    assert terminal["task_id"] == card["task_id"]
    assert buttons[0]["text"] == status
    assert channel.health["approval_card_update_unknown"] == 1
    assert channel.health["last_approval_card_update_disposition"] == (
        "sent_outcome_unknown"
    )


@pytest.mark.asyncio
async def test_wecom_slow_card_update_does_not_block_another_session(
    tmp_path: Path,
) -> None:
    loop = asyncio.get_running_loop()
    callback_sent: asyncio.Future[None] = loop.create_future()
    disconnect = asyncio.Event()

    async def websocket(request: web.Request) -> web.WebSocketResponse:
        connection = web.WebSocketResponse(max_msg_size=32 * 1024 * 1024)
        await connection.prepare(request)
        subscribe = await connection.receive_json()
        await connection.send_json({"headers": subscribe["headers"], "errcode": 0})
        card = await connection.receive_json()
        await connection.send_json({"headers": card["headers"], "errcode": 0})
        task_id = card["body"]["template_card"]["task_id"]
        transport = request.transport
        assert transport is not None
        server_socket = transport.get_extra_info("socket")
        assert server_socket is not None
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        await connection.send_json(
            {
                "cmd": "aibot_event_callback",
                "headers": {"req_id": "slow-card-update"},
                "body": {
                    "event": {
                        "eventtype": "template_card_event",
                        "template_card_event": {
                            "card_type": "button_interaction",
                            "event_key": "bcn_approve",
                            "task_id": task_id,
                        },
                    }
                },
            }
        )
        await connection.send_json(
            {
                "cmd": "aibot_msg_callback",
                "headers": {"req_id": "other-session-message"},
                "body": {
                    "msgid": "message-from-another-session",
                    "create_time": 123,
                    "aibotid": "bot-id",
                    "from": {"userid": "other-user"},
                    "chattype": "single",
                    "msgtype": "text",
                    "text": {"content": "Can this session still get through?"},
                },
            }
        )
        transport.pause_reading()
        callback_sent.set_result(None)
        await disconnect.wait()
        transport.close()
        return connection

    application = web.Application()
    application.router.add_get("/ws", websocket)
    server = TestServer(application)
    timer_wheel = TimerWheel()
    await server.start_server()
    await timer_wheel.start()

    async def referenced_paths() -> set[str]:
        return set()

    channel = WeComChannel(
        ChannelContext(
            agent_id="agent-test",
            attachments=AttachmentMaterializer(lambda: tmp_path, referenced_paths),
            options={},
            workspace=lambda: tmp_path,
            timer_wheel=timer_wheel,
        ),
        bot_id="bot-id",
        secret="secret",
        websocket_url=str(server.make_url("/ws")),
    )
    approval_task: asyncio.Task[ApprovalResult] | None = None
    inbound = channel.receive()
    try:
        await channel.start(timeout=1)
        approval_task = asyncio.create_task(
            channel.request_approval(
                ChannelApprovalRequest(
                    approval=ApprovalRequest(
                        request_id="approval-with-slow-update",
                        session_id="session-1",
                        runtime_session_id="runtime-1",
                        action="command_execution",
                        created_at_ms=1,
                        details={"reason": "x" * (16 * 1024 * 1024)},
                    ),
                    target_kind=ChannelTargetKind.DM,
                    provider_thread_id="user-id",
                ),
                timeout=5,
            )
        )
        await asyncio.wait_for(callback_sent, timeout=5)
        result = await asyncio.wait_for(approval_task, timeout=1)
        approval_task = None
        message = await asyncio.wait_for(anext(inbound), timeout=1)
        assert result.decision is ApprovalDecision.APPROVED
        assert message.provider_message_id == "message-from-another-session"
        pending_updates = channel.health["approval_card_updates_pending"]
        assert isinstance(pending_updates, int)
        assert pending_updates > 0
        async with asyncio.timeout(6):
            while channel.health["approval_card_updates_pending"]:
                await asyncio.sleep(0.05)
    finally:
        disconnect.set()
        if approval_task is not None and not approval_task.done():
            approval_task.cancel()
            await asyncio.gather(approval_task, return_exceptions=True)
        await channel.stop(timeout=5)
        await timer_wheel.close()
        await server.close()

    attempts = channel.health["approval_card_update_attempts"]
    unknown = channel.health["approval_card_update_unknown"]
    assert channel.health["approval_card_updates_pending"] == 0
    assert isinstance(attempts, int)
    assert isinstance(unknown, int)
    assert attempts >= 1
    assert unknown >= 1


@pytest.mark.asyncio
async def test_wecom_approval_cancellation_cleans_pending_request(
    tmp_path: Path,
) -> None:
    loop = asyncio.get_running_loop()
    first_card_sent: asyncio.Future[None] = loop.create_future()

    async def websocket(request: web.Request) -> web.WebSocketResponse:
        connection = web.WebSocketResponse()
        await connection.prepare(request)
        subscribe = await connection.receive_json()
        await connection.send_json({"headers": subscribe["headers"], "errcode": 0})
        frame = await connection.receive_json()
        await connection.send_json({"headers": frame["headers"], "errcode": 0})
        first_card_sent.set_result(None)
        frame = await connection.receive_json()
        await connection.send_json({"headers": frame["headers"], "errcode": 0})
        task_id = frame["body"]["template_card"]["task_id"]
        await connection.send_json(
            {
                "cmd": "aibot_event_callback",
                "headers": {"req_id": "retry-card-event"},
                "body": {
                    "event": {
                        "eventtype": "template_card_event",
                        "template_card_event": {
                            "card_type": "button_interaction",
                            "event_key": "bcn_approve",
                            "task_id": task_id,
                        },
                    }
                },
            }
        )
        await connection.receive_json()
        await connection.receive()
        return connection

    application = web.Application()
    application.router.add_get("/ws", websocket)
    server = TestServer(application)
    timer_wheel = TimerWheel()
    await server.start_server()
    await timer_wheel.start()

    async def referenced_paths_3() -> set[str]:
        return set()

    channel = WeComChannel(
        ChannelContext(
            agent_id="agent-test",
            attachments=AttachmentMaterializer(lambda: tmp_path, referenced_paths_3),
            options={},
            workspace=lambda: tmp_path,
            timer_wheel=timer_wheel,
        ),
        bot_id="bot-id",
        secret="secret",
        websocket_url=str(server.make_url("/ws")),
    )
    approval = ChannelApprovalRequest(
        approval=ApprovalRequest(
            request_id="approval-cancelled",
            session_id="session-1",
            runtime_session_id="runtime-1",
            action="permissions",
            created_at_ms=1,
        ),
        target_kind=ChannelTargetKind.GROUP,
        provider_thread_id="group-id",
    )
    approval_task: asyncio.Task[ApprovalResult] | None = None
    try:
        await channel.start(timeout=1)
        approval_task = asyncio.create_task(
            channel.request_approval(approval, timeout=1)
        )
        await asyncio.wait_for(first_card_sent, timeout=1)
        approval_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await approval_task
        approval_task = None
        result = await channel.request_approval(approval, timeout=1)
    finally:
        if approval_task is not None and not approval_task.done():
            approval_task.cancel()
            await asyncio.gather(approval_task, return_exceptions=True)
        await channel.stop(timeout=1)
        await timer_wheel.close()
        await server.close()

    assert result.request_id == "approval-cancelled"
    assert result.decision is ApprovalDecision.APPROVED


@pytest.mark.asyncio
async def test_wecom_stop_rejects_pending_approval(tmp_path: Path) -> None:
    loop = asyncio.get_running_loop()
    sent_card: asyncio.Future[None] = loop.create_future()

    async def websocket(request: web.Request) -> web.WebSocketResponse:
        connection = web.WebSocketResponse()
        await connection.prepare(request)
        subscribe = await connection.receive_json()
        await connection.send_json({"headers": subscribe["headers"], "errcode": 0})
        frame = await connection.receive_json()
        await connection.send_json({"headers": frame["headers"], "errcode": 0})
        sent_card.set_result(None)
        await connection.receive()
        return connection

    application = web.Application()
    application.router.add_get("/ws", websocket)
    server = TestServer(application)
    timer_wheel = TimerWheel()
    await server.start_server()
    await timer_wheel.start()

    async def referenced_paths_4() -> set[str]:
        return set()

    channel = WeComChannel(
        ChannelContext(
            agent_id="agent-test",
            attachments=AttachmentMaterializer(lambda: tmp_path, referenced_paths_4),
            options={},
            workspace=lambda: tmp_path,
            timer_wheel=timer_wheel,
        ),
        bot_id="bot-id",
        secret="secret",
        websocket_url=str(server.make_url("/ws")),
    )
    approval_task: asyncio.Task[ApprovalResult] | None = None
    try:
        await channel.start(timeout=1)
        approval_task = asyncio.create_task(
            channel.request_approval(
                ChannelApprovalRequest(
                    approval=ApprovalRequest(
                        request_id="approval-stopped",
                        session_id="session-1",
                        runtime_session_id="runtime-1",
                        action="file_change",
                        created_at_ms=1,
                    ),
                    target_kind=ChannelTargetKind.DM,
                    provider_thread_id="user-id",
                ),
                timeout=1,
            )
        )
        await asyncio.wait_for(sent_card, timeout=1)
        await channel.stop(timeout=1)
        result = await asyncio.wait_for(approval_task, timeout=1)
    finally:
        if approval_task is not None and not approval_task.done():
            approval_task.cancel()
            await asyncio.gather(approval_task, return_exceptions=True)
        if channel.health["state"] != "stopped":
            await channel.stop(timeout=1)
        await timer_wheel.close()
        await server.close()

    assert result.request_id == "approval-stopped"
    assert result.decision is ApprovalDecision.REJECTED
    assert result.reason == "channel_stopped"


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


def test_wecom_delivery_outcomes(tmp_path: Path) -> None:
    # a receipt tracks visible parts and upload requests
    receipt = _Delivery(
        total=2,
        confirmed=1,
        receipts=[
            {
                "provider_request_id": "send-1",
                "state": "confirmed",
                "part_type": "markdown",
                "ordinal": 1,
            }
        ],
        uploads=[
            {
                "provider_request_id": "upload-1",
                "state": "failed",
                "stage": "init",
                "attachment_ordinal": 1,
            }
        ],
    ).receipt()

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

    # failing after a visible part is partial
    async def referenced_paths_3() -> set[str]:
        return set()

    channel = WeComChannel(
        ChannelContext(
            agent_id="agent-test",
            attachments=AttachmentMaterializer(lambda: tmp_path, referenced_paths_3),
            options={},
            workspace=lambda: tmp_path,
        ),
        bot_id="bot-id",
        secret="secret",
        websocket_url="wss://example.invalid",
    )

    result = channel._clear_failure(
        _Delivery(
            total=2,
            confirmed=1,
            receipts=[
                {
                    "provider_request_id": "send-1",
                    "state": "confirmed",
                    "part_type": "markdown",
                    "ordinal": 1,
                }
            ],
            uploads=[
                {
                    "provider_request_id": "upload-1",
                    "state": "failed",
                    "stage": "init",
                    "attachment_ordinal": 1,
                }
            ],
        ),
        error_kind="provider_rejected_upload",
        error_message="upload failed",
    )

    assert result.status is ProviderCallStatus.PARTIAL
    assert result.value is not None
    assert result.value.provider_receipt_ref == "send-1"

    # failing before any visible part is a failure
    async def referenced_paths_4() -> set[str]:
        return set()

    channel = WeComChannel(
        ChannelContext(
            agent_id="agent-test",
            attachments=AttachmentMaterializer(lambda: tmp_path, referenced_paths_4),
            options={},
            workspace=lambda: tmp_path,
        ),
        bot_id="bot-id",
        secret="secret",
        websocket_url="wss://example.invalid",
    )

    result = channel._clear_failure(
        _Delivery(
            total=1,
            uploads=[
                {
                    "provider_request_id": "upload-1",
                    "state": "unknown",
                    "stage": "finish",
                    "attachment_ordinal": 1,
                }
            ],
        ),
        error_kind="upload_unknown",
        error_message="upload outcome is unknown",
    )

    assert result.status is ProviderCallStatus.FAILED
    assert result.value is None


@pytest.mark.asyncio
async def test_wecom_send_lock_timeout_does_not_block_later_delivery(
    tmp_path: Path,
) -> None:
    async def referenced_paths_5() -> set[str]:
        return set()

    channel = WeComChannel(
        ChannelContext(
            agent_id="agent-test",
            attachments=AttachmentMaterializer(lambda: tmp_path, referenced_paths_5),
            options={},
            workspace=lambda: tmp_path,
        ),
        bot_id="bot-id",
        secret="secret",
        websocket_url="wss://example.invalid",
    )
    request = ChannelSendRequest(
        session_id="session-1",
        body="hello",
        attachments=(),
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
    async def referenced_paths_6() -> set[str]:
        return set()

    channel = WeComChannel(
        ChannelContext(
            agent_id="agent-test",
            attachments=AttachmentMaterializer(
                lambda: tmp_path,
                referenced_paths_6,
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
    async def referenced_paths_7() -> set[str]:
        return set()

    channel = WeComChannel(
        ChannelContext(
            agent_id="agent-test",
            attachments=AttachmentMaterializer(lambda: tmp_path, referenced_paths_7),
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
    assert isinstance(inbound, Message)
    assert inbound.provider_payload_ref is None
    assert inbound.sender == SenderIdentity(id="user-id")
    assert inbound.sender_kind is SenderKind.HUMAN


@pytest.mark.asyncio
async def test_wecom_emits_quoted_text_before_the_current_message(
    tmp_path: Path,
) -> None:
    async def referenced_paths_8() -> set[str]:
        return set()

    channel = WeComChannel(
        ChannelContext(
            agent_id="agent-test",
            attachments=AttachmentMaterializer(lambda: tmp_path, referenced_paths_8),
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
    assert isinstance(referenced, Message)
    assert isinstance(current, Message)
    assert referenced.body == "The original quoted text."
    assert referenced.sender is None
    assert referenced.sender_kind is SenderKind.HUMAN
    assert referenced.notifies_runtime is False
    assert referenced.mentions_agent is False
    assert current.body == "Can you see the quoted text?"
    assert current.sender_kind is SenderKind.HUMAN
    assert current.message_id != current.provider_message_id
    assert current.reply_to_message_id == referenced.message_id
    assert current.session_id == referenced.session_id
    assert current.target == referenced.target
    assert "has_quote" not in current.metadata
    assert referenced.provider_message_id is not None
    assert len(referenced.provider_message_id) == 64

    await channel._receive_message(
        callback("another-message-with-quote", "Can you still see it?")
    )
    repeated_reference = channel._inbound.get_nowait()
    another_current = channel._inbound.get_nowait()
    assert isinstance(repeated_reference, Message)
    assert isinstance(another_current, Message)
    assert repeated_reference.provider_message_id == referenced.provider_message_id
    assert repeated_reference.message_id == referenced.message_id
    assert another_current.message_id != current.message_id
    assert another_current.reply_to_message_id == referenced.message_id
