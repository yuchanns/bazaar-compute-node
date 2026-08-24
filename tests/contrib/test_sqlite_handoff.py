from __future__ import annotations

from typing import Any

import pytest
from bcn_test_support import RecordingAudit

from bazaar_compute_node.contrib.sqlite import SqliteDatabase
from bazaar_compute_node.core.handoff import HandoffCheckRequest, HandoffSendRequest
from bazaar_compute_node.core.lifecycle import TimeoutBudget
from bazaar_compute_node.core.models import (
    BcnSession,
    ChannelSession,
    ConsumerCursor,
    Handoff,
    Message,
    MessageDirection,
    SenderIdentity,
)
from bazaar_compute_node.core.orchestration.handoff_command import (
    HandoffCommandService,
)
from bazaar_compute_node.core.orchestration.services import SessionAuditRecorder
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
) -> Message:
    return await transaction.append_inbound_message(
        Message(
            direction=MessageDirection.INBOUND,
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
            target=f"dm:{session.id}",
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
        repository = agent_a
        source_channel, source_session = await _create_session(
            repository,
            agent_id="agent-a",
            session_id="session-source",
        )
        target_channel, target_session = await _create_session(
            repository,
            agent_id="agent-a",
            session_id="session-target",
        )
        await _append_message(
            repository,
            channel=source_channel,
            session=source_session,
            message_id="message-source",
            received_at_ms=10,
        )
        await _append_message(
            repository,
            channel=target_channel,
            session=target_session,
            message_id="message-target-old",
            received_at_ms=20,
        )
        latest_target = await _append_message(
            repository,
            channel=target_channel,
            session=target_session,
            message_id="message-target-latest",
            received_at_ms=30,
        )
        first = await repository.save_handoff(
            _handoff(1, body="First line.\n\nSecond line.")
        )
        second = await repository.save_handoff(_handoff(2, source_message_id=None))
        third = await repository.save_handoff(_handoff(3))

        pending = await repository.list_pending_handoffs(
            target_session.id,
            limit=2,
        )
        marked = await repository.mark_handoffs_read(
            target_session.id,
            (second.handoff_id,),
            read_at_ms=2_000,
        )
        remaining = await repository.list_pending_handoffs(
            target_session.id,
            limit=100,
        )
        remaining_count = await repository.count_pending_handoffs(target_session.id)
        target_anchor = await repository.get_latest_inbound_message(target_session.id)

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
        repository = agent_b
        assert (
            await repository.list_pending_handoffs(
                target_session.id,
                limit=100,
            )
            == ()
        )
        assert await repository.count_pending_handoffs(target_session.id) == 0
        assert await repository.get_latest_inbound_message(target_session.id) is None
        assert (
            await repository.mark_handoffs_read(
                target_session.id,
                (first.handoff_id,),
                read_at_ms=3_000,
            )
            == ()
        )
    finally:
        await database.stop(timeout=2)


@pytest.mark.asyncio
async def test_sqlite_handoff_save_is_idempotent_by_command_payload() -> None:
    database = SqliteDatabase()
    await database.start(timeout=2)
    try:
        scope = database.scope("agent-a", "Agent A")
        repository = scope
        await _create_session(
            repository,
            agent_id="agent-a",
            session_id="session-source",
        )
        await _create_session(
            repository,
            agent_id="agent-a",
            session_id="session-target",
        )
        stored = await repository.save_handoff(_handoff(1))
        replayed = await repository.save_handoff(
            _handoff(1, handoff_id="handoff-retry")
        )

        with pytest.raises(HandoffConflictError, match="different payload"):
            await repository.save_handoff(
                _handoff(
                    1,
                    handoff_id="handoff-conflict",
                    body="Different task.",
                )
            )

        assert replayed == stored

        repository = database
        persisted = await repository.list_pending_handoffs(
            "session-target",
            limit=100,
        )
        unscoped = await repository.save_handoff(_handoff(2))

        assert persisted == (stored,)
        assert unscoped == _handoff(2)
    finally:
        await database.stop(timeout=2)


@pytest.mark.asyncio
async def test_handoff_command_send_then_check_preserves_message_cursors() -> None:
    database = SqliteDatabase()
    audit_sink = RecordingAudit()
    await database.start(timeout=2)
    try:
        scope = database.scope("agent-a", "Agent A")
        repository = scope
        source_channel, source_session = await _create_session(
            repository,
            agent_id="agent-a",
            session_id="session-source",
        )
        target_channel, target_session = await _create_session(
            repository,
            agent_id="agent-a",
            session_id="session-target",
        )
        source_message = await _append_message(
            repository,
            channel=source_channel,
            session=source_session,
            message_id="019d2f00-0000-7000-8000-000000000001",
            received_at_ms=10,
        )
        target_message = await _append_message(
            repository,
            channel=target_channel,
            session=target_session,
            message_id="019d2f00-0000-7000-8000-000000000002",
            received_at_ms=20,
        )
        cursors = (
            ConsumerCursor(
                session_id=source_session.id,
                delivered_through_seq=source_message.seq,
                inbox_snapshot_seq=source_message.seq,
                inbox_snapshot_source="read",
                inbox_snapshot_at_ms=100,
                updated_at_ms=100,
            ),
            ConsumerCursor(
                session_id=target_session.id,
                delivered_through_seq=target_message.seq,
                inbox_snapshot_seq=target_message.seq,
                inbox_snapshot_source="check",
                inbox_snapshot_at_ms=100,
                updated_at_ms=100,
            ),
        )
        for cursor in cursors:
            await repository.save_consumer_cursor(cursor)

        wakes: list[str] = []

        async def publish_wake(session_id: str) -> None:
            repository = scope
            assert await repository.count_pending_handoffs(session_id) == 1
            wakes.append(session_id)

        service = HandoffCommandService(
            storage=scope,
            audit=SessionAuditRecorder(
                sink=audit_sink,
                timeout_budget=TimeoutBudget(2, 2, 2, 2),
                clock=lambda: 2_000,
            ),
            publish_wake=publish_wake,
            node_id=lambda: "node-a",
            clock=lambda: 2_000,
            handoff_id=lambda: "handoff-command",
        )
        sent = await service.send(
            source_session.id,
            HandoffSendRequest(
                target="dm:session-target",
                body="Continue task.",
                command_id="command-send",
                created_at_ms=1_000,
                source_message_id=source_message.message_id,
            ),
        )
        checked = await service.check(target_session.id, HandoffCheckRequest())

        assert checked.items[0].handoff == sent.handoff.mark_read(at_ms=2_000)
        assert checked.items[0].source_target == "dm:session-source"
        assert checked.has_more is False
        assert wakes == [target_session.id]
        repository = scope
        for cursor in cursors:
            assert await repository.get_consumer_cursor(cursor.session_id) == cursor
        assert "Continue task." not in repr(audit_sink.events)
    finally:
        await database.stop(timeout=2)
