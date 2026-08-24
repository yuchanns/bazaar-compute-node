from __future__ import annotations

from .model import Migration

OUTBOUND_ATTACHMENTS_MIGRATION = Migration(
    version=10,
    name="add_outbound_attachments",
    statements=(
        """
        ALTER TABLE outbound_messages
        ADD COLUMN attachments_json TEXT NOT NULL DEFAULT '[]'
        """,
    ),
)


__all__ = ["OUTBOUND_ATTACHMENTS_MIGRATION"]
