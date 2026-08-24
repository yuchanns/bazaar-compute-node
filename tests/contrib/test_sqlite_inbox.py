from __future__ import annotations

from typing import Any

import pytest

from bazaar_compute_node.contrib.sqlite import SqliteDatabase
from bazaar_compute_node.core.inbox import InboxTargetPage
from bazaar_compute_node.core.models import (
    BcnSession,
    ChannelSession,
    ChannelTargetKind,
    ConsumerCursor,
    Message,
    MessageDirection,
    SenderIdentity,
)
from bazaar_compute_node.core.storage import InboxTargetResolutionError


async def _create_session(
    transaction: Any,
    *,
    agent_id: str,
    session_id: str,
    channel_session_id: str,
    target_kind: ChannelTargetKind = ChannelTargetKind.DM,
    last_activity_at_ms: int | None,
) -> tuple[ChannelSession, BcnSession]:
    channel_session = ChannelSession(
        id=channel_session_id,
        channel="telegram",
        provider_thread_id=f"thread-{channel_session_id}",
        created_at_ms=1,
        updated_at_ms=last_activity_at_ms or 1,
        target_kind=target_kind,
    )
    bcn_session = BcnSession(
        id=session_id,
        channel_session_id=channel_session_id,
        workspace_id=agent_id,
        created_at_ms=1,
        updated_at_ms=last_activity_at_ms or 1,
        last_activity_at_ms=last_activity_at_ms,
    )
    await transaction.save_channel_session(channel_session)
    await transaction.save_bcn_session(bcn_session)
    return channel_session, bcn_session


async def _append_message(
    transaction: Any,
    *,
    channel_session: ChannelSession,
    bcn_session: BcnSession,
    message_id: str,
    target: str,
    received_at_ms: int,
    sender_name: str,
    provider_time_ms: int | None,
    notifies_runtime: bool = True,
) -> Message:
    return await transaction.append_inbound_message(
        Message(
            direction=MessageDirection.INBOUND,
            seq=0,
            message_id=message_id,
            session_id=bcn_session.id,
            channel_session_id=channel_session.id,
            channel=channel_session.channel,
            provider_thread_id=channel_session.provider_thread_id,
            provider_message_id=f"provider-{message_id}",
            received_at_ms=received_at_ms,
            sender=SenderIdentity(name=sender_name),
            message_type="text",
            target=target,
            body=f"body-{message_id}",
            target_kind=channel_session.target_kind,
            notifies_runtime=notifies_runtime,
            provider_time_ms=provider_time_ms,
        )
    )


@pytest.mark.asyncio
async def test_sqlite_inbox_catalog_is_scoped_and_non_draining() -> None:
    database = SqliteDatabase()
    await database.start(timeout=2)
    try:
        agent_a = database.scope("agent-a", "Agent A")
        agent_b = database.scope("agent-b", "Agent B")
        repository = agent_a
        pending_channel, pending_session = await _create_session(
            repository,
            agent_id="agent-a",
            session_id="session-pending",
            channel_session_id="channel-pending",
            last_activity_at_ms=300,
        )
        pending = await _append_message(
            repository,
            channel_session=pending_channel,
            bcn_session=pending_session,
            message_id="message-pending",
            target="dm:shared-target",
            received_at_ms=301,
            sender_name="pending-sender",
            provider_time_ms=300_000,
        )

        read_channel, read_session = await _create_session(
            repository,
            agent_id="agent-a",
            session_id="session-read",
            channel_session_id="channel-read",
            target_kind=ChannelTargetKind.GROUP,
            last_activity_at_ms=200,
        )
        read = await _append_message(
            repository,
            channel_session=read_channel,
            bcn_session=read_session,
            message_id="message-read",
            target="group:read-target",
            received_at_ms=201,
            sender_name="read-sender",
            provider_time_ms=None,
        )
        await repository.save_consumer_cursor(
            ConsumerCursor(
                session_id=read_session.id,
                delivered_through_seq=read.seq,
                inbox_snapshot_seq=read.seq,
                inbox_snapshot_source="check",
                inbox_snapshot_at_ms=202,
                updated_at_ms=202,
            )
        )

        await _create_session(
            repository,
            agent_id="agent-a",
            session_id="session-empty",
            channel_session_id="channel-empty",
            last_activity_at_ms=100,
        )

        repository = agent_b
        foreign_channel, foreign_session = await _create_session(
            repository,
            agent_id="agent-b",
            session_id="session-foreign",
            channel_session_id="channel-foreign",
            last_activity_at_ms=999,
        )
        await _append_message(
            repository,
            channel_session=foreign_channel,
            bcn_session=foreign_session,
            message_id="message-foreign",
            target="dm:shared-target",
            received_at_ms=1_000,
            sender_name="foreign-sender",
            provider_time_ms=1_000_000,
        )

        repository = agent_a
        cursor_before = await repository.get_consumer_cursor(pending.session_id)
        read_cursor_before = await repository.get_consumer_cursor(read.session_id)
        first_page = await repository.list_inbox_targets(limit=2, offset=0)
        second_page = await repository.list_inbox_targets(limit=2, offset=2)
        empty_page = await repository.list_inbox_targets(limit=2, offset=3)
        cursor_after = await repository.get_consumer_cursor(pending.session_id)
        read_cursor_after = await repository.get_consumer_cursor(read.session_id)
        pending_owner = await repository.resolve_inbox_target("dm:shared-target")
        empty_owner = await repository.resolve_inbox_target("dm:channel-empty")

        assert [target.session_id for target in first_page.targets] == [
            "session-pending",
            "session-read",
        ]
        assert first_page.total == 3
        assert first_page.shown == 2
        assert first_page.offset == 0
        assert first_page.has_more is True
        assert first_page.targets[0].pending_count == 1
        assert first_page.targets[0].latest_message_id == pending.message_id
        assert first_page.targets[0].latest_sender == SenderIdentity(
            name="pending-sender"
        )
        assert first_page.targets[0].latest_provider_time_ms == 300_000
        assert first_page.targets[0].latest_received_at_ms == 301
        assert first_page.targets[1].pending_count == 0
        assert first_page.targets[1].latest_message_id == read.message_id
        assert first_page.targets[1].latest_provider_time_ms is None
        assert second_page.total == 3
        assert [target.session_id for target in second_page.targets] == [
            "session-empty"
        ]
        assert second_page.shown == 1
        assert second_page.has_more is False
        assert second_page.targets[0].target == "dm:channel-empty"
        assert empty_page == InboxTargetPage(
            targets=(),
            total=3,
            offset=3,
        )
        assert cursor_after == cursor_before
        assert read_cursor_after == read_cursor_before
        assert pending_owner.id == pending_session.id
        assert empty_owner.id == "session-empty"
    finally:
        await database.stop(timeout=2)


@pytest.mark.asyncio
async def test_sqlite_inbox_target_resolution_fails_closed_on_unknown_or_ambiguous() -> (
    None
):
    database = SqliteDatabase()
    await database.start(timeout=2)
    try:
        scope = database.scope("agent-a", "Agent A")
        repository = scope
        first_channel, first_session = await _create_session(
            repository,
            agent_id="agent-a",
            session_id="session-first",
            channel_session_id="channel-first",
            last_activity_at_ms=2,
        )
        await _append_message(
            repository,
            channel_session=first_channel,
            bcn_session=first_session,
            message_id="message-first",
            target="dm:ambiguous",
            received_at_ms=2,
            sender_name="first-sender",
            provider_time_ms=None,
        )
        second_channel, second_session = await _create_session(
            repository,
            agent_id="agent-a",
            session_id="session-second",
            channel_session_id="channel-second",
            last_activity_at_ms=1,
        )
        await _append_message(
            repository,
            channel_session=second_channel,
            bcn_session=second_session,
            message_id="message-second",
            target="dm:ambiguous",
            received_at_ms=1,
            sender_name="second-sender",
            provider_time_ms=None,
        )

        with pytest.raises(InboxTargetResolutionError):
            await repository.resolve_inbox_target("dm:missing")
        with pytest.raises(InboxTargetResolutionError):
            await repository.resolve_inbox_target("dm:ambiguous")
    finally:
        await database.stop(timeout=2)
