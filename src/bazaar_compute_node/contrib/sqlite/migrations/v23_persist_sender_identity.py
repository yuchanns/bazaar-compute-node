from __future__ import annotations

from .model import Migration

SENDER_IDENTITY_MIGRATION = Migration(
    version=23,
    name="persist_sender_identity",
    statements=("ALTER TABLE messages ADD COLUMN sender_id TEXT",),
)

__all__ = ["SENDER_IDENTITY_MIGRATION"]
