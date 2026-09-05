from __future__ import annotations

from .model import Migration

THREAD_RENAME_MIGRATION = Migration(
    version=25,
    name="rename_sessions_to_threads",
    statements=(
        "DROP INDEX idx_bcn_sessions_channel",
        "DROP INDEX idx_messages_agent_session_target_seq",
        "DROP INDEX idx_reminders_owner_state_updated",
        "ALTER TABLE bcn_sessions RENAME TO threads",
        "ALTER TABLE messages RENAME COLUMN session_id TO thread_id",
        "ALTER TABLE consumer_cursors RENAME COLUMN session_id TO thread_id",
        "ALTER TABLE reminders RENAME COLUMN owner_session_id TO owner_thread_id",
        """
        CREATE INDEX idx_threads_channel
            ON threads (agent_id, channel_session_id)
        """,
        """
        CREATE INDEX idx_messages_agent_thread_target_seq
            ON messages (agent_id, thread_id, target, seq)
        """,
        """
        CREATE INDEX idx_reminders_owner_state_updated
            ON reminders (
                agent_id,
                owner_thread_id,
                state,
                updated_at_ms,
                reminder_id
            )
        """,
    ),
)

__all__ = ["THREAD_RENAME_MIGRATION"]
