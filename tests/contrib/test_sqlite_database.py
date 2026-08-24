from __future__ import annotations

import os
from pathlib import Path
from stat import S_IMODE

import aiosqlite
import pytest

from bazaar_compute_node.contrib.sqlite import (
    MigrationChecksumError,
    SqliteDatabase,
)
from bazaar_compute_node.contrib.sqlite.migrations import (
    MIGRATIONS,
    SCHEMA_MIGRATION,
)
from bazaar_compute_node.core.models import (
    BcnSession,
    ChannelSession,
    Message,
    MessageDirection,
    OutboundDeliveryState,
    RuntimeAttempt,
    SenderIdentity,
    SenderKind,
)
from bazaar_compute_node.core.paths import resolve_data_dir, resolve_workspace_dir


@pytest.mark.asyncio
async def test_sqlite_persists_provider_attempt_lifecycle() -> None:
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
        bcn_session = BcnSession(
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
            session_id=bcn_session.id,
            channel_session_id=channel_session.id,
            target="dm:channel-1",
            body="hello",
            delivery_state=OutboundDeliveryState.PENDING,
            created_at_ms=2,
            snapshot_seq=3,
            current_inbound_seq=3,
            provider_attempted_at_ms=4,
        )

        await scope.save_channel_session(channel_session)
        await scope.save_bcn_session(bcn_session)
        pending = await scope.save_outbound_message(pending)
        sent = pending.transition_to(
            OutboundDeliveryState.SENT,
            at_ms=5,
            provider_message_id="provider-message-1",
        )
        await scope.save_outbound_message(sent)
        persisted = await scope.get_outbound_message(pending.message_id)

        assert persisted == sent
    finally:
        await database.stop(timeout=2)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sender_kind", (SenderKind.HUMAN, SenderKind.AGENT, SenderKind.UNKNOWN)
)
async def test_sqlite_persists_sender_display_name_and_kind(
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
        bcn_session = BcnSession(
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
            session_id=bcn_session.id,
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
        await scope.save_bcn_session(bcn_session)
        live = await scope.append_inbound_message(message)
        persisted = await scope.find_inbound_message(*message.inbound_identity())

        assert live.sender == SenderIdentity(id="test-user-id", name="test-user")
        assert live.sender_kind is sender_kind
        assert persisted is not None
        assert persisted.sender == SenderIdentity(name="test-user")
        assert persisted.sender_kind is sender_kind
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
            inbound_columns = await session.fetchall(
                "PRAGMA table_info(inbound_messages)"
            )
            outbound_columns = await session.fetchall(
                "PRAGMA table_info(outbound_messages)"
            )
            handoff_columns = await session.fetchall("PRAGMA table_info(handoffs)")
            journal_mode = await session.fetchone("PRAGMA journal_mode")
            busy_timeout = await session.fetchone("PRAGMA busy_timeout")
            provider_identity_columns = await session.fetchall(
                "PRAGMA index_info(idx_inbound_provider_identity)"
            )
            inbound_indexes = await session.fetchall(
                "PRAGMA index_list(inbound_messages)"
            )
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
            "bcn_sessions",
            "channel_sessions",
            "consumer_cursors",
            "handoffs",
            "inbound_attachments",
            "inbound_messages",
            "outbound_messages",
            "reminder_occurrences",
            "reminders",
            "runtime_attempts",
            "schema_migrations",
        } <= {row["name"] for row in tables}
        assert {
            "idx_inbound_channel_received",
            "idx_inbound_provider_identity",
            "idx_inbound_reply_to_message",
            "idx_inbound_seq",
            "idx_inbound_agent_session_seq",
            "idx_inbound_agent_target_session",
            "idx_inbound_session_seq",
            "idx_inbound_session_target_seq",
            "idx_handoffs_agent_target_read_seq",
            "idx_outbound_session_created",
            "idx_outbound_state_created",
            "idx_bcn_sessions_channel",
            "idx_channel_sessions_provider_identity",
            "idx_runtime_attempts_session_started",
            "idx_reminders_state_next",
            "idx_reminders_owner_state_updated",
            "idx_reminder_occurrences_owner_read_fired",
            "idx_reminder_occurrences_reminder_number",
        } <= {row["name"] for row in indexes}
        assert "compaction_completed_at_ms" in {
            row["name"] for row in migration_columns
        }
        assert schema_version is not None
        assert schema_version["version"] == 17
        assert compaction_row is not None
        assert compaction_row["compaction_completed_at_ms"] is not None
        inbound_primary_keys = {row["name"]: row["pk"] for row in inbound_columns}
        outbound_primary_keys = {row["name"]: row["pk"] for row in outbound_columns}
        assert inbound_primary_keys["message_id"] == 1
        assert inbound_primary_keys["seq"] == 0
        assert "reply_to_message_id" in inbound_primary_keys
        assert "reply_to_provider_message_id" not in inbound_primary_keys
        assert "agent_id" in inbound_primary_keys
        assert outbound_primary_keys["outbound_message_id"] == 1
        assert "attachments_json" in outbound_primary_keys
        assert "agent_id" in outbound_primary_keys
        assert "agent_name" in outbound_primary_keys
        assert "fresh_check_state" not in outbound_primary_keys
        assert "draft_saved_at_ms" not in outbound_primary_keys
        assert "next_action" not in outbound_primary_keys
        assert {row["name"] for row in handoff_columns} == {
            "seq",
            "handoff_id",
            "command_id",
            "agent_id",
            "source_session_id",
            "target_session_id",
            "source_message_id",
            "body",
            "created_at_ms",
            "read_at_ms",
        }
        assert [row["name"] for row in provider_identity_columns] == [
            "agent_id",
            "channel",
            "provider_thread_id",
            "provider_message_id",
        ]
        provider_identity_index = next(
            row
            for row in inbound_indexes
            if row["name"] == "idx_inbound_provider_identity"
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
                "WHERE type = 'index' AND name IN (?, ?, ?, ?, ?, ?, ?) ORDER BY name",
                (
                    "idx_bcn_sessions_channel",
                    "idx_channel_sessions_provider_identity",
                    "idx_inbound_provider_identity",
                    "idx_inbound_session_target_seq",
                    "idx_inbound_reply_to_message",
                    "idx_inbound_agent_session_seq",
                    "idx_inbound_agent_target_session",
                ),
            )
        assert [row["version"] for row in migration_rows] == [
            migration.version for migration in MIGRATIONS
        ]
        for row, migration in zip(migration_rows, MIGRATIONS, strict=True):
            assert row["migration_name"] == migration.name
            assert row["checksum"] == migration.checksum
        assert {row["name"] for row in session_indexes} == {
            "idx_bcn_sessions_channel",
            "idx_channel_sessions_provider_identity",
            "idx_inbound_provider_identity",
            "idx_inbound_session_target_seq",
            "idx_inbound_reply_to_message",
            "idx_inbound_agent_session_seq",
            "idx_inbound_agent_target_session",
        }
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
                "SELECT agent_id FROM bcn_sessions WHERE id = 'bcn-1' "
                "UNION ALL "
                "SELECT agent_id FROM runtime_attempts WHERE turn_id = 'turn-1'"
            )
        assert schema_version is not None
        assert schema_version["version"] == 17
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
        assert await scope.get_bcn_session("bcn-1") == BcnSession(
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
        assert schema_version["version"] == 17
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
                    4,
                    4,
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
                "SELECT outbound_message_id, state, snapshot_seq, "
                "current_inbound_seq, provider_attempted_at_ms, completed_at_ms, "
                "attachments_json, agent_id, agent_name "
                "FROM outbound_messages ORDER BY outbound_message_id"
            )
            columns = await session.fetchall("PRAGMA table_info(outbound_messages)")
            indexes = await session.fetchall("PRAGMA index_list(outbound_messages)")
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
                "outbound_message_id": "outbound-sent",
                "state": "sent",
                "snapshot_seq": 4,
                "current_inbound_seq": 4,
                "provider_attempted_at_ms": 5,
                "completed_at_ms": 6,
                "attachments_json": "[]",
                "agent_id": "agent-1",
                "agent_name": "Test Agent",
            }
        ]
        assert {row["name"] for row in columns}.isdisjoint(
            {"fresh_check_state", "draft_saved_at_ms", "next_action"}
        )
        assert {row["name"] for row in indexes} >= {
            "idx_outbound_session_created",
            "idx_outbound_state_created",
        }
        assert triggers == []
    finally:
        await database.stop(timeout=2)


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
                "FROM inbound_messages ORDER BY seq"
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
