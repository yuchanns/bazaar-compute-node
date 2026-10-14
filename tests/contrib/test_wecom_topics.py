from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from test_orchestration import make_sqlite_node

from bazaar_compute_node.contrib.wecom.identity import (
    TopicAddress,
    post_message_id,
    reply_message_id,
)
from bazaar_compute_node.contrib.wecom.topic import topic_message
from bazaar_compute_node.contrib.wecom.topic_api import Member
from bazaar_compute_node.core.actor import Agent
from bazaar_compute_node.core.channel import ChannelDeliveryReceipt
from bazaar_compute_node.core.command import MessageSendSuccess
from bazaar_compute_node.core.models import MessageDirection, Review
from bazaar_compute_node.core.outcomes import ProviderCallResult, ProviderCallStatus
from bazaar_compute_node.core.review import ReviewPolicy
from bazaar_compute_node.core.utils.clock import now_ms


@pytest.mark.asyncio
async def test_native_posts_are_readable_with_their_context_while_control_reviews_chats() -> (
    None
):
    """A topic opens through the ordinary inbox, with its historical post and parent."""

    orchestrator, _, _, database, _ = await make_sqlite_node(
        review=ReviewPolicy(reviewed=True)
    )
    scope = database.scope("workspace-1", "Test Agent")
    creator = Member(type=0, userid="Hanchin")
    address = TopicAddress("test-bot", "test-channel", "post-with-context")
    post = topic_message(
        address,
        provider_id=post_message_id(address.post_id or ""),
        body="这个帖子讨论企业微信接入。",
        creator=creator,
        create_time=1,
        name="测试 · 企业微信接入",
        notifies_runtime=False,
    )
    parent = topic_message(
        address,
        provider_id=reply_message_id(address.post_id or "", "previous-reply"),
        body="同一个机器人还需要保留普通会话。",
        creator=creator,
        create_time=2,
        name="测试 · 企业微信接入",
        notifies_runtime=False,
    )
    reply = topic_message(
        address,
        provider_id=reply_message_id(address.post_id or "", "current-reply"),
        parent_id=parent.provider_message_id,
        body="@test-bot 请看看这个帖子的讨论。",
        creator=creator,
        create_time=3,
        name="测试 · 企业微信接入",
        mentions_agent=True,
    )
    try:
        for message in (post, parent, reply):
            await orchestrator.handle_inbound(message)
        session = await scope.get_channel_session(reply.channel_session_id)
        assert session is not None and session.review is Review.APPROVED
        history = await orchestrator.command_service.read_messages(
            Agent("workspace-1"),
            raw_target=reply.target,
        )
        assert {message.body for message in history.messages}.issuperset(
            {post.body, parent.body, reply.body}
        )
        kept = await scope.get_message(reply.message_id)
        assert kept is not None and kept.reply_to_message_id == parent.message_id
        listing = await orchestrator.command_service.check_inbox(
            Agent("workspace-1"), pending_only=False
        )
        assert any(item.thread_id == reply.thread_id for item in listing.targets)
    finally:
        await orchestrator.stop(timeout=1)
        await database.stop(timeout=1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    (
        ProviderCallStatus.CONFIRMED,
        ProviderCallStatus.QUEUED,
        ProviderCallStatus.PARTIAL,
    ),
)
@pytest.mark.parametrize("legacy", (False, True))
async def test_discussion_continues_with_references_to_a_sent_reply_after_echo_context(
    status: ProviderCallStatus, legacy: bool
) -> None:
    orchestrator, channel, _, database, _ = await make_sqlite_node()
    scope = database.scope("workspace-1", "Test Agent")
    address = TopicAddress("test-bot", "test-channel", "discussion")
    post = topic_message(
        address,
        provider_id=post_message_id("discussion"),
        body="我们轮流报数，从 1 报到 10。",
        creator=Member(type=0, userid="Hanchin"),
        create_time=1,
        name="测试 · 轮流报数",
        mentions_agent=True,
        notifies_runtime=False,
    )
    try:
        await orchestrator.handle_inbound(post)
        await orchestrator.command_service.read_messages(
            Agent("workspace-1"), raw_target=post.target
        )
        provider_id = reply_message_id("discussion", "sent-reply")
        channel.queue_send_result(
            ProviderCallResult(
                status=status,
                value=ChannelDeliveryReceipt(provider_message_id=provider_id),
                error_kind="topic_send_failed"
                if status is ProviderCallStatus.PARTIAL
                else None,
            )
        )
        await orchestrator.command_service.send_message(
            actor=Agent("workspace-1"),
            raw_target=post.target,
            body="2",
            created_at_ms=2,
        )
        if legacy:
            # Previously stored echoes remain in existing conversations.
            await scope.record_inbound(
                topic_message(
                    address,
                    provider_id=provider_id,
                    body="2",
                    creator=Member(type=1, aibotid="test-bot"),
                    create_time=2,
                    name="测试 · 轮流报数",
                    notifies_runtime=False,
                ),
                now_ms=now_ms(),
                opening=Review.APPROVED,
            )
        await orchestrator.handle_inbound(
            replace(
                topic_message(
                    address,
                    provider_id=provider_id,
                    body="2",
                    creator=Member(type=1, aibotid="test-bot"),
                    create_time=2,
                    name="测试 · 轮流报数",
                    notifies_runtime=False,
                ),
                sender=None,
            )
        )
        reply = topic_message(
            address,
            provider_id=reply_message_id("discussion", "other-reply"),
            parent_id=provider_id,
            body="3",
            creator=Member(type=1, aibotid="other-bot"),
            create_time=3,
            name="测试 · 轮流报数",
            notifies_runtime=False,
        )
        await orchestrator.handle_inbound(reply)
        history = await orchestrator.command_service.read_messages(
            Agent("workspace-1"), raw_target=post.target
        )
        assert {"2", "3"}.issubset({message.body for message in history.messages})
        if not legacy:
            assert (
                len([message for message in history.messages if message.body == "2"])
                == 1
            )
        received = next(message for message in history.messages if message.body == "3")
        page = await orchestrator.command_service.read_messages(
            Agent("workspace-1"),
            raw_target=post.target,
            around_message_id=received.message_id,
            limit=1,
        )
        assert any(message.body == "2" for message in page.referenced_messages)
        assert any(
            message.sender is not None and message.sender.id == "other-bot"
            for message in page.messages
        )
        answer = await orchestrator.command_service.send_message(
            actor=Agent("workspace-1"),
            raw_target=post.target,
            reply_to_message_id=received.message_id,
            body="4",
            created_at_ms=4,
        )
        assert isinstance(answer, MessageSendSuccess)
    finally:
        await orchestrator.stop(timeout=1)
        await database.stop(timeout=1)


@pytest.mark.asyncio
@pytest.mark.parametrize("pending", (False, True))
async def test_quoted_parts_remain_readable_before_the_outbound_receipt_is_available(
    pending: bool,
) -> None:
    orchestrator, channel, _, database, _ = await make_sqlite_node()
    address = TopicAddress("test-bot", "test-channel", "long-discussion")
    post = topic_message(
        address,
        provider_id=post_message_id("long-discussion"),
        body="先讨论频道接入，再讨论普通会话。",
        creator=Member(type=0, userid="Hanchin"),
        create_time=1,
        name="测试 · 接入讨论",
        mentions_agent=True,
        notifies_runtime=False,
    )
    send: asyncio.Task[object] | None = None
    try:
        await orchestrator.handle_inbound(post)
        await orchestrator.command_service.read_messages(
            Agent("workspace-1"), raw_target=post.target
        )
        channel.queue_send_result(
            ProviderCallResult(
                status=ProviderCallStatus.CONFIRMED,
                value=ChannelDeliveryReceipt(
                    provider_message_id=reply_message_id("long-discussion", "last-part")
                ),
            )
        )
        if pending:
            channel.send_gate = asyncio.Event()
        send = asyncio.create_task(
            orchestrator.command_service.send_message(
                actor=Agent("workspace-1"),
                raw_target=post.target,
                body="先接入频道。\n" + "频道讨论内容。" * 2_000 + "\n再保留普通会话。",
                created_at_ms=2,
            )
        )
        if pending:
            async with asyncio.timeout(2):
                while not channel.send_attempts:
                    await asyncio.sleep(0)
        else:
            await send
        parent = topic_message(
            address,
            provider_id=reply_message_id(
                "long-discussion", "last-part" if pending else "first-part"
            ),
            body="先接入频道。",
            creator=Member(type=1, aibotid="test-bot"),
            create_time=2,
            name="测试 · 接入讨论",
            notifies_runtime=False,
        )
        reply = topic_message(
            address,
            provider_id=reply_message_id("long-discussion", "human-reply"),
            parent_id=parent.provider_message_id,
            body="频道接入方案可以继续做。",
            creator=Member(type=0, userid="Hanchin"),
            create_time=3,
            name="测试 · 接入讨论",
            notifies_runtime=False,
        )
        await orchestrator.handle_inbound(replace(parent, sender=None))
        await orchestrator.handle_inbound(reply)
        history = await orchestrator.command_service.read_messages(
            Agent("workspace-1"), raw_target=post.target
        )
        received = next(
            message for message in history.messages if "方案可以继续做" in message.body
        )
        page = await orchestrator.command_service.read_messages(
            Agent("workspace-1"),
            raw_target=post.target,
            around_message_id=received.message_id,
            limit=1,
        )
        assert any("先接入频道" in message.body for message in page.referenced_messages)
        assert any("方案可以继续做" in message.body for message in page.messages)
        if channel.send_gate is not None:
            channel.send_gate.set()
        assert isinstance(await send, MessageSendSuccess)
        history = await orchestrator.command_service.read_messages(
            Agent("workspace-1"), raw_target=post.target
        )
        assert any(
            message.direction is MessageDirection.OUTBOUND
            and "保留普通会话" in message.body
            for message in history.messages
        )
    finally:
        if channel.send_gate is not None:
            channel.send_gate.set()
        if send is not None:
            await send
        await orchestrator.stop(timeout=1)
        await database.stop(timeout=1)


@pytest.mark.asyncio
async def test_first_reply_opens_a_readable_post_before_its_parent_has_arrived() -> (
    None
):
    orchestrator, _, _, database, _ = await make_sqlite_node(
        review=ReviewPolicy(reviewed=True)
    )
    address = TopicAddress("test-bot", "test-channel", "existing-post")
    reply = topic_message(
        address,
        provider_id=reply_message_id("existing-post", "current-reply"),
        parent_id=reply_message_id("existing-post", "earlier-reply"),
        body="这条回复补充一下企业微信接入的讨论。",
        creator=Member(type=0, userid="Hanchin"),
        create_time=1,
        name="测试 · 企业微信接入",
        mentions_agent=True,
        notifies_runtime=False,
    )
    try:
        await orchestrator.handle_inbound(reply)
        history = await orchestrator.command_service.read_messages(
            Agent("workspace-1"), raw_target=reply.target
        )
        assert any("补充一下" in message.body for message in history.messages)
        session = await database.scope("workspace-1", "Test Agent").get_channel_session(
            reply.channel_session_id
        )
        assert session is not None and session.review is Review.APPROVED
    finally:
        await orchestrator.stop(timeout=1)
        await database.stop(timeout=1)


@pytest.mark.asyncio
async def test_posts_and_bots_keep_independent_histories_and_replayed_events_stay_idempotent() -> (
    None
):
    """Reading another post or bot never brings content from the first conversation."""

    orchestrator, _, _, database, _ = await make_sqlite_node(
        review=ReviewPolicy(reviewed=True)
    )
    scope = database.scope("workspace-1", "Test Agent")
    creator = Member(type=0, userid="Hanchin")
    messages = [
        topic_message(
            TopicAddress(bot, "same-channel", post),
            provider_id=post_message_id(post),
            body=body,
            creator=creator,
            create_time=1,
            name=body,
            notifies_runtime=False,
            mentions_agent=True,
        )
        for bot, post, body in (
            ("bot-a", "post-a", "第一个机器人的第一个帖子"),
            ("bot-a", "post-b", "第一个机器人的第二个帖子"),
            ("bot-b", "post-a", "第二个机器人的帖子"),
        )
    ]
    try:
        for message in messages:
            await orchestrator.handle_inbound(message)
            await orchestrator.handle_inbound(replace(message, received_at_ms=10))
        histories = [
            await scope.list_messages(message.thread_id) for message in messages
        ]
        assert len({message.thread_id for message in messages}) == len(histories)
        for message, history in zip(messages, histories, strict=True):
            assert [item.body for item in history] == [message.body]
    finally:
        await orchestrator.stop(timeout=1)
        await database.stop(timeout=1)
