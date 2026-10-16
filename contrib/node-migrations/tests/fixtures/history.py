"""Build authentic historical databases and their original-runner upgrade result."""

from __future__ import annotations

import asyncio
import importlib
import sys

import aiosqlite


async def seed(connection: aiosqlite.Connection, version: int) -> None:
    async def insert(table: str, row: dict[str, object]) -> None:
        fields = await connection.execute_fetchall(f'PRAGMA table_info("{table}")')
        if not fields:
            return
        names = {field[1] for field in fields}
        row = {name: value for name, value in row.items() if name in names}
        columns = ", ".join('"' + name + '"' for name in row)
        placeholders = ", ".join("?" for _ in row)
        await connection.execute(
            f'INSERT INTO "{table}" ({columns}) VALUES ({placeholders})',
            tuple(row.values()),
        )

    await insert(
        "node_state",
        {
            "singleton_key": 1,
            "node_id": "computer",
            "schema_version": version,
            "workspace_id": "agent",
        },
    )
    await insert(
        "channel_sessions",
        {
            "id": "unrouted",
            "channel": "telegram",
            "target_kind": "dm",
            "target_handle": "Alice",
            "target_handle_key": "alice",
            "agent_id": "agent",
        },
    )
    for identity, provider in (
        ("opened", "telegram:bot:@Alice:dm"),
        ("peer", "telegram:bot:alice-id:dm"),
    ):
        await insert(
            "channel_sessions",
            {
                "id": identity,
                "channel": "telegram",
                "provider_thread_id": provider,
                "target_kind": "dm",
                "following": 1,
                "agent_id": "agent",
                "created_at_ms": 1,
                "updated_at_ms": 1,
                "target_handle": "Alice" if identity == "opened" else None,
                "target_handle_key": "alice" if identity == "opened" else None,
            },
        )
        await insert(
            "threads" if version >= 25 else "bcn_sessions",
            {
                "id": identity + "-thread",
                "channel_session_id": identity,
                "workspace_id": "agent",
                "agent_id": "agent",
                "created_at_ms": 1,
                "updated_at_ms": 1,
            },
        )
    for identity, sequence, moment, channel in (
        ("first", 10, 1000, "opened"),
        ("reply", 20, 3000, "opened"),
        ("peer-message", 30, 4000, "peer"),
        ("handoff", 40, 5000, "opened"),
    ):
        current = (
            sequence // 10 + (1 if sequence >= 20 else 0) if version >= 18 else sequence
        )
        row = {
            "message_id": identity,
            "seq": current,
            "session_id": channel + "-thread",
            "thread_id": channel + "-thread",
            "channel_session_id": channel,
            "channel": "telegram",
            "provider_thread_id": "telegram:bot:alice-id:dm"
            if channel == "peer"
            else "telegram:bot:@Alice:dm",
            "provider_message_id": "provider-" + identity,
            "provider_time_ms": moment,
            "received_at_ms": moment,
            "sender": "Alice",
            "sender_id": "alice-id",
            "sender_display_name": "Alice",
            "message_type": "text",
            "canonical_target": "dm:" + channel,
            "target": "dm:" + channel,
            "target_kind": "dm",
            "body": "handoff record"
            if identity == "handoff"
            else "Message " + identity,
            "mentions_agent": 0,
            "notifies_runtime": 1,
            "agent_id": "agent",
            "direction": "inbound",
            "metadata_json": '{"system_message_kind":"handoff"}'
            if identity == "handoff"
            else "{}",
        }
        if identity == "reply":
            row["reply_to_provider_message_id"] = "provider-first"
            row["reply_to_message_id"] = "first"
        await insert("messages" if version >= 18 else "inbound_messages", row)
    outbound = {
        "outbound_message_id": "outbound",
        "message_id": "outbound",
        "seq": 2,
        "command_id": "command",
        "session_id": "opened-thread",
        "thread_id": "opened-thread",
        "channel_session_id": "opened",
        "target": "dm:opened",
        "target_kind": "dm",
        "channel": "telegram",
        "provider_thread_id": "telegram:bot:@Alice:dm",
        "body": "Provider response",
        "state": "sent",
        "delivery_state": "sent",
        "snapshot_seq": 1 if version >= 18 else 10,
        "current_inbound_seq": 1 if version >= 18 else 10,
        "provider_message_id": "provider-outbound",
        "created_at_ms": 2000,
        "provider_attempted_at_ms": 2000,
        "completed_at_ms": 2001,
        "agent_id": "agent",
        "agent_name": "default",
        "sender": "default",
        "direction": "outbound",
        "message_type": "text",
        "attachments_json": "[]",
    }
    await insert("messages" if version >= 18 else "outbound_messages", outbound)
    for identity in ("opened", "peer"):
        await insert(
            "consumer_cursors",
            {
                "session_id": identity + "-thread",
                "thread_id": identity + "-thread",
                "delivered_through_seq": (1 if version >= 18 else 10)
                if identity == "opened"
                else 0,
                "inbox_snapshot_seq": (3 if version >= 18 else 20)
                if identity == "opened"
                else 0,
                "updated_at_ms": 1,
            },
        )
    await insert(
        "inbound_attachments",
        {
            "attachment_id": "attachment",
            "message_id": "first",
            "ordinal": 0,
            "name": "notes.txt",
            "kind": "file",
            "state": "ready",
            "media_type": "text/plain",
            "relative_path": "notes.txt",
            "size_bytes": 20,
        },
    )
    await insert(
        "runtime_attempts" if version >= 4 else "runtime_turns",
        {
            "turn_id": "turn",
            "session_id": "opened-thread",
            "client_user_message_id": "first",
            "started_at_ms": 1,
            "agent_id": "agent",
        },
    )
    await insert(
        "reminders",
        {
            "reminder_id": "reminder",
            "owner_session_id": "opened-thread",
            "owner_thread_id": "opened-thread",
            "anchor_message_id": "first",
            "title": 'Review "笔记"',
            "state": "scheduled",
            "next_fire_at_ms": 10000,
            "repeat_rule": "daily",
            "timezone": "UTC",
            "revision": 1,
            "last_occurrence_no": 1,
            "created_at_ms": 1,
            "updated_at_ms": 2,
            "agent_id": "agent",
        },
    )
    await insert(
        "reminder_occurrences",
        {
            "occurrence_id": "occurrence",
            "reminder_id": "reminder",
            "owner_session_id": "opened-thread",
            "anchor_message_id": "first",
            "occurrence_no": 1,
            "fired_at_ms": 6000,
            "next_fire_at_ms": 10000,
            "read_at_ms": None,
            "agent_id": "agent",
        },
    )
    await insert(
        "settings",
        {
            "agent_id": "agent",
            "key": "welcome",
            "value": "Keep this setting",
            "updated_at_ms": 1,
        },
    )


async def main() -> None:
    source, version, path, expected = sys.argv[1:]
    version = int(version)
    sys.path.insert(0, source)
    registry = importlib.import_module(
        "bazaar_compute_node.contrib.sqlite.migrations.registry"
    )
    migrations = registry.MIGRATIONS

    async def apply(connection: aiosqlite.Connection) -> None:
        executor = importlib.import_module(
            "bazaar_compute_node.contrib.sqlite.executor"
        )
        await connection.execute("BEGIN IMMEDIATE")
        try:
            await registry.apply_migrations(executor.SqliteSession(connection))
        except BaseException:
            await connection.rollback()
            raise
        await connection.commit()

    async with aiosqlite.connect(path, isolation_level=None) as connection:
        connection.row_factory = aiosqlite.Row
        await connection.create_function("bcn_agent_id", 0, lambda: "agent")
        await connection.create_function("bcn_agent_name", 0, lambda: "default")
        vars(registry)["MIGRATIONS"] = migrations[:version]
        await apply(connection)
        await seed(connection, version)
        async with aiosqlite.connect(expected, isolation_level=None) as target:
            await connection.backup(target)
    vars(registry)["MIGRATIONS"] = migrations
    async with aiosqlite.connect(expected, isolation_level=None) as connection:
        connection.row_factory = aiosqlite.Row
        await connection.create_function("bcn_agent_id", 0, lambda: "agent")
        await connection.create_function("bcn_agent_name", 0, lambda: "default")
        await apply(connection)


if __name__ == "__main__":
    asyncio.run(main())
