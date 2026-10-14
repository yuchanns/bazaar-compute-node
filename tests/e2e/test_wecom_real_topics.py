from __future__ import annotations

import asyncio
import os
import struct
import zlib
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote
from uuid import uuid4

import aiohttp
import pytest

from bazaar_compute_node.app.attachments import AttachmentMaterializer
from bazaar_compute_node.contrib.sqlite.storage import SqliteDatabase
from bazaar_compute_node.contrib.wecom.channel import WeComChannel
from bazaar_compute_node.contrib.wecom.identity import (
    TopicAddress,
    conversation_thread_id,
)
from bazaar_compute_node.contrib.wecom.topic_api import Members, TopicClient
from bazaar_compute_node.core.channel import ChannelContext, ChannelSendRequest
from bazaar_compute_node.core.models import ChannelTargetKind, Review
from bazaar_compute_node.core.orchestration.commands.messages import (
    OutboundAttachmentResolver,
)
from bazaar_compute_node.core.outcomes import ProviderCallStatus
from bazaar_compute_node.core.timerwheel import TimerWheel
from bazaar_compute_node.core.utils.clock import now_ms

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_native_posts_replies_mentions_and_files_on_the_real_provider(
    tmp_path: Path,
) -> None:
    """Read back delivered content and bytes from the real test channel."""

    bot_id = os.environ["BCN_WECOM_BOT_ID"]
    secret = os.environ["BCN_WECOM_SECRET"]
    topic_id = os.environ["BCN_WECOM_TOPIC_ID"]

    async def referenced() -> set[str]:
        return set()

    wheel = TimerWheel()
    await wheel.start()
    channel = WeComChannel(
        ChannelContext(
            agent_id="isolated-wecom-e2e",
            workspace=lambda: tmp_path,
            attachments=AttachmentMaterializer(lambda: tmp_path, referenced),
            options={},
            timer_wheel=wheel,
        ),
        bot_id=bot_id,
        secret=secret,
        websocket_url="wss://openws.work.weixin.qq.com",
    )
    try:
        await channel.start(timeout=30)
        members = await channel._topics.client.call(
            "topic/get_topic_member_list",
            {"topic_id": topic_id, "limit": 200},
            Members,
        )
        userid = next(member.id for member in members.member_list if member.type == 0)
        nonce = uuid4().hex[:8]
        text = f"企业微信频道接入验证 {nonce}。请 @{userid} 看看附件；`@{userid}` 是代码示例。"
        file = tmp_path / "channel-check.txt"
        file.write_bytes(
            f"同一个机器人支持普通会话与频道。验证编号：{nonce}\n".encode() * 10_000
        )
        copy = tmp_path / "channel-check-copy.txt"
        copy.write_bytes(file.read_bytes())
        image = tmp_path / "channel-check.png"
        pixels = b"".join(
            b"\x00"
            + b"".join(
                bytes((30, 120, 220))
                if (x // 32 + y // 32) % 2
                else bytes((240, 130, 40))
                for x in range(256)
            )
            for y in range(192)
        )
        chunks = [
            (b"IHDR", struct.pack("!IIBBBBB", 256, 192, 8, 2, 0, 0, 0)),
            (b"IDAT", zlib.compress(pixels)),
            (b"IEND", b""),
        ]
        image.write_bytes(
            b"\x89PNG\r\n\x1a\n"
            + b"".join(
                struct.pack("!I", len(data))
                + kind
                + data
                + struct.pack("!I", zlib.crc32(kind + data))
                for kind, data in chunks
            )
        )
        attachments = OutboundAttachmentResolver(lambda: tmp_path)(
            (str(file), str(copy), str(image))
        )
        root = TopicAddress(bot_id, topic_id)
        sent = await channel.send(
            ChannelSendRequest(
                session_id="isolated-wecom-e2e",
                body=text,
                attachments=attachments,
                target_kind=ChannelTargetKind.GROUP,
                provider_thread_id=root.provider_thread_id,
            ),
            timeout=60,
        )
        assert sent.status is ProviderCallStatus.CONFIRMED, sent.error_message
        assert sent.value is not None and sent.value.provider_thread_id is not None
        address = TopicAddress.parse(sent.value.provider_thread_id)
        assert address is not None and address.post_id is not None
        inbound = channel.receive()
        async with asyncio.timeout(5):
            while True:
                message = await anext(inbound)
                if message.provider_message_id == sent.value.provider_message_id:
                    break
        database = SqliteDatabase(database_name="native-post.sqlite3")
        await database.start(timeout=5)
        try:
            scope = database.scope("isolated-wecom-e2e", "WeCom test")
            await scope.record_inbound(
                message, now_ms=now_ms(), opening=Review.APPROVED
            )
            stored = await scope.get_message(message.message_id)
            assert stored is not None
            assert {file.name, copy.name, image.name}.issubset(
                {attachment.name for attachment in stored.attachments}
            )
        finally:
            await database.stop(timeout=5)
        post = await channel._topics.client.post(topic_id, address.post_id)
        assert nonce in post.content
        assert any(item.member.id == userid for item in post.inline_mention_list)
        assert f"`@{userid}`" in post.content
        for item in post.inline_file_list:
            download = await channel._topics._download(
                item, name=item.file_name, kind="file"
            )
            assert download.relative_path is not None
            assert nonce in (tmp_path / download.relative_path).read_text()
            assert (tmp_path / download.relative_path).read_bytes() == file.read_bytes()
        assert post.inline_file_list and post.image_list
        download = await channel._topics._download(
            post.image_list[0], name=image.name, kind="image"
        )
        assert download.relative_path is not None
        assert (tmp_path / download.relative_path).read_bytes() == image.read_bytes()
        reply = await channel.send(
            ChannelSendRequest(
                session_id="isolated-wecom-e2e",
                body="帖子和附件已收到，继续讨论这次频道接入。",
                attachments=(),
                target_kind=ChannelTargetKind.GROUP,
                provider_thread_id=address.provider_thread_id,
            ),
            timeout=30,
        )
        assert reply.status is ProviderCallStatus.CONFIRMED, reply.error_message
        assert reply.value is not None and reply.value.provider_message_id is not None
        reply_id = unquote(reply.value.provider_message_id.split(":")[2])
        delivered = await channel._topics.client.reply(
            topic_id, address.post_id, reply_id
        )
        assert "频道接入" in delivered.content
        nested = await channel.send(
            ChannelSendRequest(
                session_id="isolated-wecom-e2e",
                body="这是对上一条回复的回复，关联关系也一起核对。",
                attachments=(),
                target_kind=ChannelTargetKind.GROUP,
                provider_thread_id=address.provider_thread_id,
                provider_reply_to_message_id=reply.value.provider_message_id,
            ),
            timeout=30,
        )
        assert nested.status is ProviderCallStatus.CONFIRMED, nested.error_message
        assert nested.value is not None and nested.value.provider_message_id is not None
        nested_id = unquote(nested.value.provider_message_id.split(":")[2])
        delivered = await channel._topics.client.reply(
            topic_id, address.post_id, nested_id
        )
        assert delivered.parent_reply_id == reply_id
        image_only = await channel.send(
            ChannelSendRequest(
                session_id="isolated-wecom-e2e",
                body="",
                attachments=(attachments[2],),
                target_kind=ChannelTargetKind.GROUP,
                provider_thread_id=address.provider_thread_id,
            ),
            timeout=30,
        )
        assert image_only.status is ProviderCallStatus.CONFIRMED, (
            image_only.error_message
        )

        # Observe a real HTTP send in flight before stopping its owning channel.
        started = asyncio.Event()

        async def sending(
            session: aiohttp.ClientSession,
            context: SimpleNamespace,
            params: aiohttp.TraceRequestStartParams,
        ) -> None:
            del session, context
            if params.url.path.endswith("/topic/send_reply"):
                started.set()

        trace = aiohttp.TraceConfig()
        trace.on_request_start.append(sending)
        trace.freeze()
        session = channel._topics.client.session
        session.trace_configs.append(trace)
        request = ChannelSendRequest(
            session_id="isolated-wecom-e2e",
            body=f"验证关闭前已开始发送的评论能够送达。{nonce}",
            attachments=(),
            target_kind=ChannelTargetKind.GROUP,
            provider_thread_id=address.provider_thread_id,
        )
        delivery = asyncio.create_task(channel.send(request, timeout=30))
        await asyncio.wait_for(started.wait(), timeout=10)
        queued = asyncio.create_task(channel.send(request, timeout=30))
        await asyncio.sleep(0)
        await channel.stop(timeout=10)
        sent = await delivery
        stopped = await queued
        assert session.closed
        assert sent.status is ProviderCallStatus.CONFIRMED, sent.error_message
        assert stopped.status is ProviderCallStatus.FAILED, stopped.error_message
        assert sent.value is not None and sent.value.provider_message_id is not None
        reply_id = unquote(sent.value.provider_message_id.split(":")[2])
        verifier = TopicClient(bot_id, secret)
        try:
            delivered = await verifier.reply(topic_id, address.post_id, reply_id)
            assert nonce in delivered.content
        finally:
            await verifier.close()

        # A deliberate restart opens a new client; a zero-budget stop cancels work.
        await channel.start(timeout=30)
        started.clear()
        session = channel._topics.client.session
        session.trace_configs.append(trace)
        delivery = asyncio.create_task(channel.send(request, timeout=30))
        await asyncio.wait_for(started.wait(), timeout=10)
        await channel.stop(timeout=0)
        outcome = await delivery
        assert outcome.status in {
            ProviderCallStatus.UNKNOWN,
            ProviderCallStatus.CONFIRMED,
        }, outcome.error_message
        assert session.closed
    finally:
        await channel.stop(timeout=10)
        await wheel.close()
    assert channel.health["state"] == "stopped"


@pytest.mark.asyncio
async def test_ordinary_chat_on_the_same_real_bot(tmp_path: Path) -> None:
    """Use a chat id obtained from a real ordinary-chat callback."""

    chat_id = os.environ.get("BCN_WECOM_TEST_CHAT_ID")
    if not chat_id:
        pytest.skip("An ordinary-chat callback's chat id is required")

    async def referenced() -> set[str]:
        return set()

    bot_id = os.environ["BCN_WECOM_BOT_ID"]
    wheel = TimerWheel()
    await wheel.start()
    channel = WeComChannel(
        ChannelContext(
            agent_id="isolated-wecom-e2e",
            workspace=lambda: tmp_path,
            attachments=AttachmentMaterializer(lambda: tmp_path, referenced),
            options={},
            timer_wheel=wheel,
        ),
        bot_id=bot_id,
        secret=os.environ["BCN_WECOM_SECRET"],
        websocket_url="wss://openws.work.weixin.qq.com",
    )
    try:
        await channel.start(timeout=30)
        result = await channel.send(
            ChannelSendRequest(
                session_id="isolated-wecom-e2e",
                body="同一个测试机器人验证普通会话发送。",
                attachments=(),
                target_kind=ChannelTargetKind.GROUP,
                provider_thread_id=conversation_thread_id(bot_id, chat_id),
            ),
            timeout=30,
        )
        assert result.status is ProviderCallStatus.CONFIRMED, result.error_message
    finally:
        await channel.stop(timeout=10)
        await wheel.close()
