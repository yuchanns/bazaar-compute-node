from __future__ import annotations

from .model import Migration

# Whether whoever is behind a conversation may talk to the agent. A session
# from before this column was let in by the rules of its day, so it stands
# approved; one opened from now on says so itself, pending unless a list or
# a reviewer says otherwise. Alongside, a table for what an agent is set to
# do, by key, starting with what it says to a conversation still pending.
CHANNEL_SESSION_REVIEW_MIGRATION = Migration(
    version=31,
    name="channel_session_review",
    statements=(
        """
        ALTER TABLE channel_sessions ADD COLUMN review TEXT NOT NULL DEFAULT 'pending'
            CHECK (review IN ('pending', 'approved', 'denied'))
        """,
        "UPDATE channel_sessions SET review = 'approved'",
        """
        CREATE TABLE settings (
            agent_id TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            updated_at_ms INTEGER NOT NULL,
            PRIMARY KEY (agent_id, key)
        )
        """,
    ),
)
