from __future__ import annotations

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
from bazaar_compute_node.core.models import Review
from bazaar_compute_node.core.review import ReviewPolicy


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
