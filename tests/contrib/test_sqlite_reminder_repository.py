from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from uuid import uuid7

import pytest
import pytest_asyncio
from bcn_test_support import MemoryStorage

from bazaar_compute_node.contrib.sqlite import SqliteDatabase
from bazaar_compute_node.core.models import (
    BcnSession,
    ChannelSession,
    InboundMessage,
    Reminder,
    ReminderOccurrence,
    ReminderState,
)
from bazaar_compute_node.core.reminder import next_recurrence_ms

ANCHOR_ID = "a1b2c3d4-0000-7000-8000-000000000001"


@pytest_asyncio.fixture
async def database() -> AsyncIterator[SqliteDatabase]:
    database = SqliteDatabase()
    await database.start(timeout=2)
    await database.initialize(node_id="node-1", workspace_id="workspace-1")
    try:
        yield database
    finally:
        await database.stop(timeout=2)


def make_channel_session() -> ChannelSession:
    return ChannelSession(
        id="channel-1",
        channel="test",
        provider_thread_id="thread-1",
        created_at_ms=100,
        updated_at_ms=100,
    )


def make_bcn_session() -> BcnSession:
    return BcnSession(
        id="bcn-1",
        channel_session_id="channel-1",
        workspace_id="workspace-1",
        created_at_ms=100,
        updated_at_ms=100,
    )


def make_inbound(
    *,
    message_id: str = ANCHOR_ID,
    session_id: str = "bcn-1",
    provider_message_id: str = "provider-1",
) -> InboundMessage:
    return InboundMessage(
        seq=1,
        message_id=message_id,
        session_id=session_id,
        channel_session_id="channel-1",
        channel="test",
        provider_thread_id="thread-1",
        provider_message_id=provider_message_id,
        received_at_ms=200,
        sender="sender",
        message_type="text",
        canonical_target="dm:channel-1",
        body="remember this",
    )


def make_reminder(
    *,
    anchor_message_id: str = ANCHOR_ID,
    repeat_rule: str | None = None,
    next_fire_at_ms: int = 1_000,
) -> Reminder:
    return Reminder(
        reminder_id=str(uuid7()),
        owner_session_id="bcn-1",
        anchor_message_id=anchor_message_id,
        title="Follow up",
        state=ReminderState.SCHEDULED,
        next_fire_at_ms=next_fire_at_ms,
        repeat_rule=repeat_rule,
        timezone="UTC",
        revision=1,
        last_occurrence_no=0,
        created_at_ms=300,
        updated_at_ms=300,
    )


async def seed(storage: SqliteDatabase | MemoryStorage) -> None:
    async with storage.transaction() as transaction:
        await transaction.save_channel_session(make_channel_session())
        await transaction.save_bcn_session(make_bcn_session())
        await transaction.append_inbound_message(make_inbound())


@pytest.mark.asyncio
async def test_sqlite_schema_v12_and_reminder_restart(
    database: SqliteDatabase,
) -> None:
    await seed(database)
    async with database.transaction() as transaction:
        reminder = await transaction.save_new_reminder(make_reminder())

    assert database.node_state.schema_version == 12
    await database.stop(timeout=2)

    restarted = SqliteDatabase()
    await restarted.start(timeout=2)
    await restarted.initialize(node_id="node-1", workspace_id="workspace-1")
    try:
        assert restarted.node_state.schema_version == 12
        async with restarted.transaction() as transaction:
            assert (
                await transaction.get_reminder("bcn-1", reminder.reminder_id)
                == reminder
            )
    finally:
        await restarted.stop(timeout=2)


@pytest.mark.asyncio
async def test_sqlite_anchor_resolve_is_owner_scoped_and_supports_short_id(
    database: SqliteDatabase,
) -> None:
    await seed(database)
    async with database.transaction() as transaction:
        by_prefix = await transaction.resolve_inbound_message("bcn-1", "a1b2c3d4")
        by_full_id = await transaction.resolve_inbound_message(
            "bcn-1", "a1b2c3d4-0000-7000-8000-000000000001"
        )
        assert by_prefix is not None
        assert by_full_id is not None
        assert by_prefix.message_id == ANCHOR_ID
        assert by_full_id.message_id == ANCHOR_ID
        assert await transaction.resolve_inbound_message("missing", "a1b2c3d4") is None

        with pytest.raises(ValueError, match="owner"):
            await transaction.save_new_reminder(
                make_reminder(anchor_message_id="b1b2c3d4-0000-7000-8000-000000000001")
            )


@pytest.mark.asyncio
async def test_sqlite_reminder_transition_revision_and_frontier(
    database: SqliteDatabase,
) -> None:
    await seed(database)
    async with database.transaction() as transaction:
        first = await transaction.save_new_reminder(make_reminder(next_fire_at_ms=900))
        second = await transaction.save_new_reminder(
            replace(make_reminder(next_fire_at_ms=1_200), title="Second")
        )
        assert await transaction.get_next_scheduled_reminder() == first
        assert await transaction.list_due_reminders(1_000, limit=10) == (first,)

        updated = first.update_title("Updated", at_ms=400)
        assert (
            await transaction.save_reminder_transition(first.revision, updated)
            == updated
        )
        with pytest.raises(ValueError, match="revision conflict"):
            await transaction.save_reminder_transition(first.revision, updated)

        listed = await transaction.list_reminders(
            "bcn-1", frozenset({ReminderState.SCHEDULED})
        )
        assert {reminder.reminder_id for reminder in listed} == {
            updated.reminder_id,
            second.reminder_id,
        }


@pytest.mark.asyncio
async def test_sqlite_fire_and_check_are_atomic(
    database: SqliteDatabase,
) -> None:
    await seed(database)
    async with database.transaction() as transaction:
        saved = await transaction.save_new_reminder(make_reminder())
        fired = saved.record_fire(
            scheduled_for_ms=1_000,
            fired_at_ms=1_100,
            next_fire_at_ms=None,
        )
        occurrence = ReminderOccurrence(
            occurrence_id=str(uuid7()),
            reminder_id=saved.reminder_id,
            owner_session_id=saved.owner_session_id,
            occurrence_no=1,
            anchor_message_id=saved.anchor_message_id,
            scheduled_for_ms=1_000,
            fired_at_ms=1_100,
            next_fire_at_ms=None,
            overdue=True,
            read_at_ms=None,
            created_at_ms=1_100,
        )
        persisted = await transaction.save_fired_occurrence(
            saved.revision, fired, occurrence
        )

        assert await transaction.count_pending_reminder_occurrences("bcn-1") == 1
        assert await transaction.list_pending_reminder_occurrences(
            "bcn-1", limit=100
        ) == (persisted,)
        read = await transaction.mark_reminder_occurrences_read(
            "bcn-1", (persisted.occurrence_id,), read_at_ms=1_200
        )
        assert read[0].read_at_ms == 1_200
        assert await transaction.count_pending_reminder_occurrences("bcn-1") == 0


@pytest.mark.asyncio
async def test_sqlite_recurring_fire_advances_occurrence_number(
    database: SqliteDatabase,
) -> None:
    await seed(database)
    async with database.transaction() as transaction:
        saved = await transaction.save_new_reminder(
            make_reminder(repeat_rule="every:15m")
        )
        current = saved
        for occurrence_no, fired_at_ms in ((1, 1_100), (2, 901_100)):
            scheduled_for_ms = current.next_fire_at_ms
            assert scheduled_for_ms is not None
            next_fire_at_ms = next_recurrence_ms(
                scheduled_for_ms=scheduled_for_ms,
                repeat_rule=current.repeat_rule or "",
                timezone=current.timezone,
            )
            fired = current.record_fire(
                scheduled_for_ms=scheduled_for_ms,
                fired_at_ms=fired_at_ms,
                next_fire_at_ms=next_fire_at_ms,
            )
            occurrence = ReminderOccurrence(
                occurrence_id=str(uuid7()),
                reminder_id=current.reminder_id,
                owner_session_id=current.owner_session_id,
                occurrence_no=occurrence_no,
                anchor_message_id=current.anchor_message_id,
                scheduled_for_ms=scheduled_for_ms,
                fired_at_ms=fired_at_ms,
                next_fire_at_ms=next_fire_at_ms,
                overdue=True,
                read_at_ms=None,
                created_at_ms=fired_at_ms,
            )
            await transaction.save_fired_occurrence(current.revision, fired, occurrence)
            current = fired

        assert current.state is ReminderState.SCHEDULED
        assert current.last_occurrence_no == 2
        assert await transaction.count_pending_reminder_occurrences("bcn-1") == 2


@pytest.mark.asyncio
async def test_sqlite_fire_rollback_preserves_due_reminder(
    database: SqliteDatabase,
) -> None:
    await seed(database)
    async with database.transaction() as transaction:
        saved = await transaction.save_new_reminder(make_reminder())

    with pytest.raises(RuntimeError, match="rollback"):
        async with database.transaction() as transaction:
            fired = saved.record_fire(
                scheduled_for_ms=1_000,
                fired_at_ms=1_100,
                next_fire_at_ms=None,
            )
            occurrence = ReminderOccurrence(
                occurrence_id=str(uuid7()),
                reminder_id=saved.reminder_id,
                owner_session_id=saved.owner_session_id,
                occurrence_no=1,
                anchor_message_id=saved.anchor_message_id,
                scheduled_for_ms=1_000,
                fired_at_ms=1_100,
                next_fire_at_ms=None,
                overdue=True,
                read_at_ms=None,
                created_at_ms=1_100,
            )
            await transaction.save_fired_occurrence(saved.revision, fired, occurrence)
            raise RuntimeError("rollback")

    async with database.transaction() as transaction:
        assert await transaction.get_reminder("bcn-1", saved.reminder_id) == saved
        assert await transaction.count_pending_reminder_occurrences("bcn-1") == 0


@pytest.mark.asyncio
async def test_memory_storage_reminder_contract_matches_sqlite_shape() -> None:
    storage = MemoryStorage()
    await storage.initialize(node_id="node-1", workspace_id="workspace-1")
    await seed(storage)

    async with storage.transaction() as transaction:
        saved = await transaction.save_new_reminder(make_reminder())
        snoozed = saved.snooze(duration_ms=60_000, at_ms=400)
        assert (
            await transaction.save_reminder_transition(saved.revision, snoozed)
            == snoozed
        )
        rollback_source = await transaction.save_new_reminder(
            replace(make_reminder(next_fire_at_ms=2_000), title="Rollback")
        )
        canceled = snoozed.cancel(at_ms=500)
        assert (
            await transaction.save_reminder_transition(snoozed.revision, canceled)
            == canceled
        )

    with pytest.raises(RuntimeError, match="rollback"):
        async with storage.transaction() as transaction:
            changed = rollback_source.update_title("Changed", at_ms=600)
            await transaction.save_reminder_transition(
                rollback_source.revision, changed
            )
            raise RuntimeError("rollback")

    async with storage.transaction() as transaction:
        assert await transaction.get_reminder("bcn-1", saved.reminder_id) == canceled
        assert (
            await transaction.get_reminder("bcn-1", rollback_source.reminder_id)
            == rollback_source
        )
