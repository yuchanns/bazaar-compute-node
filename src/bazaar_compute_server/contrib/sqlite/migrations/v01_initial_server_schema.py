from __future__ import annotations

from .model import Migration

SCHEMA_MIGRATION = Migration(
    version=1,
    name="initial_server_schema",
    statements=(
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            migration_name TEXT NOT NULL,
            checksum TEXT NOT NULL,
            applied_at_ms INTEGER NOT NULL,
            duration_ms INTEGER NOT NULL
        )
        """,
        """
        CREATE TABLE accounts (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at_ms INTEGER NOT NULL
        )
        """,
        """
        CREATE TABLE computers (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            secret_hash TEXT NOT NULL,
            created_at_ms INTEGER NOT NULL
        )
        """,
        """
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            computer_id TEXT NOT NULL REFERENCES computers (id),
            run_id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            agent_id TEXT,
            event_name TEXT NOT NULL,
            thread_id TEXT,
            created_at_ms INTEGER NOT NULL,
            received_at_ms INTEGER NOT NULL,
            payload TEXT NOT NULL,
            UNIQUE (computer_id, run_id, seq)
        )
        """,
        "CREATE INDEX events_by_agent ON events (computer_id, agent_id, created_at_ms)",
        (
            "CREATE INDEX events_by_thread"
            " ON events (computer_id, agent_id, thread_id, created_at_ms)"
        ),
    ),
)

__all__ = ["SCHEMA_MIGRATION"]
