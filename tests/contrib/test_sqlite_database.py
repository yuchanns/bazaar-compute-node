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
from bazaar_compute_node.core.paths import resolve_data_dir, resolve_workspace_dir


@pytest.mark.asyncio
async def test_sqlite_bootstrap_persists_node_and_workspace_state() -> None:
    data_dir = resolve_data_dir()
    database = SqliteDatabase()

    await database.start(timeout=2)
    try:
        identity = await database.initialize(node_id="node-1")
        first_state = database.node_state
        assert first_state.node_id == "node-1"
        assert first_state.schema_version == 7
        assert identity.workspace_id == first_state.workspace_id
        assert not (data_dir / "workspaces" / first_state.workspace_id).exists()

        async with database.transaction() as transaction:
            tables = await transaction.fetchall(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            )
            indexes = await transaction.fetchall(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'index' AND name LIKE 'idx_%' ORDER BY name"
            )
            inbound_columns = await transaction.fetchall(
                "PRAGMA table_info(inbound_messages)"
            )
            outbound_columns = await transaction.fetchall(
                "PRAGMA table_info(outbound_messages)"
            )
            journal_mode = await transaction.fetchone("PRAGMA journal_mode")
            busy_timeout = await transaction.fetchone("PRAGMA busy_timeout")
            provider_identity_columns = await transaction.fetchall(
                "PRAGMA index_info(idx_inbound_provider_identity)"
            )
            inbound_indexes = await transaction.fetchall(
                "PRAGMA index_list(inbound_messages)"
            )

        assert {row["name"] for row in tables} == {
            "bcn_sessions",
            "channel_sessions",
            "consumer_cursors",
            "inbound_attachments",
            "inbound_messages",
            "node_state",
            "outbound_messages",
            "runtime_events",
            "runtime_attempts",
            "runtime_sessions",
            "schema_migrations",
        }
        assert {row["name"] for row in indexes} == {
            "idx_inbound_channel_received",
            "idx_inbound_provider_identity",
            "idx_inbound_reply_to_message",
            "idx_inbound_seq",
            "idx_inbound_session_seq",
            "idx_inbound_session_target_seq",
            "idx_outbound_session_created",
            "idx_outbound_state_created",
            "idx_bcn_sessions_channel",
            "idx_channel_sessions_provider_identity",
            "idx_runtime_sessions_bcn",
            "idx_runtime_events_created",
            "idx_runtime_events_name_seq",
            "idx_runtime_events_session_seq",
            "idx_runtime_attempts_session_started",
        }
        inbound_primary_keys = {row["name"]: row["pk"] for row in inbound_columns}
        outbound_primary_keys = {row["name"]: row["pk"] for row in outbound_columns}
        assert inbound_primary_keys["message_id"] == 1
        assert inbound_primary_keys["seq"] == 0
        assert "reply_to_message_id" in inbound_primary_keys
        assert "reply_to_provider_message_id" not in inbound_primary_keys
        assert outbound_primary_keys["outbound_message_id"] == 1
        assert [row["name"] for row in provider_identity_columns] == [
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
        restarted_identity = await restarted.initialize(node_id="node-1")
        assert restarted_identity.workspace_id == first_state.workspace_id
        assert restarted.workspace_id == first_state.workspace_id
        assert restarted.node_state.created_at_ms == first_state.created_at_ms
    finally:
        await restarted.stop(timeout=2)


@pytest.mark.asyncio
async def test_sqlite_transaction_rolls_back_and_commits_ddl() -> None:
    database = SqliteDatabase()
    await database.start(timeout=2)
    try:
        with pytest.raises(RuntimeError, match="rollback"):
            async with database.transaction() as transaction:
                await transaction.execute("CREATE TABLE rollback_probe (value TEXT)")
                raise RuntimeError("rollback")

        async with database.transaction() as transaction:
            rollback_probe = await transaction.fetchone(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name = 'rollback_probe'"
            )
        assert rollback_probe is None

        async with database.transaction() as transaction:
            await transaction.execute("CREATE TABLE commit_probe (value TEXT)")

        async with database.transaction() as transaction:
            commit_probe = await transaction.fetchone(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name = 'commit_probe'"
            )
        assert commit_probe is not None
        assert commit_probe["name"] == "commit_probe"
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
        async with database.transaction() as transaction:
            migration_rows = await transaction.fetchall(
                "SELECT version, migration_name, checksum "
                "FROM schema_migrations ORDER BY version"
            )
            mapping_indexes = await transaction.fetchall(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'index' AND name IN (?, ?, ?, ?, ?, ?) ORDER BY name",
                (
                    "idx_bcn_sessions_channel",
                    "idx_channel_sessions_provider_identity",
                    "idx_runtime_sessions_bcn",
                    "idx_inbound_provider_identity",
                    "idx_inbound_session_target_seq",
                    "idx_inbound_reply_to_message",
                ),
            )
        assert [row["version"] for row in migration_rows] == [1, 2, 3, 4, 5, 6, 7]
        assert migration_rows[1]["migration_name"] == MIGRATIONS[1].name
        assert migration_rows[1]["checksum"] == MIGRATIONS[1].checksum
        assert migration_rows[2]["migration_name"] == MIGRATIONS[2].name
        assert migration_rows[2]["checksum"] == MIGRATIONS[2].checksum
        assert migration_rows[3]["migration_name"] == MIGRATIONS[3].name
        assert migration_rows[3]["checksum"] == MIGRATIONS[3].checksum
        assert migration_rows[4]["migration_name"] == MIGRATIONS[4].name
        assert migration_rows[4]["checksum"] == MIGRATIONS[4].checksum
        assert migration_rows[5]["migration_name"] == MIGRATIONS[5].name
        assert migration_rows[5]["checksum"] == MIGRATIONS[5].checksum
        assert migration_rows[6]["migration_name"] == MIGRATIONS[6].name
        assert migration_rows[6]["checksum"] == MIGRATIONS[6].checksum
        assert {row["name"] for row in mapping_indexes} == {
            "idx_bcn_sessions_channel",
            "idx_channel_sessions_provider_identity",
            "idx_runtime_sessions_bcn",
            "idx_inbound_provider_identity",
            "idx_inbound_session_target_seq",
            "idx_inbound_reply_to_message",
        }
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
        async with database.transaction() as transaction:
            rows = await transaction.fetchall(
                "SELECT message_id, reply_to_message_id "
                "FROM inbound_messages ORDER BY seq"
            )
            quick_check = await transaction.fetchone("PRAGMA quick_check")
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


def test_resolve_data_dir_uses_the_home_bcn_root() -> None:
    assert resolve_data_dir() == (Path.home() / ".bcn").resolve()


def test_default_workspace_uses_the_home_bcn_root() -> None:
    assert (
        resolve_workspace_dir("workspace-1")
        == (Path.home() / ".bcn" / "workspaces" / "workspace-1").resolve()
    )
