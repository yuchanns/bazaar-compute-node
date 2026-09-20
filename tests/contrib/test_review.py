from __future__ import annotations

from dataclasses import replace

import pytest
from bcn_test_support import TestChannel
from test_orchestration import make_message, make_sqlite_node

from bazaar_compute_node.app.config import ChannelConfiguration, ConfigurationError
from bazaar_compute_node.core.actor import Agent
from bazaar_compute_node.core.channel import ChannelIdentity
from bazaar_compute_node.core.models import Review, RuntimeTurnState
from bazaar_compute_node.core.review import REVIEW_REPLY, ReviewPolicy
from bazaar_compute_node.core.storage import InboxTargetResolutionError


@pytest.mark.asyncio
async def test_a_conversation_opens_as_the_policy_says() -> None:
    """A chat on its bot's list is let in; the rest wait when someone reviews,
    and otherwise only when the list names anyone at all."""

    first, second = TestChannel(), TestChannel()
    second.identity = ChannelIdentity(id="second-bot")
    for channel in (first, second):
        await channel.start(timeout=1)
    listed = ReviewPolicy(((first, frozenset({"42"})), (second, frozenset({"7"}))))
    reviewed = ReviewPolicy(listed.listed, reviewed=True)
    # case: a bot's list lets its own in, whoever reviews
    assert listed.opening("test-bot", "42") is Review.APPROVED
    assert reviewed.opening("test-bot", "42") is Review.APPROVED
    # case: with a reviewer, everyone else waits, list or no list
    assert reviewed.opening("test-bot", "7") is Review.PENDING
    assert ReviewPolicy(reviewed=True).opening("test-bot", "7") is Review.PENDING
    # case: with nobody to ask, an empty list lets everyone in, and a list
    # that names anyone keeps the rest out
    assert ReviewPolicy().opening("test-bot", "7") is Review.APPROVED
    assert listed.opening("test-bot", "7") is Review.PENDING
    # case: two bots each keep their own list, the same chat id read apart
    assert listed.opening("second-bot", "7") is Review.APPROVED
    assert listed.opening("second-bot", "42") is Review.PENDING


@pytest.mark.asyncio
async def test_a_stranger_is_kept_but_not_heard_until_let_in() -> None:
    """A stranger's messages are kept without starting anything and without
    the agent seeing the conversation; once let in the next message runs a
    turn; turned away, the next message asks again."""

    orchestrator, _, _, storage, audit = await make_sqlite_node(
        review=ReviewPolicy(reviewed=True)
    )
    scope = storage.scope("workspace-1", "Test Agent")
    service = orchestrator.command_service
    agent = Agent("workspace-1")
    try:
        # case: the first message is kept, quiet
        assert await orchestrator.handle_inbound(make_message(seq=1)) is None
        session = await scope.get_channel_session("channel-bcn-1")
        assert session is not None and session.review is Review.PENDING
        kept = await scope.get_message("message-bcn-1-1")
        assert kept is not None and kept.notifies_runtime is False

        # case: to the agent the conversation is not there, though the
        # listing counts it as waiting
        listing = await service.check_inbox(agent, pending_only=False)
        assert listing.targets == () and listing.pending_review == 1
        with pytest.raises(InboxTargetResolutionError):
            await service.read_messages(agent, raw_target="dm:channel-bcn-1")
        with pytest.raises(InboxTargetResolutionError):
            await service.send_message(
                actor=agent, raw_target="dm:channel-bcn-1", body="hi", created_at_ms=2
            )
        # whoever reviews sees it, first message and all
        waiting = await service.check_inbox(
            agent, pending_only=False, review=Review.PENDING
        )
        assert [item.thread_id for item in waiting.targets] == ["bcn-1"]
        history = await service.read_messages(
            agent, raw_target="dm:channel-bcn-1", review=None
        )
        assert [item.body for item in history.messages] == ["inbound-1"]

        # case: let in, the next message runs a turn and the conversation is listed
        await service.review_contact("bcn-1", Review.APPROVED)
        turn = await orchestrator.handle_inbound(make_message(seq=2))
        assert turn is not None and turn.state is RuntimeTurnState.COMPLETED
        listing = await service.check_inbox(agent, pending_only=False)
        assert [item.thread_id for item in listing.targets] == ["bcn-1"]
        assert listing.pending_review == 0

        # case: turned away, the next message is kept and asks again
        await service.review_contact("bcn-1", Review.DENIED)
        assert await orchestrator.handle_inbound(make_message(seq=3)) is None
        session = await scope.get_channel_session("channel-bcn-1")
        assert session is not None and session.review is Review.PENDING
        reviewed = [
            event.metadata["review"]
            for event in audit.events
            if event.event_name == "channel.session.reviewed"
        ]
        assert reviewed == ["approved", "denied"]
    finally:
        await orchestrator.stop(timeout=1)
        await storage.stop(timeout=1)


@pytest.mark.asyncio
async def test_a_conversation_pending_review_is_answered_as_the_agent_is_set() -> None:
    """With a reply set, every message into a conversation not let in gets
    that one line and nothing else; with none set, silence."""

    orchestrator, channel, _, storage, audit = await make_sqlite_node(
        review=ReviewPolicy(reviewed=True)
    )
    service = orchestrator.command_service
    try:
        # case: nothing set, nothing said
        assert await orchestrator.handle_inbound(make_message(seq=1)) is None
        assert channel.send_requests == []

        # case: set, said once per message, whether the first or the third -
        # but not to one that would not have reached the agent anyway, as a
        # message quoted from before is kept
        await service.set_setting(REVIEW_REPLY, "Not yet: ask the operator.")
        assert await service.setting(REVIEW_REPLY) == "Not yet: ask the operator."
        assert await orchestrator.handle_inbound(make_message(seq=2)) is None
        quoted = replace(make_message(seq=3), notifies_runtime=False)
        assert await orchestrator.handle_inbound(quoted) is None
        assert await orchestrator.handle_inbound(make_message(seq=4)) is None
        assert [request.body for request in channel.send_requests] == [
            "Not yet: ask the operator.",
            "Not yet: ask the operator.",
        ]
        assert channel.send_requests[0].provider_thread_id == "thread-bcn-1"
        replied = [
            event
            for event in audit.events
            if event.event_name == "channel.review.replied"
        ]
        assert len(replied) == 2 and replied[0].metadata["review"] == "pending"
        assert "setting.changed" in [event.event_name for event in audit.events]

        # case: let in, the reply stops
        await service.review_contact("bcn-1", Review.APPROVED)
        turn = await orchestrator.handle_inbound(make_message(seq=5))
        assert turn is not None and len(channel.send_requests) == 2
    finally:
        await orchestrator.stop(timeout=1)
        await storage.stop(timeout=1)


def test_the_chats_a_channel_lets_in_come_from_its_options() -> None:
    """`allowed_chats` is read as text whatever the ids were written as, and
    anything but a list of ids is refused at the door."""

    listed = ChannelConfiguration(
        kind="telegram", options={"allowed_chats": [42, "-100"]}
    )
    assert listed.allowed_chats == frozenset({"42", "-100"})
    assert ChannelConfiguration(kind="telegram").allowed_chats == frozenset()
    with pytest.raises(ConfigurationError):
        ChannelConfiguration(kind="telegram", options={"allowed_chats": "42"})
