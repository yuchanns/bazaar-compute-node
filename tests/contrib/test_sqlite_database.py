from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from pathlib import Path
from stat import S_IMODE
from typing import cast

import aiosqlite
import pytest

from bazaar_compute_node.contrib.sqlite import (
    MigrationChecksumError,
    SqliteDatabase,
)
from bazaar_compute_node.contrib.sqlite.codec import (
    inbound_attachment_from_row,
    message_from_row,
    validate_message_input,
)
from bazaar_compute_node.contrib.sqlite.migrations import (
    MESSAGE_UNIFICATION_MIGRATION,
    MIGRATIONS,
    SCHEMA_MIGRATION,
    STORAGE_ACCESS_MIGRATION,
)
from bazaar_compute_node.core.command import (
    MessageDraft,
    MessageSendFreshnessHold,
    OutboundFreshnessPass,
)
from bazaar_compute_node.core.concurrency import ThreadLockRegistry
from bazaar_compute_node.core.models import (
    ChannelSession,
    ChannelTargetKind,
    ConsumerCursor,
    InboundAttachment,
    Message,
    MessageDirection,
    OutboundDeliveryState,
    OwnedReminder,
    Reminder,
    ReminderState,
    RuntimeAttempt,
    SenderIdentity,
    SenderKind,
    SystemMessageKind,
    Thread,
)
from bazaar_compute_node.core.orchestration.reminder import ReminderScheduler
from bazaar_compute_node.core.paths import resolve_data_dir, resolve_workspace_dir
from bazaar_compute_node.core.reminder import render_reminder_fire_body
from bazaar_compute_node.core.storage import IStorage, ThreadNotFoundError
from bazaar_compute_node.core.timerwheel import TimerWheel


@pytest.mark.asyncio
async def test_sqlite_check_drains_every_thread_or_none_of_them() -> None:
    database = SqliteDatabase()
    await database.start(timeout=2)
    try:
        scope = database.scope("agent-1", "Test Agent")
        channel_session = ChannelSession(
            id="channel-1",
            channel="telegram",
            provider_thread_id="thread-1",
            created_at_ms=1,
            updated_at_ms=1,
        )
        thread = Thread(
            id="bcn-1",
            channel_session_id=channel_session.id,
            workspace_id="agent-1",
            created_at_ms=1,
            updated_at_ms=1,
        )
        await scope.save_channel_session(channel_session)
        await scope.save_thread(thread)
        inbound = await scope.save_message(
            Message(
                direction=MessageDirection.INBOUND,
                seq=0,
                message_id="inbound-1",
                thread_id=thread.id,
                channel_session_id=channel_session.id,
                channel="telegram",
                provider_thread_id="thread-1",
                provider_message_id="provider-1",
                received_at_ms=2,
                sender=SenderIdentity(name="sender"),
                message_type="text",
                target="dm:channel-1",
                body="hello",
            )
        )

        with pytest.raises(ThreadNotFoundError):
            await scope.check_messages(
                (thread.id, "bcn-missing"),
                checked_at_ms=3,
            )

        # the conversation that was drained before the failure is unread again
        assert await scope.get_consumer_cursor(thread.id) is None
        drained = await scope.check_messages((thread.id,), checked_at_ms=4)
        assert tuple(message.message_id for message in drained[0].messages) == (
            inbound.message_id,
        )
    finally:
        await database.stop(timeout=2)


@pytest.mark.asyncio
async def test_sqlite_persists_outbound_and_finalizes_once() -> None:
    database = SqliteDatabase()
    await database.start(timeout=2)
    try:
        scope = database.scope("agent-1", "Test Agent")
        channel_session = ChannelSession(
            id="channel-1",
            channel="telegram",
            provider_thread_id="thread-1",
            created_at_ms=1,
            updated_at_ms=1,
        )
        bcn_session = Thread(
            id="bcn-1",
            channel_session_id=channel_session.id,
            workspace_id="agent-1",
            created_at_ms=1,
            updated_at_ms=1,
        )
        pending = Message(
            direction=MessageDirection.OUTBOUND,
            seq=0,
            message_id="outbound-1",
            command_id="command-1",
            thread_id=bcn_session.id,
            channel_session_id=channel_session.id,
            target="dm:channel-1",
            body="hello",
            delivery_state=OutboundDeliveryState.PENDING,
            created_at_ms=2,
            provider_attempted_at_ms=4,
        )

        await scope.save_channel_session(channel_session)
        await scope.save_thread(bcn_session)
        pending = await scope.save_message(pending)
        visible_states = frozenset(
            {OutboundDeliveryState.QUEUED, OutboundDeliveryState.SENT}
        )
        assert (
            await scope.list_messages(
                bcn_session.id,
                delivery_states=visible_states,
            )
            == ()
        )
        sent = pending.transition_to(
            OutboundDeliveryState.SENT,
            at_ms=5,
            provider_message_id="provider-message-1",
        )
        await scope.save_message(sent)
        persisted = await scope.get_message(
            pending.message_id,
            direction=MessageDirection.OUTBOUND,
        )
        history = await scope.read_message_history(
            raw_target=pending.target,
            around_message_id=pending.message_id,
            limit=10,
        )
        catalog = await scope.list_inbox_targets()

        assert persisted == sent
        assert history.history.messages == (sent,)
        assert history.history.snapshot_seq == sent.seq
        assert catalog.targets[0].latest_message_id == sent.message_id
        assert catalog.targets[0].latest_sender == SenderIdentity(name="Test Agent")

        source_channel = ChannelSession(
            id="channel-source",
            channel="telegram",
            provider_thread_id="thread-source",
            created_at_ms=10,
            updated_at_ms=10,
            target_kind=ChannelTargetKind.GROUP,
        )
        source_thread = Thread(
            id="bcn-source",
            channel_session_id=source_channel.id,
            workspace_id="agent-1",
            created_at_ms=10,
            updated_at_ms=10,
        )
        source_message = Message(
            direction=MessageDirection.INBOUND,
            seq=0,
            message_id="018f0000-0000-7000-8000-000000000010",
            thread_id=source_thread.id,
            channel_session_id=source_channel.id,
            channel=source_channel.channel,
            provider_thread_id=source_channel.provider_thread_id,
            provider_message_id="provider-source",
            received_at_ms=10,
            sender=SenderIdentity(name="Source User"),
            target="group:channel-source",
            target_kind=ChannelTargetKind.GROUP,
            body="source context",
            metadata={"sender_kind": SenderKind.HUMAN.value},
        )
        target_channel = ChannelSession(
            id="channel-target",
            channel="telegram",
            provider_thread_id="thread-target",
            created_at_ms=10,
            updated_at_ms=10,
        )
        target_thread = Thread(
            id="bcn-target",
            channel_session_id=target_channel.id,
            workspace_id="agent-1",
            created_at_ms=10,
            updated_at_ms=10,
        )
        await scope.save_channel_session(source_channel)
        await scope.save_thread(source_thread)
        source_message = await scope.save_message(source_message)
        await scope.save_channel_session(target_channel)
        await scope.save_thread(target_thread)
        draft = MessageDraft(
            target="dm:channel-target",
            target_id=target_thread.id,
            body="cross-session payload",
            attachments=(),
            reply_to_message_id=None,
            created_at_ms=11,
        )
        target_message = await scope.save_message(
            Message(
                direction=MessageDirection.INBOUND,
                seq=0,
                message_id="018f0000-0000-7000-8000-000000000020",
                thread_id=target_thread.id,
                channel_session_id=target_channel.id,
                channel=target_channel.channel,
                provider_thread_id=target_channel.provider_thread_id,
                provider_message_id="provider-target",
                received_at_ms=10,
                sender=SenderIdentity(name="Target User"),
                target="dm:channel-target",
                target_kind=ChannelTargetKind.DM,
                body="target context",
                metadata={"sender_kind": SenderKind.HUMAN.value},
            )
        )
        fresh = await scope.check_outbound_freshness(
            target_thread.id,
            snapshot_seq=target_message.seq,
            payload=draft,
            draft_replaced=False,
        )
        assert isinstance(fresh, OutboundFreshnessPass)
        await scope.save_message(
            replace(
                source_message,
                message_id="018f0000-0000-7000-8000-000000000012",
                provider_message_id="provider-source-2",
                received_at_ms=12,
                body="new source context",
            )
        )
        # what the sender's own conversation says does not gate this send
        assert isinstance(
            await scope.check_outbound_freshness(
                target_thread.id,
                snapshot_seq=fresh.current_inbound_seq,
                payload=draft,
                draft_replaced=False,
            ),
            OutboundFreshnessPass,
        )
        newer_target_message = await scope.save_message(
            replace(
                target_message,
                message_id="018f0000-0000-7000-8000-000000000021",
                provider_message_id="provider-target-2",
                received_at_ms=12,
                body="new target context",
            )
        )
        stale_recheck = await scope.materialize_outbound_if_fresh(
            target_thread.id,
            fresh.current_inbound_seq,
            command_id="command-raced",
            payload=draft,
            attempted_at_ms=12,
        )
        assert isinstance(stale_recheck.outcome, MessageSendFreshnessHold)
        assert stale_recheck.outcome.messages == (newer_target_message,)
        assert not any(
            message.command_id == "command-raced"
            for message in await scope.list_messages(
                target_thread.id,
                direction=MessageDirection.OUTBOUND,
            )
        )
        pending = await scope.save_message(
            Message(
                direction=MessageDirection.OUTBOUND,
                seq=0,
                message_id="outbound-target",
                command_id="command-target",
                thread_id=target_thread.id,
                channel_session_id=target_channel.id,
                target="dm:channel-target",
                body="reply",
                delivery_state=OutboundDeliveryState.PENDING,
                created_at_ms=11,
                provider_attempted_at_ms=12,
            )
        )
        sent = pending.transition_to(
            OutboundDeliveryState.SENT,
            at_ms=13,
            provider_message_id="provider-target",
        )
        first_finalize = await scope.finalize_outbound_delivery(sent)
        second_finalize = await scope.finalize_outbound_delivery(sent)
        target_history = await scope.read_message_history(
            raw_target="dm:channel-target",
            around_message_id=pending.message_id,
            limit=10,
        )

        assert first_finalize == second_finalize
        assert target_history.history.messages == (
            target_message,
            newer_target_message,
            first_finalize,
        )
    finally:
        await database.stop(timeout=2)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sender_kind", (SenderKind.HUMAN, SenderKind.AGENT, SenderKind.UNKNOWN)
)
async def test_sqlite_persists_sender_identity_and_kind(
    sender_kind: SenderKind,
) -> None:
    database = SqliteDatabase()
    await database.start(timeout=2)
    try:
        scope = database.scope("agent-1", "Test Agent")
        channel_session = ChannelSession(
            id="channel-1",
            channel="telegram",
            provider_thread_id="thread-1",
            created_at_ms=1,
            updated_at_ms=1,
        )
        bcn_session = Thread(
            id="bcn-1",
            channel_session_id=channel_session.id,
            workspace_id="agent-1",
            created_at_ms=1,
            updated_at_ms=1,
        )
        message = Message(
            direction=MessageDirection.INBOUND,
            seq=0,
            message_id="message-1",
            thread_id=bcn_session.id,
            channel_session_id=channel_session.id,
            channel="telegram",
            provider_thread_id=channel_session.provider_thread_id,
            provider_message_id="provider-message-1",
            received_at_ms=1,
            sender=SenderIdentity(id="test-user-id", name="test-user"),
            message_type="text",
            target="dm:channel-1",
            body="hello",
            metadata={"sender_kind": sender_kind.value},
        )

        await scope.save_channel_session(channel_session)
        await scope.save_thread(bcn_session)
        live = await scope.save_message(message)
        persisted = await scope.find_message(
            *message.inbound_identity(),
            direction=MessageDirection.INBOUND,
        )

        assert live.sender == SenderIdentity(id="test-user-id", name="test-user")
        assert live.sender_kind is sender_kind
        assert persisted is not None
        assert persisted.sender == SenderIdentity(id="test-user-id", name="test-user")
        assert persisted.sender_kind is sender_kind
    finally:
        await database.stop(timeout=2)


@pytest.mark.asyncio
async def test_sqlite_preserves_wecom_sender_without_display_name() -> None:
    database = SqliteDatabase()
    await database.start(timeout=2)
    try:
        scope = database.scope("agent-1", "Test Agent")
        channel_session = ChannelSession(
            id="channel-1",
            channel="wecom",
            provider_thread_id="thread-1",
            created_at_ms=1,
            updated_at_ms=1,
        )
        bcn_session = Thread(
            id="bcn-1",
            channel_session_id=channel_session.id,
            workspace_id="agent-1",
            created_at_ms=1,
            updated_at_ms=1,
        )
        message = Message(
            direction=MessageDirection.INBOUND,
            seq=0,
            message_id="message-1",
            thread_id=bcn_session.id,
            channel_session_id=channel_session.id,
            channel="wecom",
            provider_thread_id=channel_session.provider_thread_id,
            provider_message_id="provider-message-1",
            received_at_ms=1,
            sender=SenderIdentity(id="test-user-id"),
            message_type="text",
            target="dm:channel-1",
            body="hello",
        )

        await scope.save_channel_session(channel_session)
        await scope.save_thread(bcn_session)
        await scope.save_message(message)
        persisted = await scope.find_message(
            *message.inbound_identity(),
            direction=MessageDirection.INBOUND,
        )

        assert persisted is not None
        assert persisted.sender == SenderIdentity(id="test-user-id")
    finally:
        await database.stop(timeout=2)


@pytest.mark.asyncio
async def test_sqlite_atomically_materializes_reminder_system_message() -> None:
    database = SqliteDatabase()
    await database.start(timeout=2)
    try:
        storage = cast(IStorage, database)
        scope = database.scope("agent-1", "Test Agent")
        channel_session = ChannelSession(
            id="channel-1",
            channel="telegram",
            provider_thread_id="thread-1",
            created_at_ms=1,
            updated_at_ms=1,
        )
        bcn_session = Thread(
            id="bcn-1",
            channel_session_id=channel_session.id,
            workspace_id="agent-1",
            created_at_ms=1,
            updated_at_ms=1,
        )
        anchor = Message(
            direction=MessageDirection.INBOUND,
            seq=0,
            message_id="018f0000-0000-7000-8000-000000000002",
            thread_id=bcn_session.id,
            channel_session_id=channel_session.id,
            channel=channel_session.channel,
            provider_thread_id=channel_session.provider_thread_id,
            provider_message_id="provider-message-1",
            received_at_ms=1_000,
            sender=SenderIdentity(id="user-1", name="Test User"),
            target="dm:channel-1",
            body="remember this",
            metadata={"sender_kind": SenderKind.HUMAN.value},
        )
        reminder = Reminder(
            reminder_id="018f0000-0000-7000-8000-000000000001",
            owner_thread_id=bcn_session.id,
            anchor_message_id=anchor.message_id,
            title="Review the pull request",
            state=ReminderState.SCHEDULED,
            next_fire_at_ms=2_000,
            repeat_rule=None,
            timezone="UTC",
            revision=1,
            last_occurrence_no=0,
            created_at_ms=1_000,
            updated_at_ms=1_000,
        )

        await scope.save_channel_session(channel_session)
        await scope.save_thread(bcn_session)
        anchor = await scope.save_message(anchor)
        await scope.save_consumer_cursor(
            ConsumerCursor(
                thread_id=bcn_session.id,
                delivered_through_seq=anchor.seq,
                updated_at_ms=1_100,
            )
        )
        reminder = await scope.save_new_reminder(reminder)
        fired = reminder.record_fire(
            scheduled_for_ms=2_000,
            fired_at_ms=2_100,
            next_fire_at_ms=None,
        )

        def system_message(message_id: str) -> Message:
            return Message(
                direction=MessageDirection.INBOUND,
                seq=0,
                message_id=message_id,
                thread_id=bcn_session.id,
                channel_session_id=channel_session.id,
                channel=channel_session.channel,
                provider_thread_id=channel_session.provider_thread_id,
                provider_message_id=None,
                provider_time_ms=None,
                received_at_ms=2_100,
                sender=SenderIdentity(id="system", name="system"),
                target=anchor.target,
                target_kind=anchor.target_kind,
                body=render_reminder_fire_body(fired, anchor.target, None),
                metadata={
                    "sender_kind": SenderKind.SYSTEM.value,
                    "system_message_kind": SystemMessageKind.REMINDER.value,
                },
            )

        with pytest.raises(ValueError, match="already in use"):
            await storage.materialize_owned_reminder_message(
                reminder.revision,
                OwnedReminder("agent-1", fired),
                system_message(anchor.message_id),
            )
        assert (
            await scope.get_reminder(bcn_session.id, reminder.reminder_id) == reminder
        )

        materialized = await storage.materialize_owned_reminder_message(
            reminder.revision,
            OwnedReminder("agent-1", fired),
            system_message("018f0000-0000-7000-8000-000000000003"),
        )
        assert materialized is not None
        persisted = await scope.get_message(materialized.message_id)
        owners = await storage.list_unread_message_owners()
        catalog = await scope.list_inbox_targets()
        freshness = await scope.check_outbound_freshness(
            bcn_session.id,
            snapshot_seq=anchor.seq,
            payload=MessageDraft(
                target=anchor.target,
                target_id=bcn_session.id,
                body="acknowledged",
                attachments=(),
                reply_to_message_id=None,
                created_at_ms=2_200,
            ),
            draft_replaced=False,
        )
        history = await scope.read_message_history(
            raw_target=anchor.target,
            around_message_id=materialized.message_id,
            limit=10,
        )
        checked = await scope.check_messages(
            (bcn_session.id,),
            checked_at_ms=2_300,
        )

        assert materialized.seq == anchor.seq + 1
        assert persisted == materialized
        assert persisted is not None
        assert persisted.sender == SenderIdentity(id="system", name="system")
        assert persisted.system_message_kind is SystemMessageKind.REMINDER
        assert await scope.get_reminder(bcn_session.id, reminder.reminder_id) == fired
        assert len(owners) == 1
        assert owners[0].agent_id == "agent-1"
        assert owners[0].owner_thread_id == bcn_session.id
        assert owners[0].trigger_message == materialized
        assert catalog.targets[0].pending_count == 1
        assert catalog.targets[0].latest_message_id == materialized.message_id
        assert catalog.targets[0].latest_sender == SenderIdentity(
            id="system", name="system"
        )
        assert catalog.targets[0].last_activity_at_ms == 2_100
        assert isinstance(freshness, MessageSendFreshnessHold)
        assert freshness.messages == (materialized,)
        assert tuple(message.message_id for message in history.history.messages) == (
            anchor.message_id,
            materialized.message_id,
        )
        assert history.history.messages[-1].body == materialized.body
        assert (
            history.history.messages[-1].system_message_kind
            is SystemMessageKind.REMINDER
        )
        assert tuple(message.message_id for message in checked[0].messages) == (
            materialized.message_id,
        )

        assert await storage.list_unread_message_owners() == ()
    finally:
        await database.stop(timeout=2)


@pytest.mark.asyncio
async def test_sqlite_bootstrap_binds_agent_scope_without_node_state() -> None:
    data_dir = resolve_data_dir()
    database = SqliteDatabase()

    await database.start(timeout=2)
    try:
        scope = database.scope("agent-1", "Test Agent")
        assert scope.agent_id == "agent-1"
        assert scope.agent_name == "Test Agent"
        assert not (data_dir / "workspaces" / scope.agent_id).exists()

        async with database.reader() as session, session.transaction():
            tables = await session.fetchall(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            )
            indexes = await session.fetchall(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'index' AND name LIKE 'idx_%' ORDER BY name"
            )
            message_columns = await session.fetchall("PRAGMA table_info(messages)")
            cursor_columns = await session.fetchall(
                "PRAGMA table_info(consumer_cursors)"
            )
            journal_mode = await session.fetchone("PRAGMA journal_mode")
            busy_timeout = await session.fetchone("PRAGMA busy_timeout")
            provider_identity_columns = await session.fetchall(
                "PRAGMA index_info(idx_messages_inbound_provider_identity)"
            )
            message_indexes = await session.fetchall("PRAGMA index_list(messages)")
            migration_columns = await session.fetchall(
                "PRAGMA table_info(schema_migrations)"
            )
            schema_version = await session.fetchone(
                "SELECT MAX(version) AS version FROM schema_migrations"
            )
            compaction_row = await session.fetchone(
                "SELECT compaction_completed_at_ms FROM schema_migrations "
                "WHERE version = 9"
            )

        assert {
            "threads",
            "channel_sessions",
            "consumer_cursors",
            "inbound_attachments",
            "messages",
            "reminders",
            "runtime_attempts",
            "schema_migrations",
        } <= {row["name"] for row in tables}
        assert {
            "idx_messages_agent_direction_seq",
            "idx_messages_agent_thread_target_seq",
            "idx_messages_inbound_provider_identity",
            "idx_messages_outbound_command",
            "idx_messages_outbound_state_created",
            "idx_messages_reply_to_message",
            "idx_threads_channel",
            "idx_channel_sessions_provider_identity",
            "idx_runtime_attempts_session_started",
            "idx_reminders_state_next",
            "idx_reminders_owner_state_updated",
        } <= {row["name"] for row in indexes}
        assert "compaction_completed_at_ms" in {
            row["name"] for row in migration_columns
        }
        assert schema_version is not None
        assert schema_version["version"] == 26
        assert {row["name"] for row in message_columns}.isdisjoint(
            {"snapshot_seq", "current_inbound_seq"}
        )
        assert {row["name"] for row in cursor_columns}.isdisjoint(
            {
                "inbox_snapshot_seq",
                "inbox_snapshot_source",
                "inbox_snapshot_at_ms",
            }
        )
        assert compaction_row is not None
        assert compaction_row["compaction_completed_at_ms"] is not None
        primary_keys = {row["name"]: row["pk"] for row in message_columns}
        assert primary_keys["message_id"] == 1
        assert primary_keys["seq"] == 0
        assert "direction" in primary_keys
        assert "reply_to_message_id" in primary_keys
        assert "delivery_state" in primary_keys
        assert "attachments_json" in primary_keys
        assert "agent_id" in primary_keys
        assert [row["name"] for row in provider_identity_columns] == [
            "agent_id",
            "channel",
            "provider_thread_id",
            "provider_message_id",
        ]
        provider_identity_index = next(
            row
            for row in message_indexes
            if row["name"] == "idx_messages_inbound_provider_identity"
        )
        assert provider_identity_index["unique"] == 1
        assert journal_mode is not None
        assert busy_timeout is not None
        assert journal_mode[0] == "wal"
        assert busy_timeout[0] == 5_000
        if os.name != "nt":
            assert S_IMODE(database.data_dir.stat().st_mode) == 0o700
            assert S_IMODE(database.database_path.stat().st_mode) == 0o600
    finally:
        await database.stop(timeout=2)

    restarted = SqliteDatabase()
    await restarted.start(timeout=2)
    try:
        restarted_scope = restarted.scope("agent-1", "Test Agent")
        assert restarted_scope.agent_id == scope.agent_id
        assert restarted_scope.agent_name == scope.agent_name
    finally:
        await restarted.stop(timeout=2)


@pytest.mark.asyncio
async def test_v19_migrates_pending_reminders_and_v21_drops_handoffs() -> None:
    data_dir = resolve_data_dir()
    data_dir.mkdir()
    database_path = data_dir / "bcn.sqlite3"
    one_time_id = "018f0000-0000-7000-8000-000000000001"
    recurring_id = "018f0000-0000-7000-8000-000000000002"
    read_id = "018f0000-0000-7000-8000-000000000003"
    anchor_id = "018f0000-0000-7000-8000-000000000004"
    discarded_handoff_id = "018f0000-0000-7000-8000-000000000005"

    async with aiosqlite.connect(database_path) as connection:
        for migration in MIGRATIONS[:18]:
            for statement in migration.statements:
                await connection.execute(statement)
            await connection.execute(
                "INSERT INTO schema_migrations "
                "(version, migration_name, checksum, applied_at_ms, duration_ms) "
                "VALUES (?, ?, ?, ?, ?)",
                (migration.version, migration.name, migration.checksum, 1, 0),
            )
            await connection.commit()

        await connection.execute(
            "INSERT INTO channel_sessions "
            "(id, channel, provider_thread_id, target_kind, following, "
            "created_at_ms, updated_at_ms, agent_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("channel-1", "test", "thread-1", "group", 1, 1, 1, "agent-1"),
        )
        await connection.execute(
            "INSERT INTO bcn_sessions "
            "(id, channel_session_id, workspace_id, created_at_ms, updated_at_ms, "
            "agent_id) VALUES (?, ?, ?, ?, ?, ?)",
            ("session-1", "channel-1", "agent-1", 1, 1, "agent-1"),
        )
        await connection.execute(
            "INSERT INTO messages "
            "(message_id, seq, direction, agent_id, session_id, channel_session_id, "
            "channel, provider_thread_id, provider_message_id, received_at_ms, "
            "sender, message_type, target, target_kind, body, mentions_agent, "
            "notifies_runtime, metadata_json) "
            "VALUES (?, ?, 'inbound', ?, ?, ?, ?, ?, ?, ?, ?, 'text', ?, ?, ?, 0, 1, ?)",
            (
                anchor_id,
                7,
                "agent-1",
                "session-1",
                "channel-1",
                "test",
                "thread-1",
                "provider-anchor",
                1,
                "human",
                "group:release",
                "group",
                "anchor",
                '{"sender_kind":"human"}',
            ),
        )
        reminder_rows = (
            (
                one_time_id,
                'Review "release" ✨',
                "fired",
                None,
                None,
                2,
                1,
                1_000,
            ),
            (
                recurring_id,
                'Review "release" ✨',
                "scheduled",
                1_800_000,
                "every:15m",
                2,
                1,
                2_000,
            ),
            (read_id, "Already read", "fired", None, None, 2, 1, 500),
        )
        for (
            reminder_id,
            title,
            state,
            next_fire,
            repeat_rule,
            revision,
            count,
            fired,
        ) in reminder_rows:
            await connection.execute(
                "INSERT INTO reminders "
                "(reminder_id, owner_session_id, anchor_message_id, title, state, "
                "next_fire_at_ms, repeat_rule, timezone, revision, "
                "last_occurrence_no, created_at_ms, updated_at_ms, "
                "last_fired_at_ms, agent_id) "
                "VALUES (?, 'session-1', ?, ?, ?, ?, ?, 'UTC', ?, ?, 1, ?, ?, 'agent-1')",
                (
                    reminder_id,
                    anchor_id,
                    title,
                    state,
                    next_fire,
                    repeat_rule,
                    revision,
                    count,
                    fired,
                    fired,
                ),
            )
        occurrence_rows = (
            (one_time_id, one_time_id, 1_000, None, None),
            (recurring_id, recurring_id, 2_000, 1_800_000, None),
            (read_id, read_id, 500, None, 600),
        )
        for occurrence_id, reminder_id, fired_at, next_fire, read_at in occurrence_rows:
            await connection.execute(
                "INSERT INTO reminder_occurrences "
                "(occurrence_id, reminder_id, owner_session_id, occurrence_no, "
                "anchor_message_id, scheduled_for_ms, fired_at_ms, next_fire_at_ms, "
                "overdue, read_at_ms, created_at_ms, agent_id) "
                "VALUES (?, ?, 'session-1', 1, ?, ?, ?, ?, 0, ?, ?, 'agent-1')",
                (
                    occurrence_id,
                    reminder_id,
                    anchor_id,
                    fired_at,
                    fired_at,
                    next_fire,
                    read_at,
                    fired_at,
                ),
            )
        await connection.execute(
            "INSERT INTO handoffs "
            "(handoff_id, command_id, agent_id, source_session_id, "
            "target_session_id, source_message_id, body, created_at_ms, read_at_ms) "
            "VALUES (?, 'handoff-command', 'agent-1', 'session-1', 'session-1', "
            "?, 'Discarded context.', 1100, NULL)",
            (discarded_handoff_id, anchor_id),
        )
        await connection.commit()

    database = SqliteDatabase()
    await database.start(timeout=2)
    try:
        scope = database.scope("agent-1", "Test Agent")
        messages = await scope.list_messages(
            "session-1",
            direction=MessageDirection.INBOUND,
        )
        migrated = {message.message_id: message for message in messages[1:]}
        one_time = Reminder(
            reminder_id=one_time_id,
            owner_thread_id="session-1",
            anchor_message_id=anchor_id,
            title='Review "release" ✨',
            state=ReminderState.FIRED,
            next_fire_at_ms=None,
            repeat_rule=None,
            timezone="UTC",
            revision=2,
            last_occurrence_no=1,
            created_at_ms=1,
            updated_at_ms=1_000,
            last_fired_at_ms=1_000,
        )
        recurring = Reminder(
            reminder_id=recurring_id,
            owner_thread_id="session-1",
            anchor_message_id=anchor_id,
            title='Review "release" ✨',
            state=ReminderState.SCHEDULED,
            next_fire_at_ms=1_800_000,
            repeat_rule="every:15m",
            timezone="UTC",
            revision=2,
            last_occurrence_no=1,
            created_at_ms=1,
            updated_at_ms=2_000,
            last_fired_at_ms=2_000,
        )

        assert tuple(message.seq for message in messages) == (7, 8, 9)
        assert set(migrated) == {one_time_id, recurring_id}
        assert migrated[one_time_id].body == render_reminder_fire_body(
            one_time,
            "group:release",
            None,
        )
        assert migrated[recurring_id].body == render_reminder_fire_body(
            recurring,
            "group:release",
            1_800_000,
        )
        assert all(
            message.sender_kind is SenderKind.SYSTEM
            and message.system_message_kind is SystemMessageKind.REMINDER
            and message.notifies_runtime
            for message in migrated.values()
        )
        assert discarded_handoff_id not in {
            message.message_id
            for message in await scope.list_messages(
                "session-1",
                direction=MessageDirection.INBOUND,
            )
        }
        async with database.reader() as session, session.transaction():
            occurrence_table = await session.fetchone(
                "SELECT 1 FROM sqlite_master "
                "WHERE type = 'table' AND name = 'reminder_occurrences'"
            )
            handoff_table = await session.fetchone(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'handoffs'"
            )
        assert occurrence_table is None
        assert handoff_table is None
    finally:
        await database.stop(timeout=2)


@pytest.mark.asyncio
async def test_sqlite_migration_checksum_mismatch_fails_closed() -> None:
    data_dir = resolve_data_dir()
    database = SqliteDatabase()
    await database.start(timeout=2)
    await database.stop(timeout=2)

    async with aiosqlite.connect(data_dir / "bcn.sqlite3") as connection:
        await connection.execute(
            "UPDATE schema_migrations SET checksum = ? WHERE version = ?",
            ("tampered", 1),
        )
        await connection.commit()

    restarted = SqliteDatabase()
    with pytest.raises(MigrationChecksumError):
        await restarted.start(timeout=2)
    assert not restarted.is_started


@pytest.mark.asyncio
async def test_sqlite_applies_new_migration_to_existing_v1_database() -> None:
    data_dir = resolve_data_dir()
    data_dir.mkdir()
    database_path = data_dir / "bcn.sqlite3"

    async with aiosqlite.connect(database_path) as connection:
        for statement in SCHEMA_MIGRATION.statements:
            await connection.execute(statement)
        await connection.execute(
            "INSERT INTO schema_migrations "
            "(version, migration_name, checksum, applied_at_ms, duration_ms) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                SCHEMA_MIGRATION.version,
                SCHEMA_MIGRATION.name,
                SCHEMA_MIGRATION.checksum,
                1,
                0,
            ),
        )
        await connection.commit()

    database = SqliteDatabase()
    await database.start(timeout=2)
    try:
        async with database.reader() as session, session.transaction():
            migration_rows = await session.fetchall(
                "SELECT version, migration_name, checksum "
                "FROM schema_migrations ORDER BY version"
            )
            session_indexes = await session.fetchall(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'index' "
                "AND name IN (?, ?, ?, ?, ?, ?, ?, ?) ORDER BY name",
                (
                    "idx_threads_channel",
                    "idx_channel_sessions_provider_identity",
                    "idx_messages_agent_direction_seq",
                    "idx_messages_agent_thread_target_seq",
                    "idx_messages_inbound_provider_identity",
                    "idx_messages_outbound_command",
                    "idx_messages_outbound_state_created",
                    "idx_messages_reply_to_message",
                ),
            )
        assert [row["version"] for row in migration_rows] == [
            migration.version for migration in MIGRATIONS
        ]
        for row, migration in zip(migration_rows, MIGRATIONS, strict=True):
            assert row["migration_name"] == migration.name
            assert row["checksum"] == migration.checksum
        assert {row["name"] for row in session_indexes} == {
            "idx_threads_channel",
            "idx_channel_sessions_provider_identity",
            "idx_messages_agent_direction_seq",
            "idx_messages_agent_thread_target_seq",
            "idx_messages_inbound_provider_identity",
            "idx_messages_outbound_command",
            "idx_messages_outbound_state_created",
            "idx_messages_reply_to_message",
        }
    finally:
        await database.stop(timeout=2)


@pytest.mark.asyncio
async def test_sqlite_v26_removes_handoff_messages_and_keeps_the_rest() -> None:
    data_dir = resolve_data_dir()
    data_dir.mkdir()
    database_path = data_dir / "bcn.sqlite3"

    async with aiosqlite.connect(database_path) as connection:
        connection.row_factory = aiosqlite.Row
        await connection.create_function("bcn_agent_id", 0, lambda: "agent-1")
        await connection.create_function("bcn_agent_name", 0, lambda: "Agent 1")
        for migration in MIGRATIONS[:25]:
            for statement in migration.statements:
                await connection.execute(statement)
            await connection.execute(
                "INSERT INTO schema_migrations "
                "(version, migration_name, checksum, applied_at_ms, duration_ms) "
                "VALUES (?, ?, ?, ?, ?)",
                (migration.version, migration.name, migration.checksum, 1, 0),
            )
        await connection.execute(
            "INSERT INTO channel_sessions ("
            "id, channel, provider_thread_id, target_kind, following, "
            "provider_identity_ref_json, created_at_ms, updated_at_ms, agent_id"
            ") VALUES ('channel-1', 'test', 'thread-1', 'dm', 1, '{}', 1, 1, 'agent-1')"
        )
        await connection.execute(
            "INSERT INTO threads ("
            "id, channel_session_id, workspace_id, created_at_ms, updated_at_ms, "
            "agent_id"
            ") VALUES ('thread-1', 'channel-1', 'agent-1', 1, 1, 'agent-1')"
        )
        await connection.executemany(
            "INSERT INTO messages ("
            "message_id, seq, direction, agent_id, thread_id, channel_session_id, "
            "channel, provider_thread_id, received_at_ms, sender, message_type, "
            "target, target_kind, body, mentions_agent, notifies_runtime, "
            "metadata_json"
            ") VALUES (?, ?, 'inbound', 'agent-1', 'thread-1', 'channel-1', 'test', "
            "'thread-1', ?, 'system', 'text', 'dm:channel-1', 'dm', ?, 0, 1, ?)",
            (
                (
                    "message-handoff",
                    1,
                    1,
                    "handoff note",
                    '{"sender_kind":"system","system_message_kind":"handoff"}',
                ),
                (
                    "message-reminder",
                    2,
                    2,
                    "reminder note",
                    '{"sender_kind":"system","system_message_kind":"reminder"}',
                ),
            ),
        )
        await connection.execute(
            "INSERT INTO consumer_cursors ("
            "thread_id, delivered_through_seq, updated_at_ms"
            ") VALUES ('thread-1', 3, 3)"
        )
        await connection.execute(
            "INSERT INTO reminders ("
            "reminder_id, owner_thread_id, anchor_message_id, title, state, "
            "next_fire_at_ms, revision, last_occurrence_no, created_at_ms, "
            "updated_at_ms, agent_id"
            ") VALUES ('018f0000-0000-7000-8000-000000000009', 'thread-1', "
            "'message-latest-handoff', 'anchored to a handoff', 'scheduled', 100, 1, "
            "0, 1, 1, 'agent-1')"
        )
        await connection.execute(
            "INSERT INTO messages ("
            "message_id, seq, direction, agent_id, thread_id, channel_session_id, "
            "channel, provider_thread_id, message_type, target, target_kind, body, "
            "reply_to_message_id, command_id, delivery_state, created_at_ms, "
            "provider_attempted_at_ms, attachments_json"
            ") VALUES ('outbound-reply', 4, 'outbound', 'agent-1', 'thread-1', "
            "'channel-1', 'test', 'thread-1', 'text', 'dm:channel-1', 'dm', "
            "'answering the handoff', 'message-handoff', 'command-1', 'sent', 4, 4, "
            "'[]')"
        )
        await connection.execute(
            "INSERT INTO messages ("
            "message_id, seq, direction, agent_id, thread_id, channel_session_id, "
            "channel, provider_thread_id, received_at_ms, sender, message_type, "
            "target, target_kind, body, mentions_agent, notifies_runtime, "
            "metadata_json"
            ") VALUES ('message-latest-handoff', 3, 'inbound', 'agent-1', 'thread-1', "
            "'channel-1', 'test', 'thread-1', 3, 'system', 'text', 'dm:channel-1', "
            "'dm', 'handoff note', 0, 1, "
            '\'{"sender_kind":"system","system_message_kind":"handoff"}\')'
        )
        await connection.commit()

    database = SqliteDatabase()
    await database.start(timeout=2)
    try:
        scope = database.scope("agent-1", "Agent 1")
        checked = await scope.check_messages(("thread-1",), checked_at_ms=9)
        async with database.reader() as session, session.transaction():
            cursor_row = await session.fetchone(
                "SELECT delivered_through_seq FROM consumer_cursors "
                "WHERE thread_id = 'thread-1'"
            )
            remaining = await session.fetchall(
                "SELECT message_id FROM messages ORDER BY seq"
            )
            reminders = await session.fetchall("SELECT reminder_id FROM reminders")
            schema_version = await session.fetchone(
                "SELECT MAX(version) AS version FROM schema_migrations"
            )

        # what no release writes any more is gone, and what one still writes stays
        assert [row["message_id"] for row in remaining] == [
            "message-reminder",
            "outbound-reply",
        ]
        # a Reminder anchored to one of them goes with it, rather than waiting
        # to be canceled for a missing anchor when it comes due
        assert reminders == []
        # a cursor left pointing at a deleted message comes back into range, so
        # the next check does not read as a cursor moving backwards
        assert cursor_row is not None
        assert cursor_row["delivered_through_seq"] == 2
        assert checked[0].messages == ()

        # an outbound reply to a deleted message still reads back
        history = await scope.read_message_history(
            raw_target="dm:channel-1",
            around_message_id=None,
            limit=10,
        )
        assert [message.message_id for message in history.history.messages] == [
            "message-reminder",
            "outbound-reply",
        ]
        assert history.history.messages[-1].reply_to_message_id is None

        # a sequence freed by the delete can be handed out again, and the
        # clamped cursor still lets that message through
        await scope.save_message(
            Message(
                direction=MessageDirection.INBOUND,
                seq=0,
                message_id="inbound-after-upgrade",
                thread_id="thread-1",
                channel_session_id="channel-1",
                channel="test",
                provider_thread_id="thread-1",
                provider_message_id="provider-after-upgrade",
                received_at_ms=10,
                sender=SenderIdentity(name="sender"),
                message_type="text",
                target="dm:channel-1",
                body="after upgrade",
            )
        )
        drained_after = await scope.check_messages(("thread-1",), checked_at_ms=11)
        assert tuple(message.message_id for message in drained_after[0].messages) == (
            "inbound-after-upgrade",
        )
        assert schema_version is not None
        assert schema_version["version"] == 26
    finally:
        await database.stop(timeout=2)


@pytest.mark.asyncio
async def test_sqlite_v13_migration_preserves_durable_session_and_attempt_facts() -> (
    None
):
    data_dir = resolve_data_dir()
    data_dir.mkdir()
    database_path = data_dir / "bcn.sqlite3"

    async with aiosqlite.connect(database_path) as connection:
        for migration in MIGRATIONS[:10]:
            for statement in migration.statements:
                await connection.execute(statement)
            await connection.execute(
                "INSERT INTO schema_migrations "
                "(version, migration_name, checksum, applied_at_ms, duration_ms) "
                "VALUES (?, ?, ?, ?, ?)",
                (migration.version, migration.name, migration.checksum, 1, 0),
            )
        await connection.execute(
            "INSERT INTO node_state ("
            "singleton_key, node_id, schema_version, workspace_id, "
            "created_at_ms, updated_at_ms, metadata_json"
            ") VALUES (1, 'node-1', 10, 'workspace-1', 1, 1, '{}')"
        )
        await connection.execute(
            "INSERT INTO channel_sessions ("
            "id, channel, provider_thread_id, target_kind, following, "
            "provider_identity_ref_json, created_at_ms, updated_at_ms"
            ") VALUES ('channel-1', 'test', 'thread-1', 'dm', 1, '{}', 1, 1)"
        )
        await connection.execute(
            "INSERT INTO bcn_sessions ("
            "id, channel_session_id, workspace_id, created_at_ms, updated_at_ms, "
            "metadata_json"
            ") VALUES ('bcn-1', 'channel-1', 'workspace-1', 1, 1, '{}')"
        )
        await connection.execute(
            "INSERT INTO runtime_sessions ("
            "id, bcn_session_id, channel_session_id, runtime, created_at_ms, "
            "updated_at_ms, metadata_json"
            ") VALUES ('runtime-1', 'bcn-1', 'channel-1', 'test', 1, 1, '{}')"
        )
        await connection.execute(
            "INSERT INTO runtime_attempts "
            "(turn_id, session_id, client_user_message_id, started_at_ms) "
            "VALUES ('turn-1', 'runtime-1', 'message-1', 2)"
        )
        await connection.commit()

    database = SqliteDatabase()
    await database.start(timeout=2)
    try:
        scope = database.scope("workspace-1", "default")
        async with database.reader() as session, session.transaction():
            schema_version = await session.fetchone(
                "SELECT MAX(version) AS version FROM schema_migrations"
            )
            node_state = await session.fetchone(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name = 'node_state'"
            )
            ownership_rows = await session.fetchall(
                "SELECT agent_id FROM channel_sessions WHERE id = 'channel-1' "
                "UNION ALL "
                "SELECT agent_id FROM threads WHERE id = 'bcn-1' "
                "UNION ALL "
                "SELECT agent_id FROM runtime_attempts WHERE turn_id = 'turn-1'"
            )
        assert schema_version is not None
        assert schema_version["version"] == 26
        assert node_state is None
        assert [row["agent_id"] for row in ownership_rows] == [
            "workspace-1",
            "workspace-1",
            "workspace-1",
        ]
        retained_attempt = RuntimeAttempt(
            turn_id="turn-1",
            session_id="runtime-1",
            client_user_message_id="message-1",
            started_at_ms=2,
        )
        new_attempt = RuntimeAttempt(
            turn_id="turn-2",
            session_id="runtime-2",
            client_user_message_id="message-2",
            started_at_ms=3,
        )
        assert await scope.get_channel_session("channel-1") == ChannelSession(
            id="channel-1",
            channel="test",
            provider_thread_id="thread-1",
            created_at_ms=1,
            updated_at_ms=1,
        )
        assert await scope.get_thread("bcn-1") == Thread(
            id="bcn-1",
            channel_session_id="channel-1",
            workspace_id="workspace-1",
            created_at_ms=1,
            updated_at_ms=1,
        )
        assert await scope.get_runtime_attempt("turn-1") == retained_attempt
        await scope.save_runtime_attempt(new_attempt)
        assert await scope.get_runtime_attempt("turn-2") == new_attempt
    finally:
        await database.stop(timeout=2)


@pytest.mark.asyncio
async def test_sqlite_removes_runtime_events_and_node_state() -> None:
    data_dir = resolve_data_dir()
    data_dir.mkdir()
    database_path = data_dir / "bcn.sqlite3"

    async with aiosqlite.connect(database_path) as connection:
        for migration in MIGRATIONS[:8]:
            for statement in migration.statements:
                await connection.execute(statement)
            await connection.execute(
                "INSERT INTO schema_migrations "
                "(version, migration_name, checksum, applied_at_ms, duration_ms) "
                "VALUES (?, ?, ?, ?, ?)",
                (migration.version, migration.name, migration.checksum, 1, 0),
            )
        await connection.execute(
            "INSERT INTO node_state ("
            "singleton_key, node_id, schema_version, workspace_id, "
            "created_at_ms, updated_at_ms, metadata_json"
            ") VALUES (1, 'node-retained', 8, 'workspace-retained', 1, 1, '{}')"
        )
        await connection.executemany(
            "INSERT INTO runtime_events ("
            "event_seq, event_id, created_at_ms, level, event_name, state, "
            "metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                (
                    event_seq,
                    f"event-{event_seq}",
                    event_seq,
                    "info",
                    "runtime.turn.completed",
                    "completed",
                    "x" * 4096,
                )
                for event_seq in range(1, 2001)
            ),
        )
        await connection.commit()
    size_before = database_path.stat().st_size

    database = SqliteDatabase()
    await database.start(timeout=10)
    try:
        wal_path = Path(f"{database_path}-wal")
        assert not wal_path.exists() or wal_path.stat().st_size == 0
        async with database.reader() as session, session.transaction():
            runtime_objects = await session.fetchall(
                "SELECT name FROM sqlite_master WHERE name = 'runtime_events' "
                "OR name LIKE 'idx_runtime_events_%'"
            )
            node_state = await session.fetchone(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name = 'node_state'"
            )
            schema_version = await session.fetchone(
                "SELECT MAX(version) AS version FROM schema_migrations"
            )
            marker = await session.fetchone(
                "SELECT compaction_completed_at_ms FROM schema_migrations "
                "WHERE version = 9"
            )
            freelist = await session.fetchone("PRAGMA freelist_count")
            quick_check = await session.fetchone("PRAGMA quick_check")

        assert not runtime_objects
        assert node_state is None
        assert schema_version is not None
        assert schema_version["version"] == 26
        assert marker is not None
        assert marker["compaction_completed_at_ms"] is not None
        assert freelist is not None
        assert freelist[0] == 0
        assert quick_check is not None
        assert quick_check[0] == "ok"
        assert database_path.stat().st_size < size_before // 2
    finally:
        await database.stop(timeout=2)


@pytest.mark.asyncio
async def test_sqlite_v16_fixture_upgrades_without_ownership_triggers() -> None:
    data_dir = resolve_data_dir()
    data_dir.mkdir()
    database_path = data_dir / "bcn.sqlite3"

    async with aiosqlite.connect(database_path) as connection:
        await connection.create_function("bcn_agent_id", 0, lambda: "agent-1")
        await connection.create_function("bcn_agent_name", 0, lambda: "Test Agent")
        for migration in MIGRATIONS[:15]:
            for statement in migration.statements:
                await connection.execute(statement)
            await connection.execute(
                "INSERT INTO schema_migrations "
                "(version, migration_name, checksum, applied_at_ms, duration_ms) "
                "VALUES (?, ?, ?, ?, ?)",
                (migration.version, migration.name, migration.checksum, 1, 0),
            )
        await connection.execute(
            "INSERT INTO channel_sessions ("
            "id, channel, provider_thread_id, target_kind, following, "
            "created_at_ms, updated_at_ms, agent_id"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "channel-1",
                "test",
                "message-1",
                "dm",
                1,
                1,
                1,
                "agent-1",
            ),
        )
        await connection.executemany(
            "INSERT INTO outbound_messages ("
            "outbound_message_id, command_id, session_id, channel_session_id, "
            "target, body, state, fresh_check_state, snapshot_seq, "
            "current_inbound_seq, created_at_ms, provider_attempted_at_ms, "
            "completed_at_ms, draft_saved_at_ms, metadata_json, attachments_json, "
            "agent_id, agent_name"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                (
                    "outbound-draft",
                    "command-draft",
                    "session-1",
                    "channel-1",
                    "#test:message-1",
                    "draft",
                    "draft",
                    "required",
                    None,
                    None,
                    1,
                    None,
                    None,
                    None,
                    "{}",
                    "[]",
                    "agent-1",
                    "Test Agent",
                ),
                (
                    "outbound-rejected",
                    "command-rejected",
                    "session-1",
                    "channel-1",
                    "#test:message-1",
                    "rejected",
                    "rejected",
                    "failed",
                    1,
                    2,
                    2,
                    None,
                    3,
                    3,
                    "{}",
                    "[]",
                    "agent-1",
                    "Test Agent",
                ),
                (
                    "outbound-sent",
                    "command-sent",
                    "session-1",
                    "channel-1",
                    "#test:message-1",
                    "sent",
                    "sent",
                    "passed",
                    0,
                    0,
                    4,
                    5,
                    6,
                    None,
                    "{}",
                    "[]",
                    "agent-1",
                    "Test Agent",
                ),
            ),
        )
        await connection.commit()

    database = SqliteDatabase()
    await database.start(timeout=2)
    try:
        async with database.reader() as session, session.transaction():
            rows = await session.fetchall(
                "SELECT message_id, delivery_state, provider_attempted_at_ms, completed_at_ms, "
                "attachments_json, agent_id, sender "
                "FROM messages WHERE direction = 'outbound' ORDER BY message_id"
            )
            columns = await session.fetchall("PRAGMA table_info(messages)")
            indexes = await session.fetchall("PRAGMA index_list(messages)")
            triggers = await session.fetchall(
                "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                "AND name IN (?, ?, ?, ?, ?, ?, ?)",
                (
                    "set_channel_sessions_agent_id",
                    "set_bcn_sessions_agent_id",
                    "set_inbound_messages_agent_id",
                    "set_outbound_messages_agent_identity",
                    "set_runtime_attempts_agent_id",
                    "set_reminders_agent_id",
                    "set_reminder_occurrences_agent_id",
                ),
            )
        assert [dict(row) for row in rows] == [
            {
                "message_id": "outbound-sent",
                "delivery_state": "sent",
                "provider_attempted_at_ms": 5,
                "completed_at_ms": 6,
                "attachments_json": "[]",
                "agent_id": "agent-1",
                "sender": "Test Agent",
            }
        ]
        assert {row["name"] for row in columns}.isdisjoint(
            {
                "fresh_check_state",
                "snapshot_seq",
                "current_inbound_seq",
                "draft_saved_at_ms",
                "next_action",
            }
        )
        assert {row["name"] for row in indexes} >= {
            "idx_messages_outbound_command",
            "idx_messages_outbound_state_created",
        }
        assert triggers == []
    finally:
        await database.stop(timeout=2)


@pytest.mark.asyncio
async def test_sqlite_v16_fixture_unifies_message_history() -> None:
    data_dir = resolve_data_dir()
    data_dir.mkdir()
    database_path = data_dir / "bcn.sqlite3"

    async with aiosqlite.connect(database_path) as connection:
        connection.row_factory = aiosqlite.Row
        await connection.create_function("bcn_agent_id", 0, lambda: "agent-a")
        await connection.create_function("bcn_agent_name", 0, lambda: "Agent A")
        for migration in MIGRATIONS[:16]:
            for statement in migration.statements:
                await connection.execute(statement)
            await connection.execute(
                "INSERT INTO schema_migrations "
                "(version, migration_name, checksum, applied_at_ms, duration_ms) "
                "VALUES (?, ?, ?, ?, ?)",
                (migration.version, migration.name, migration.checksum, 1, 0),
            )

        await connection.executemany(
            "INSERT INTO channel_sessions ("
            "id, channel, provider_thread_id, target_kind, following, "
            "created_at_ms, updated_at_ms, agent_id"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ("channel-a", "telegram", "thread-a", "dm", 1, 1, 1, "agent-a"),
                (
                    "channel-b",
                    "telegram",
                    "thread-b",
                    "group",
                    1,
                    1,
                    1,
                    "agent-b",
                ),
            ),
        )
        await connection.executemany(
            "INSERT INTO bcn_sessions ("
            "id, channel_session_id, workspace_id, created_at_ms, updated_at_ms, "
            "agent_id"
            ") VALUES (?, ?, ?, ?, ?, ?)",
            (
                ("session-a", "channel-a", "agent-a", 1, 1, "agent-a"),
                ("session-b", "channel-b", "agent-b", 1, 1, "agent-b"),
            ),
        )
        await connection.executemany(
            "INSERT INTO inbound_messages ("
            "message_id, seq, session_id, channel_session_id, channel, "
            "provider_thread_id, provider_message_id, provider_time_ms, "
            "received_at_ms, sender, message_type, canonical_target, target_kind, "
            "reply_to_message_id, body, mentions_agent, notifies_runtime, "
            "provider_payload_ref, metadata_json, agent_id"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                (
                    "inbound-a-1",
                    1,
                    "session-a",
                    "channel-a",
                    "telegram",
                    "thread-a",
                    "provider-a-1",
                    100,
                    110,
                    "Alice",
                    "text",
                    "dm:channel-a",
                    "dm",
                    None,
                    "first inbound",
                    1,
                    1,
                    "payload-a-1",
                    '{"sender_kind":"human"}',
                    "agent-a",
                ),
                (
                    "inbound-a-2",
                    2,
                    "session-a",
                    "channel-a",
                    "telegram",
                    "thread-a",
                    "provider-a-2",
                    200,
                    210,
                    "Alice",
                    "text",
                    "dm:channel-a",
                    "dm",
                    "inbound-a-1",
                    "second inbound",
                    1,
                    1,
                    None,
                    "{}",
                    "agent-a",
                ),
                (
                    "inbound-b-1",
                    3,
                    "session-b",
                    "channel-b",
                    "telegram",
                    "thread-b",
                    "provider-b-1",
                    None,
                    350,
                    "Bob",
                    "text",
                    "group:channel-b",
                    "group",
                    None,
                    "group inbound",
                    0,
                    1,
                    None,
                    "{}",
                    "agent-b",
                ),
            ),
        )
        await connection.execute(
            "INSERT INTO inbound_attachments ("
            "attachment_id, message_id, ordinal, name, kind, state, media_type, "
            "relative_path, size_bytes, error"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "attachment-a-2",
                "inbound-a-2",
                0,
                "note.txt",
                "file",
                "ready",
                "text/plain",
                "attachments/note.txt",
                12,
                None,
            ),
        )

        outbound_rows = (
            (
                "outbound-pending",
                "command-pending",
                "session-a",
                "channel-a",
                "dm:channel-a",
                None,
                "pending",
                "pending",
                1,
                1,
                None,
                None,
                150,
                150,
                None,
                None,
                None,
                "{}",
                "[]",
                "agent-a",
                "Agent A",
            ),
            (
                "outbound-queued",
                "command-queued",
                "session-a",
                "channel-a",
                "dm:channel-a",
                "inbound-a-2",
                "queued",
                "queued",
                2,
                2,
                None,
                None,
                250,
                250,
                None,
                None,
                None,
                "{}",
                "[]",
                "agent-a",
                "Agent A",
            ),
            (
                "outbound-sent",
                "command-sent",
                "session-a",
                "channel-a",
                "dm:channel-a",
                "inbound-a-2",
                "sent",
                "sent",
                2,
                2,
                "provider-outbound-sent",
                "receipt-sent",
                300,
                300,
                301,
                None,
                None,
                "{}",
                '[{"name":"report.txt","relative_path":"files/report.txt",'
                '"media_type":"text/plain","size_bytes":42,"sha256":"'
                + "a" * 64
                + '"}]',
                "agent-a",
                "Agent A",
            ),
            (
                "outbound-partial",
                "command-partial",
                "session-b",
                "channel-b",
                "group:channel-b",
                "inbound-b-1",
                "partial",
                "partial",
                3,
                3,
                None,
                "receipt-partial",
                400,
                400,
                401,
                "partial_delivery",
                "one part failed",
                "{}",
                "[]",
                "agent-b",
                "Agent B",
            ),
            (
                "outbound-failed",
                "command-failed",
                "session-b",
                "channel-b",
                "group:channel-b",
                None,
                "failed",
                "failed",
                3,
                3,
                None,
                None,
                500,
                500,
                501,
                "provider_error",
                "delivery failed",
                "{}",
                "[]",
                "agent-b",
                "Agent B",
            ),
            (
                "outbound-unknown",
                "command-unknown",
                "session-b",
                "channel-b",
                "group:channel-b",
                None,
                "unknown",
                "unknown",
                3,
                3,
                None,
                None,
                600,
                600,
                601,
                "unknown_result",
                "result unknown",
                "{}",
                "[]",
                "agent-b",
                "Agent B",
            ),
        )
        await connection.executemany(
            "INSERT INTO outbound_messages ("
            "outbound_message_id, command_id, session_id, channel_session_id, "
            "target, reply_to_message_id, body, state, snapshot_seq, "
            "current_inbound_seq, provider_message_id, provider_receipt_ref, "
            "created_at_ms, provider_attempted_at_ms, completed_at_ms, error_kind, "
            "error_message, metadata_json, attachments_json, agent_id, agent_name"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            outbound_rows,
        )
        await connection.executemany(
            "INSERT INTO consumer_cursors ("
            "session_id, delivered_through_seq, inbox_snapshot_seq, "
            "inbox_snapshot_source, inbox_snapshot_at_ms, updated_at_ms"
            ") VALUES (?, ?, ?, ?, ?, ?)",
            (
                ("session-a", 2, 2, "check", 220, 220),
                ("session-b", 3, 3, "read", 360, 360),
            ),
        )
        await connection.commit()

        old_boundary_ids = {
            row["seq"]: row["message_id"]
            for row in await connection.execute_fetchall(
                "SELECT seq, message_id FROM inbound_messages"
            )
        }
        await connection.execute("BEGIN")
        for migration in (
            STORAGE_ACCESS_MIGRATION,
            MESSAGE_UNIFICATION_MIGRATION,
        ):
            for statement in migration.statements:
                await connection.execute(statement)
            await connection.execute(
                "INSERT INTO schema_migrations "
                "(version, migration_name, checksum, applied_at_ms, duration_ms) "
                "VALUES (?, ?, ?, ?, ?)",
                (migration.version, migration.name, migration.checksum, 1, 0),
            )
        await connection.commit()

        tables = {
            row["name"]
            for row in await connection.execute_fetchall(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        indexes = {
            row["name"]
            for row in await connection.execute_fetchall(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'index' AND name LIKE 'idx_messages_%'"
            )
        }
        rows = await connection.execute_fetchall("SELECT * FROM messages ORDER BY seq")
        cursors = await connection.execute_fetchall(
            "SELECT session_id, delivered_through_seq, inbox_snapshot_seq "
            "FROM consumer_cursors ORDER BY session_id"
        )
        attachment_row = await (
            await connection.execute(
                "SELECT attachment_id, name, kind, state, media_type, relative_path, "
                "size_bytes, error FROM inbound_attachments "
                "WHERE message_id = 'inbound-a-2'"
            )
        ).fetchone()
        schema_version = await (
            await connection.execute(
                "SELECT MAX(version) AS version FROM schema_migrations"
            )
        ).fetchone()
        quick_check = await (await connection.execute("PRAGMA quick_check")).fetchone()

    assert "messages" in tables
    assert "inbound_messages" not in tables
    assert "outbound_messages" not in tables
    assert indexes == {
        "idx_messages_agent_direction_seq",
        "idx_messages_agent_session_target_seq",
        "idx_messages_inbound_provider_identity",
        "idx_messages_outbound_command",
        "idx_messages_outbound_state_created",
        "idx_messages_reply_to_message",
    }
    assert [(row["message_id"], row["seq"]) for row in rows] == [
        ("inbound-a-1", 1),
        ("outbound-pending", 2),
        ("inbound-a-2", 3),
        ("outbound-queued", 4),
        ("outbound-sent", 5),
        ("inbound-b-1", 6),
        ("outbound-partial", 7),
        ("outbound-failed", 8),
        ("outbound-unknown", 9),
    ]
    assert {row["direction"] for row in rows} == {"inbound", "outbound"}
    assert {
        row["delivery_state"] for row in rows if row["direction"] == "outbound"
    } == {"pending", "queued", "sent", "partial", "failed", "unknown"}
    assert {(row["agent_id"], row["sender"]) for row in rows} >= {
        ("agent-a", "Agent A"),
        ("agent-b", "Agent B"),
    }
    assert [dict(row) for row in cursors] == [
        {
            "session_id": "session-a",
            "delivered_through_seq": 3,
            "inbox_snapshot_seq": 3,
        },
        {
            "session_id": "session-b",
            "delivered_through_seq": 6,
            "inbox_snapshot_seq": 6,
        },
    ]

    migrated_by_id = {row["message_id"]: row for row in rows}
    for outbound_row in outbound_rows:
        old_snapshot_seq = outbound_row[8]
        old_current_seq = outbound_row[9]
        migrated = migrated_by_id[outbound_row[0]]
        assert old_boundary_ids[old_snapshot_seq] == next(
            row["message_id"] for row in rows if row["seq"] == migrated["snapshot_seq"]
        )
        assert old_boundary_ids[old_current_seq] == next(
            row["message_id"]
            for row in rows
            if row["seq"] == migrated["current_inbound_seq"]
        )
    for cursor, old_seq in zip(cursors, (2, 3), strict=True):
        assert old_boundary_ids[old_seq] == next(
            row["message_id"]
            for row in rows
            if row["seq"] == cursor["delivered_through_seq"]
        )
        assert old_boundary_ids[old_seq] == next(
            row["message_id"]
            for row in rows
            if row["seq"] == cursor["inbox_snapshot_seq"]
        )

    assert attachment_row is not None
    inbound_attachment = inbound_attachment_from_row(attachment_row)
    assert inbound_attachment == InboundAttachment(
        attachment_id="attachment-a-2",
        name="note.txt",
        kind="file",
        state="ready",
        media_type="text/plain",
        relative_path="attachments/note.txt",
        size_bytes=12,
    )
    decoded = [
        message_from_row(
            cast(
                aiosqlite.Row,
                {
                    **dict(row),
                    "thread_id": row["session_id"],
                    "sender_id": None,
                    "sender_display_name": None,
                },
            ),
            (inbound_attachment,) if row["message_id"] == "inbound-a-2" else (),
        )
        for row in rows
    ]
    for message in decoded:
        validate_message_input(message)
    second_inbound = next(
        message for message in decoded if message.message_id == "inbound-a-2"
    )
    sent = next(message for message in decoded if message.message_id == "outbound-sent")
    assert second_inbound.reply_to_message_id == "inbound-a-1"
    assert second_inbound.attachments == (inbound_attachment,)
    assert sent.seq == 5
    assert sent.sender == SenderIdentity(name="Agent A")
    assert sent.reply_to_message_id == "inbound-a-2"
    assert sent.attachments[0].relative_path == "files/report.txt"
    assert schema_version is not None
    assert schema_version["version"] == 18
    assert quick_check is not None
    assert quick_check[0] == "ok"


@pytest.mark.asyncio
async def test_sqlite_retries_unmarked_post_migration_compaction() -> None:
    data_dir = resolve_data_dir()
    data_dir.mkdir()
    database_path = data_dir / "bcn.sqlite3"

    async with aiosqlite.connect(database_path) as connection:
        for migration in MIGRATIONS[:8]:
            for statement in migration.statements:
                await connection.execute(statement)
            await connection.execute(
                "INSERT INTO schema_migrations "
                "(version, migration_name, checksum, applied_at_ms, duration_ms) "
                "VALUES (?, ?, ?, ?, ?)",
                (migration.version, migration.name, migration.checksum, 1, 0),
            )
        await connection.executemany(
            "INSERT INTO runtime_events ("
            "event_seq, event_id, created_at_ms, level, event_name, state, "
            "metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                (
                    event_seq,
                    f"event-{event_seq}",
                    event_seq,
                    "info",
                    "runtime.turn.completed",
                    "completed",
                    "x" * 4096,
                )
                for event_seq in range(1, 1001)
            ),
        )
        migration = MIGRATIONS[8]
        for statement in migration.statements:
            await connection.execute(statement)
        await connection.execute(
            "INSERT INTO schema_migrations "
            "(version, migration_name, checksum, applied_at_ms, duration_ms) "
            "VALUES (?, ?, ?, ?, ?)",
            (migration.version, migration.name, migration.checksum, 1, 0),
        )
        await connection.commit()
    size_before = database_path.stat().st_size

    database = SqliteDatabase()
    await database.start(timeout=10)
    try:
        async with database.reader() as session, session.transaction():
            marker = await session.fetchone(
                "SELECT compaction_completed_at_ms FROM schema_migrations "
                "WHERE version = 9"
            )
            quick_check = await session.fetchone("PRAGMA quick_check")
        assert marker is not None
        assert marker["compaction_completed_at_ms"] is not None
        assert quick_check is not None
        assert quick_check[0] == "ok"
        assert database_path.stat().st_size < size_before // 2
    finally:
        await database.stop(timeout=2)


@pytest.mark.asyncio
async def test_sqlite_migrates_provider_reply_ids_to_internal_message_ids() -> None:
    data_dir = resolve_data_dir()
    data_dir.mkdir()
    database_path = data_dir / "bcn.sqlite3"

    async with aiosqlite.connect(database_path) as connection:
        for migration in MIGRATIONS[:4]:
            for statement in migration.statements:
                await connection.execute(statement)
            await connection.execute(
                "INSERT INTO schema_migrations "
                "(version, migration_name, checksum, applied_at_ms, duration_ms) "
                "VALUES (?, ?, ?, ?, ?)",
                (migration.version, migration.name, migration.checksum, 1, 0),
            )
        await connection.execute(
            "INSERT INTO node_state ("
            "singleton_key, node_id, schema_version, workspace_id, "
            "created_at_ms, updated_at_ms, metadata_json"
            ") VALUES (1, 'node-legacy', 4, 'agent-legacy', 1, 1, '{}')"
        )
        for message_id, seq, session_id, provider_message_id, reply_id in (
            (
                "message-cross-session",
                1,
                "session-2",
                "provider-cross-session",
                None,
            ),
            ("message-reference", 2, "session-1", "provider-reference", None),
            (
                "message-current",
                3,
                "session-1",
                "provider-current",
                "provider-reference",
            ),
            (
                "message-cross-session-current",
                4,
                "session-1",
                "provider-cross-session-current",
                "provider-cross-session",
            ),
            (
                "message-future-current",
                5,
                "session-1",
                "provider-future-current",
                "provider-future",
            ),
            ("message-future", 6, "session-1", "provider-future", None),
            (
                "message-unresolved",
                7,
                "session-1",
                "provider-unresolved",
                "provider-missing",
            ),
        ):
            await connection.execute(
                "INSERT INTO inbound_messages ("
                "message_id, seq, session_id, channel_session_id, channel, "
                "provider_thread_id, provider_message_id, received_at_ms, sender, "
                "message_type, canonical_target, target_kind, "
                "reply_to_provider_message_id, body, mentions_agent, "
                "notifies_runtime, metadata_json"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    message_id,
                    seq,
                    session_id,
                    "channel-session-1",
                    "wecom",
                    "provider-thread-1",
                    provider_message_id,
                    seq,
                    "user-1",
                    "text",
                    "dm:channel-session-1",
                    "dm",
                    reply_id,
                    "body",
                    0,
                    1,
                    "{}",
                ),
            )
        await connection.commit()

    database = SqliteDatabase()
    await database.start(timeout=2)
    try:
        async with database.reader() as session, session.transaction():
            rows = await session.fetchall(
                "SELECT message_id, reply_to_message_id "
                "FROM messages WHERE direction = 'inbound' ORDER BY seq"
            )
            quick_check = await session.fetchone("PRAGMA quick_check")
        assert [(row["message_id"], row["reply_to_message_id"]) for row in rows] == [
            ("message-cross-session", None),
            ("message-reference", None),
            ("message-current", "message-reference"),
            ("message-cross-session-current", None),
            ("message-future-current", None),
            ("message-future", None),
            ("message-unresolved", None),
        ]
        assert quick_check is not None
        assert quick_check[0] == "ok"
    finally:
        await database.stop(timeout=2)


def test_resolve_data_dir_uses_the_configured_home_data_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BCN_DATA_NAME", ".bcn-custom")
    assert resolve_data_dir() == Path.home() / ".bcn-custom"


@pytest.mark.asyncio
async def test_sqlite_uses_configured_database_name() -> None:
    database = SqliteDatabase(database_name="task.sqlite3")

    await database.start(timeout=2)
    try:
        assert database.database_path == resolve_data_dir() / "task.sqlite3"
        assert database.database_path.exists()
        assert not (resolve_data_dir() / "bcn.sqlite3").exists()
    finally:
        await database.stop(timeout=2)


@pytest.mark.parametrize("value", ["", ".", "..", "sub/task.sqlite3", "sub\\task"])
def test_sqlite_rejects_database_paths(value: str) -> None:
    with pytest.raises(ValueError, match="single path component"):
        SqliteDatabase(database_name=value)


def test_resolve_data_dir_defaults_to_home_bcn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BCN_DATA_NAME")
    assert resolve_data_dir() == Path.home() / ".bcn"


@pytest.mark.parametrize("data_name", ("", ".", "..", "nested/name", "nested\\name"))
def test_resolve_data_dir_rejects_invalid_data_names(
    monkeypatch: pytest.MonkeyPatch,
    data_name: str,
) -> None:
    monkeypatch.setenv("BCN_DATA_NAME", data_name)
    with pytest.raises(ValueError, match="single path component"):
        resolve_data_dir()


def test_default_workspace_uses_the_home_bcn_root() -> None:
    assert (
        resolve_workspace_dir("workspace-1")
        == resolve_data_dir() / "workspaces" / "workspace-1"
    )


@pytest.mark.asyncio
async def test_sqlite_scheduler_fires_reminder_anchored_to_a_system_message() -> None:
    """A Reminder anchored to an earlier fire is accepted on input and fires."""

    database = SqliteDatabase()
    await database.start(timeout=2)
    wheel = TimerWheel()
    await wheel.start()
    try:
        storage = cast(IStorage, database)
        scope = database.scope("agent-1", "Test Agent")
        channel_session = ChannelSession(
            id="channel-1",
            channel="telegram",
            provider_thread_id="thread-1",
            created_at_ms=1,
            updated_at_ms=1,
        )
        bcn_session = Thread(
            id="bcn-1",
            channel_session_id=channel_session.id,
            workspace_id="agent-1",
            created_at_ms=1,
            updated_at_ms=1,
        )
        await scope.save_channel_session(channel_session)
        await scope.save_thread(bcn_session)

        anchor = await scope.save_message(
            Message(
                direction=MessageDirection.INBOUND,
                seq=0,
                message_id="018f0000-0000-7000-8000-0000000000a1",
                thread_id=bcn_session.id,
                channel_session_id=channel_session.id,
                channel=channel_session.channel,
                provider_thread_id=channel_session.provider_thread_id,
                provider_message_id="provider-message-1",
                received_at_ms=1_000,
                sender=SenderIdentity(id="user-1", name="Test User"),
                target="dm:channel-1",
                body="remember this",
                metadata={"sender_kind": SenderKind.HUMAN.value},
            )
        )
        # A previous Reminder fire: inbound, but with no provider message behind it.
        system_anchor = await scope.save_message(
            Message(
                direction=MessageDirection.INBOUND,
                seq=0,
                message_id="018f0000-0000-7000-8000-0000000000a2",
                thread_id=bcn_session.id,
                channel_session_id=channel_session.id,
                channel=channel_session.channel,
                provider_thread_id=channel_session.provider_thread_id,
                provider_message_id=None,
                provider_time_ms=None,
                received_at_ms=1_500,
                sender=SenderIdentity(id="system", name="system"),
                target=anchor.target,
                target_kind=anchor.target_kind,
                body="reminder fired",
                metadata={
                    "sender_kind": SenderKind.SYSTEM.value,
                    "system_message_kind": SystemMessageKind.REMINDER.value,
                },
            )
        )

        def scheduled(anchor_message_id: str, title: str) -> Reminder:
            return Reminder(
                reminder_id="018f0000-0000-7000-8000-0000000000b1",
                owner_thread_id=bcn_session.id,
                anchor_message_id=anchor_message_id,
                title=title,
                state=ReminderState.SCHEDULED,
                next_fire_at_ms=2_000,
                repeat_rule=None,
                timezone="UTC",
                revision=1,
                last_occurrence_no=0,
                created_at_ms=1_000,
                updated_at_ms=1_000,
            )

        on_system_anchor = await scope.save_new_reminder(
            scheduled(system_anchor.message_id, "Anchored to a system message")
        )
        on_provider_anchor = await scope.save_new_reminder(
            scheduled(anchor.message_id, "Anchored to a provider message")
        )

        published: list[str] = []

        async def publish(agent_id: str, message: Message) -> bool:
            del agent_id
            published.append(message.body)
            return True

        scheduler = ReminderScheduler(
            storage=storage,
            timer_wheel=wheel,
            concurrency=ThreadLockRegistry(),
            publish_wake=publish,
            clock=lambda: 3_000,
        )
        await scheduler.start(timeout=2)
        await scheduler.stop(timeout=2)

        from_system = await scope.get_reminder(
            bcn_session.id, on_system_anchor.reminder_id
        )
        from_provider = await scope.get_reminder(
            bcn_session.id, on_provider_anchor.reminder_id
        )
        assert from_system is not None
        assert from_provider is not None
        assert from_system.state is ReminderState.FIRED
        assert from_provider.state is ReminderState.FIRED
        assert any("Anchored to a system message" in body for body in published)
        assert any("Anchored to a provider message" in body for body in published)
    finally:
        await wheel.close()
        await database.stop(timeout=2)


@pytest.mark.asyncio
async def test_sqlite_scheduler_survives_a_failed_cycle() -> None:
    """A storage failure inside the scheduler loop retries instead of stopping it."""

    database = SqliteDatabase()
    await database.start(timeout=2)
    wheel = TimerWheel()
    await wheel.start()
    scheduler = ReminderScheduler(
        storage=cast(IStorage, database),
        timer_wheel=wheel,
        concurrency=ThreadLockRegistry(),
        publish_wake=_never_published,
        clock=lambda: 1_000,
    )
    try:
        await scheduler.start(timeout=2)
        await database.stop(timeout=2)
        scheduler.poke()

        async def until_cycle_failed() -> None:
            while scheduler._task is not None and not scheduler._task.done():
                await asyncio.sleep(0)
                return

        await asyncio.wait_for(until_cycle_failed(), timeout=2)
        assert scheduler._task is not None
        assert scheduler._task.done() is False
    finally:
        await scheduler.stop(timeout=2)
        await wheel.close()


async def _never_published(agent_id: str, message: Message) -> bool:
    del agent_id, message
    return True
