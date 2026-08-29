from __future__ import annotations

from .model import Migration

SENDER_DISPLAY_NAME_MIGRATION = Migration(
    version=24,
    name="persist_sender_display_name",
    statements=("ALTER TABLE messages ADD COLUMN sender_display_name TEXT",),
)

__all__ = ["SENDER_DISPLAY_NAME_MIGRATION"]
