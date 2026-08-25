from __future__ import annotations

from .model import Migration

READABLE_TARGET_MIGRATION = Migration(
    version=22,
    name="readable_targets",
    statements=(
        "ALTER TABLE channel_sessions ADD COLUMN target_display_name TEXT",
        "ALTER TABLE channel_sessions ADD COLUMN target_handle TEXT",
        "ALTER TABLE channel_sessions ADD COLUMN target_handle_key TEXT",
        """
        CREATE INDEX idx_channel_sessions_target_handle
            ON channel_sessions (agent_id, target_kind, target_handle_key)
            WHERE target_handle_key IS NOT NULL
        """,
    ),
)

__all__ = ["READABLE_TARGET_MIGRATION"]
