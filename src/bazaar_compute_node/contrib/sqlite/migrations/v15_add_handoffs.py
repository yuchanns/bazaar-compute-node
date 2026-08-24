from __future__ import annotations

from .model import Migration

HANDOFF_MIGRATION = Migration(
    version=15,
    name="add_handoffs",
    statements=(
        """
        CREATE TABLE handoffs (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            handoff_id TEXT NOT NULL UNIQUE,
            command_id TEXT NOT NULL UNIQUE,
            agent_id TEXT NOT NULL,
            source_session_id TEXT NOT NULL,
            target_session_id TEXT NOT NULL,
            source_message_id TEXT,
            body TEXT NOT NULL,
            created_at_ms INTEGER NOT NULL,
            read_at_ms INTEGER
        )
        """,
        """
        CREATE INDEX idx_handoffs_agent_target_read_seq
            ON handoffs (agent_id, target_session_id, read_at_ms, seq)
        """,
    ),
)
__all__ = ["HANDOFF_MIGRATION"]
