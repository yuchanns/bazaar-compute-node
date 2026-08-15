from __future__ import annotations

from . import migrations as _migrations
from .migrations import Migration

REMINDER_MIGRATION = Migration(
    version=12,
    name="add_reminders",
    statements=(
        """
        CREATE TABLE reminders (
            reminder_id TEXT PRIMARY KEY,
            owner_session_id TEXT,
            anchor_message_id TEXT,
            title TEXT,
            state TEXT,
            next_fire_at_ms INTEGER,
            repeat_rule TEXT,
            timezone TEXT,
            revision INTEGER,
            last_occurrence_no INTEGER,
            created_at_ms INTEGER,
            updated_at_ms INTEGER,
            last_fired_at_ms INTEGER,
            canceled_at_ms INTEGER
        )
        """,
        """
        CREATE TABLE reminder_occurrences (
            occurrence_id TEXT PRIMARY KEY,
            reminder_id TEXT,
            owner_session_id TEXT,
            occurrence_no INTEGER,
            anchor_message_id TEXT,
            scheduled_for_ms INTEGER,
            fired_at_ms INTEGER,
            next_fire_at_ms INTEGER,
            overdue INTEGER,
            read_at_ms INTEGER,
            created_at_ms INTEGER
        )
        """,
        """
        CREATE INDEX idx_reminders_state_next
            ON reminders (state, next_fire_at_ms, reminder_id)
        """,
        """
        CREATE INDEX idx_reminders_owner_state_updated
            ON reminders (owner_session_id, state, updated_at_ms, reminder_id)
        """,
        """
        CREATE INDEX idx_reminder_occurrences_owner_read_fired
            ON reminder_occurrences (
                owner_session_id,
                read_at_ms,
                fired_at_ms,
                occurrence_id
            )
        """,
        """
        CREATE INDEX idx_reminder_occurrences_reminder_number
            ON reminder_occurrences (reminder_id, occurrence_no)
        """,
    ),
)


def install_reminder_migration() -> None:
    versions = {migration.version: migration for migration in _migrations.MIGRATIONS}
    existing = versions.get(REMINDER_MIGRATION.version)
    if existing is not None:
        if (
            existing.name != REMINDER_MIGRATION.name
            or existing.checksum != REMINDER_MIGRATION.checksum
        ):
            raise RuntimeError(
                "migration version 12 is already bound to different content"
            )
        return
    if _migrations.MIGRATIONS[-1].version >= REMINDER_MIGRATION.version:
        raise RuntimeError("reminder migration must extend the migration ledger")
    _migrations.MIGRATIONS = (*_migrations.MIGRATIONS, REMINDER_MIGRATION)


__all__ = ["REMINDER_MIGRATION", "install_reminder_migration"]
