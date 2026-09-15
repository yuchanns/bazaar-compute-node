from __future__ import annotations

from .model import Migration

# Which bot a conversation lives on. Rows written before an agent could hold
# several channels leave it empty; startup fills them in.
CHANNEL_SESSION_IDENTITY_MIGRATION = Migration(
    version=28,
    name="channel_session_identity",
    statements=("ALTER TABLE channel_sessions ADD COLUMN channel_identity TEXT",),
)

__all__ = ["CHANNEL_SESSION_IDENTITY_MIGRATION"]
