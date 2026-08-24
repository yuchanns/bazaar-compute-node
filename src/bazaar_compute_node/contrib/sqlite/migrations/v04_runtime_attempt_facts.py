from __future__ import annotations

from .model import Migration

RUNTIME_ATTEMPT_FACT_MIGRATION = Migration(
    version=4,
    name="runtime_attempt_facts",
    statements=(
        """
        CREATE TABLE runtime_attempts (
            turn_id TEXT PRIMARY KEY,
            session_id TEXT,
            client_user_message_id TEXT,
            started_at_ms INTEGER
        )
        """,
        """
        INSERT INTO runtime_attempts (
            turn_id,
            session_id,
            client_user_message_id,
            started_at_ms
        )
        SELECT
            turn_id,
            session_id,
            client_user_message_id,
            started_at_ms
        FROM runtime_turns
        WHERE client_user_message_id IS NOT NULL
        """,
        """
        DROP INDEX idx_runtime_turns_session_state
        """,
        """
        DROP TABLE runtime_turns
        """,
        """
        CREATE INDEX idx_runtime_attempts_session_started
            ON runtime_attempts (session_id, started_at_ms)
        """,
    ),
)


__all__ = ["RUNTIME_ATTEMPT_FACT_MIGRATION"]
