from __future__ import annotations

from typing import Any

import pytest

from bazaar_compute_node.contrib.sqlite import SqliteDatabase
from bazaar_compute_node.core.models import (
    BcnSession,
    ChannelSession,
    Handoff,
    InboundMessage,
    SenderIdentity,
)
from bazaar_compute_node.core.storage import HandoffConflictError


async def _create_session(
    transaction: Any,
    *,
    agent_id: str,
    session_id: str,
) -> tuple[ChannelSession, BcnSession]:
    channel = ChannelSession(
        id=f"channel-{session_id}",
        channel="test",
        provider_thread_id=f"thread-{session_id}",
        created_at_ms=1,
        updated_at_ms=1,
    )
    session = BcnSession(
        id=session_id,
        channel_session_id=channel.id,
        workspace_id=agent_id,
        created_at_ms=1,
        updated_at_ms=1,
    )
    await transaction.save_channel_session(channel)
    await transaction.save_bcn_session(session)
    return channel, session


async def _append_message(
    transaction: Any,
    *,
    channel: ChannelSession,
    session: BcnSession,
    message_id: str,
    received_at_ms: int,
) -> InboundMessage:
    return await transaction.append_inbound_message(
        InboundMessage(
            seq=0,
            message_id=message_id,
            session_id=session.id,
            channel_session_id=channel.id,
            channel=channel.channel,
            provider_thread_id=channel.provider_thread_id,
            provider_message_id=f"provider-{message_id}",
            received_at_ms=received_at_ms,
            sender=SenderIdentity(name="sender"),
            message_type="text",
            canonical_target=f"dm:{session.id}",
            body=f"body-{message_id}",
        )
    )


def _handoff(
    number: int,
    *,
    command_id: str | None = None,
    handoff_id: str | None = None,
    source_message_id: str | None = "message-source",
    body: str | None = None,
) -> Handoff:
    return Handoff(
        handoff_id=handoff_id or f"handoff-{number}",
        command_id=command_id or f"command-{number}",
        source_session_id="session-source",
        target_session_id="session-target",
        source_message_id=source_message_id,
        body=body or f"Continue task {number}.",
        created_at_ms=1_000 + number,
    )


@pytest.mark.asyncio
async def test_sqlite_handoff_repository_is_scoped_and_marks_exact_ids() -> None:
    database = SqliteDatabase()
    await database.start(timeout=2)
    try:
        agent_a = database.scope("agent-a", "Agent A")
        agent_b = database.scope("agent-b", "Agent B")
        async with agent_a.transaction() as transaction:
            source_channel, source_session = await _create_session(
                transaction,
                agent_id="agent-a",
                session_id="session-source",
            )
            target_channel, target_session = await _create_session(
                transaction,
                agent_id="agent-a",
                session_id="session-target",
            )
            await _append_message(
                transaction,
                channel=source_channel,
                session=source_session,
                message_id="message-source",
                received_at_ms=10,
            )
            await _append_message(
                transaction,
                channel=target_channel,
                session=target_session,
                message_id="message-target-old",
                received_at_ms=20,
            )
            latest_target = await _append_message(
                transaction,
                channel=target_channel,
                session=target_session,
                message_id="message-target-latest",
                received_at_ms=30,
            )
            first = await transaction.save_handoff(
                _handoff(1, body="First line.\n\nSecond line.")
            )
            second = await transaction.save_handoff(
                _handoff(2, source_message_id=None)
            )
            third = await transaction.save_handoff(_handoff(3))

            pending = await transaction.list_pending_handoffs(
                target_session.id,
                limit=2,
            )
            marked = await transaction.mark_handoffs_read(
                target_session.id,
                (second.handoff_id,),
                read_at_ms=2_000,
            )
            remaining = await transaction.list_pending_handoffs(
                target_session.id,
                limit=100,
            )
            remaining_count = await transaction.count_pending_handoffs(
                target_session.id
            )
            target_anchor = await transaction.get_latest_inbound_message(
                target_session.id
            )

            pending_plan = await transaction.fetchall(
                "EXPLAIN QUERY PLAN SELECT handoff_id FROM handoffs "
                "WHERE agent_id = bcn_agent_id() AND target_session_id = ? "
                "AND read_at_ms IS NULL ORDER BY seq LIMIT ?",
                (target_session.id, 100),
            )
            command_plan = await transaction.fetchall(
                "EXPLAIN QUERY PLAN SELECT handoff_id FROM handoffs "
                "WHERE agent_id = bcn_agent_id() AND command_id = ?",
                (first.command_id,),
            )

        assert [handoff.handoff_id for handoff in pending] == [
            first.handoff_id,
            second.handoff_id,
        ]
        assert first.body == "First line.\n\nSecond line."
        assert second.source_message_id is None
        assert marked == (second.mark_read(at_ms=2_000),)
        assert [handoff.handoff_id for handoff in remaining] == [
            first.handoff_id,
            third.handoff_id,
        ]
        assert remaining_count == 2
        assert target_anchor == latest_target
        assert any(
            "idx_handoffs_agent_target_read_seq" in row["detail"]
            for row in pending_plan
        )
        assert any("sqlite_autoindex_handoffs" in row["detail"] for row in command_plan)

        async with agent_b.transaction() as transaction:
            assert await transaction.list_pending_handoffs(
                target_session.id,
                limit=100,
            ) == ()
            assert await transaction.count_pending_handoffs(target_session.id) == 0
            assert await transaction.get_latest_inbound_message(target_session.id) is None
            assert await transaction.mark_handoffs_read(
                target_session.id,
                (first.handoff_id,),
                read_at_ms=3_000,
            ) == ()
    finally:
        await database.stop(timeout=2)


@pytest.mark.asyncio
async def test_sqlite_handoff_save_is_idempotent_by_command_payload() -> None:
    database = SqliteDatabase()
    await database.start(timeout=2)
    try:
        scope = database.scope("agent-a", "Agent A")
        async with scope.transaction() as transaction:
            await _create_session(
                transaction,
                agent_id="agent-a",
                session_id="session-source",
            )
            await _create_session(
                transaction,
                agent_id="agent-a",
                session_id="session-target",
            )
            stored = await transaction.save_handoff(_handoff(1))
            replayed = await transaction.save_handoff(
                _handoff(1, handoff_id="handoff-retry")
            )

            with pytest.raises(HandoffConflictError, match="different payload"):
                await transaction.save_handoff(
                    _handoff(
                        1,
                        handoff_id="handoff-conflict",
                        body="Different task.",
                    )
                )

        assert replayed == stored

        async with database.transaction() as transaction:
            persisted = await transaction.list_pending_handoffs(
                "session-target",
                limit=100,
            )
            unscoped = await transaction.save_handoff(_handoff(2))

        assert persisted == (stored,)
        assert unscoped == _handoff(2)
    finally:
        await database.stop(timeout=2)
