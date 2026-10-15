from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from uuid import uuid7

import pytest
from test_orchestration import make_sqlite_node

from bazaar_compute_node.app.attachments import AttachmentMaterializer
from bazaar_compute_node.contrib.lark.channel import LarkChannel
from bazaar_compute_node.contrib.lark.identity import LarkBotIdentity
from bazaar_compute_node.contrib.telegram.channel import TelegramChannel
from bazaar_compute_node.contrib.wecom.channel import WeComChannel
from bazaar_compute_node.contrib.wecom.identity import TopicAddress, reply_message_id
from bazaar_compute_node.contrib.wecom.topic import topic_message
from bazaar_compute_node.contrib.wecom.topic_api import Member
from bazaar_compute_node.core.actor import Agent
from bazaar_compute_node.core.channel import ChannelContext, ChannelDeliveryReceipt
from bazaar_compute_node.core.command import MessageSendSuccess
from bazaar_compute_node.core.models import Message, MessageDirection
from bazaar_compute_node.core.outcomes import ProviderCallResult, ProviderCallStatus
from bazaar_compute_node.core.timerwheel import TimerWheel


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "receipt"),
    (
        ("telegram", True),
        ("telegram-topic", True),
        ("telegram", False),
        ("lark", True),
        ("wecom", False),
        ("wecom-topic", True),
    ),
)
async def test_native_replies_keep_the_quoted_work_readable(
    tmp_path: Path, kind: str, receipt: bool
) -> None:
    orchestrator, channel, _, database, _ = await make_sqlite_node()
    scope = database.scope("workspace-1", "Test Agent")

    async def referenced_paths() -> set[str]:
        return set(await scope.list_ready_attachment_paths())

    context = ChannelContext(
        agent_id="workspace-1",
        attachments=AttachmentMaterializer(lambda: tmp_path, referenced_paths),
        options={},
        workspace=lambda: tmp_path,
    )
    body = "下一步先打通本地调用，再拆出 channel 和 harness。"
    reply = "继续做这段里列的任务。"
    messages: list[Message] = []
    try:
        if kind.startswith("telegram"):
            telegram = TelegramChannel(context, token="unused")
            telegram._bot_id = 100
            telegram._bot_username = "test_bot"
            chat = {"id": 200, "type": "private"}
            topic: dict[str, object] = {}
            if kind == "telegram-topic":
                chat = {"id": -200, "type": "supergroup"}
                topic = {"message_thread_id": 10, "is_topic_message": True}
            await telegram._handle_message(
                {
                    "message_id": 3,
                    "date": telegram._started_at_s,
                    "chat": chat,
                    "from": {"id": 200, "is_bot": False},
                    "text": reply,
                    **topic,
                    "reply_to_message": {
                        "message_id": 2,
                        "date": telegram._started_at_s,
                        "chat": chat,
                        "from": {"id": 100, "is_bot": True},
                        "text": body,
                        **topic,
                    },
                },
                update_id=3,
            )
            while not telegram._inbound.empty():
                message = telegram._inbound.get_nowait()
                assert isinstance(message, Message)
                messages.append(message)
            provider_id = "2"
        elif kind == "lark":
            lark = LarkChannel(
                context,
                app_id="unused",
                app_secret="unused",
                region="feishu",
                base_url="https://open.feishu.cn",
                timer_wheel=TimerWheel(),
            )
            lark._identity = LarkBotIdentity(open_id="ou_bot")
            # No provider client is connected: the existing sent message must
            # still be usable when the parent cannot be fetched again.
            await lark._handle_event(
                "im.message.receive_v1",
                {
                    "schema": "2.0",
                    "header": {"event_type": "im.message.receive_v1"},
                    "event": {
                        "sender": {
                            "sender_id": {"open_id": "ou_hanchin"},
                            "sender_type": "user",
                        },
                        "message": {
                            "message_id": "om_current",
                            "parent_id": "om_parent",
                            "chat_id": "oc_chat",
                            "chat_type": "p2p",
                            "message_type": "text",
                            "content": json.dumps({"text": reply}),
                        },
                    },
                },
                None,
            )
            async with asyncio.timeout(1):
                messages.append(await anext(lark.receive()))
            provider_id = "om_parent"
        elif kind == "wecom":
            wecom = WeComChannel(
                context,
                bot_id="test-bot",
                secret="unused",
                websocket_url="wss://example.invalid",
            )
            await wecom._receive_message(
                {
                    "cmd": "aibot_msg_callback",
                    "body": {
                        "msgid": "current",
                        "from": {"userid": "Hanchin"},
                        "chattype": "single",
                        "msgtype": "text",
                        "text": {"content": reply},
                        "quote": {"msgtype": "text", "text": {"content": body}},
                    },
                }
            )
            while not wecom._inbound.empty():
                message = wecom._inbound.get_nowait()
                assert isinstance(message, Message)
                messages.append(message)
            provider_id = messages[0].provider_message_id
        else:
            address = TopicAddress("test-bot", "test-channel", "discussion")
            provider_id = reply_message_id("discussion", "parent")
            messages = [
                topic_message(
                    address,
                    provider_id=provider_id,
                    body=body,
                    creator=Member(type=1, aibotid="test-bot"),
                    create_time=2,
                    name="插件开发",
                    notifies_runtime=False,
                ),
                topic_message(
                    address,
                    provider_id=reply_message_id("discussion", "current"),
                    parent_id=provider_id,
                    body=reply,
                    creator=Member(type=0, userid="Hanchin"),
                    create_time=3,
                    name="插件开发",
                    notifies_runtime=False,
                ),
            ]

        current = messages[-1]
        metadata = dict(current.metadata)
        metadata.pop("provider_reply_to_message_id", None)
        post = replace(
            current,
            message_id=str(uuid7()),
            provider_message_id="opening",
            body="我们继续讨论插件开发。",
            reply_to_message_id=None,
            metadata=metadata,
            notifies_runtime=False,
        )
        await orchestrator.handle_inbound(post)
        await orchestrator.command_service.read_messages(
            Agent("workspace-1"), raw_target=post.target
        )
        channel.queue_send_result(
            ProviderCallResult(
                status=ProviderCallStatus.CONFIRMED
                if receipt
                else ProviderCallStatus.QUEUED,
                value=ChannelDeliveryReceipt(provider_message_id=provider_id)
                if receipt
                else ChannelDeliveryReceipt(provider_receipt_ref="pending"),
            )
        )
        sent = await orchestrator.command_service.send_message(
            actor=Agent("workspace-1"),
            raw_target=post.target,
            body=body,
            created_at_ms=2,
        )
        assert isinstance(sent, MessageSendSuccess)
        for message in messages:
            await orchestrator.handle_inbound(replace(message, notifies_runtime=False))
        page = await orchestrator.command_service.read_messages(
            Agent("workspace-1"),
            raw_target=post.target,
            around_message_id=current.message_id,
            limit=1,
        )
        assert any("列的任务" in message.body for message in page.messages)
        assert any(
            "打通本地调用" in message.body for message in page.referenced_messages
        )
        if receipt:
            assert any(
                message.message_id == sent.message.message_id
                for message in page.referenced_messages
            )
            history = await orchestrator.command_service.read_messages(
                Agent("workspace-1"), raw_target=post.target
            )
            assert (
                sum(
                    message.provider_message_id == provider_id
                    for message in history.messages
                )
                == 1
            )
        else:
            assert any(
                message.direction is MessageDirection.INBOUND
                for message in page.referenced_messages
            )
        answer = await orchestrator.command_service.send_message(
            actor=Agent("workspace-1"),
            raw_target=post.target,
            reply_to_message_id=current.message_id,
            body="好，按这里的任务继续。",
            created_at_ms=4,
        )
        assert isinstance(answer, MessageSendSuccess)
    finally:
        await orchestrator.stop(timeout=1)
        await database.stop(timeout=1)
