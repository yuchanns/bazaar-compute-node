from __future__ import annotations

from . import migrations as _migrations
from .migrations import Migration

INBOX_DISCOVERY_MIGRATION = Migration(
    version=14,
    name="add_inbox_discovery_indexes",
    statements=(
        """
        CREATE INDEX idx_inbound_agent_session_seq
            ON inbound_messages (agent_id, session_id, seq DESC)
        """,
        """
        CREATE INDEX idx_inbound_agent_target_session
            ON inbound_messages (agent_id, canonical_target, session_id)
        """,
    ),
)


def install_inbox_discovery_migration() -> None:
    versions = {migration.version: migration for migration in _migrations.MIGRATIONS}
    existing = versions.get(INBOX_DISCOVERY_MIGRATION.version)
    if existing is not None:
        if (
            existing.name != INBOX_DISCOVERY_MIGRATION.name
            or existing.checksum != INBOX_DISCOVERY_MIGRATION.checksum
        ):
            raise RuntimeError(
                "migration version 14 is already bound to different content"
            )
        return
    if _migrations.MIGRATIONS[-1].version >= INBOX_DISCOVERY_MIGRATION.version:
        raise RuntimeError("inbox discovery migration must extend the migration ledger")
    _migrations.MIGRATIONS = (
        *_migrations.MIGRATIONS,
        INBOX_DISCOVERY_MIGRATION,
    )


__all__ = [
    "INBOX_DISCOVERY_MIGRATION",
    "install_inbox_discovery_migration",
]
