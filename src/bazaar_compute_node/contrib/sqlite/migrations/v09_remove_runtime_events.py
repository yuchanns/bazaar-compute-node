from __future__ import annotations

from .model import Migration

RUNTIME_EVENTS_REMOVAL_MIGRATION = Migration(
    version=9,
    name="remove_runtime_events",
    statements=(
        """
        ALTER TABLE schema_migrations
        ADD COLUMN compaction_completed_at_ms INTEGER
        """,
        """
        DROP TABLE runtime_events
        """,
    ),
)


__all__ = ["RUNTIME_EVENTS_REMOVAL_MIGRATION"]
