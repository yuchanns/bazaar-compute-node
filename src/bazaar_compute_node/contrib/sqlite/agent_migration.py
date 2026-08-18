from __future__ import annotations

from . import migrations as _migrations
from .migrations import Migration

AGENT_OWNERSHIP_MIGRATION = Migration(
    version=13,
    name="add_agent_ownership",
    statements=(
        """
        CREATE TEMP TABLE _agent_ownership_guard (
            ok INTEGER NOT NULL CHECK (ok = 1)
        )
        """,
        """
        INSERT INTO _agent_ownership_guard (ok)
        SELECT CASE
            WHEN (
                EXISTS (SELECT 1 FROM channel_sessions LIMIT 1)
                OR EXISTS (SELECT 1 FROM bcn_sessions LIMIT 1)
                OR EXISTS (SELECT 1 FROM inbound_messages LIMIT 1)
                OR EXISTS (SELECT 1 FROM outbound_messages LIMIT 1)
                OR EXISTS (SELECT 1 FROM runtime_attempts LIMIT 1)
                OR EXISTS (SELECT 1 FROM reminders LIMIT 1)
                OR EXISTS (SELECT 1 FROM reminder_occurrences LIMIT 1)
            )
            AND NOT EXISTS (
                SELECT 1
                FROM node_state
                WHERE singleton_key = 1
                  AND typeof(workspace_id) = 'text'
                  AND length(workspace_id) > 0
            )
            THEN 0
            ELSE 1
        END
        """,
        "ALTER TABLE channel_sessions ADD COLUMN agent_id TEXT",
        "ALTER TABLE bcn_sessions ADD COLUMN agent_id TEXT",
        "ALTER TABLE inbound_messages ADD COLUMN agent_id TEXT",
        "ALTER TABLE outbound_messages ADD COLUMN agent_id TEXT",
        "ALTER TABLE outbound_messages ADD COLUMN agent_name TEXT",
        "ALTER TABLE runtime_attempts ADD COLUMN agent_id TEXT",
        "ALTER TABLE reminders ADD COLUMN agent_id TEXT",
        "ALTER TABLE reminder_occurrences ADD COLUMN agent_id TEXT",
        """
        UPDATE channel_sessions
        SET agent_id = (
            SELECT workspace_id FROM node_state WHERE singleton_key = 1
        )
        """,
        """
        UPDATE bcn_sessions
        SET agent_id = (
            SELECT workspace_id FROM node_state WHERE singleton_key = 1
        )
        """,
        """
        UPDATE inbound_messages
        SET agent_id = (
            SELECT workspace_id FROM node_state WHERE singleton_key = 1
        )
        """,
        """
        UPDATE outbound_messages
        SET agent_id = (
                SELECT workspace_id FROM node_state WHERE singleton_key = 1
            ),
            agent_name = 'default'
        """,
        """
        UPDATE runtime_attempts
        SET agent_id = (
            SELECT workspace_id FROM node_state WHERE singleton_key = 1
        )
        """,
        """
        UPDATE reminders
        SET agent_id = (
            SELECT workspace_id FROM node_state WHERE singleton_key = 1
        )
        """,
        """
        UPDATE reminder_occurrences
        SET agent_id = (
            SELECT workspace_id FROM node_state WHERE singleton_key = 1
        )
        """,
        "DROP INDEX idx_channel_sessions_provider_identity",
        """
        CREATE INDEX idx_channel_sessions_provider_identity
            ON channel_sessions (agent_id, channel, provider_thread_id)
        """,
        "DROP INDEX idx_bcn_sessions_channel",
        """
        CREATE INDEX idx_bcn_sessions_channel
            ON bcn_sessions (agent_id, channel_session_id)
        """,
        "DROP INDEX idx_inbound_provider_identity",
        """
        CREATE UNIQUE INDEX idx_inbound_provider_identity
            ON inbound_messages (
                agent_id,
                channel,
                provider_thread_id,
                provider_message_id
            )
        """,
        "DROP INDEX idx_runtime_attempts_session_started",
        """
        CREATE INDEX idx_runtime_attempts_session_started
            ON runtime_attempts (agent_id, session_id, started_at_ms)
        """,
        "DROP INDEX idx_reminders_owner_state_updated",
        """
        CREATE INDEX idx_reminders_owner_state_updated
            ON reminders (
                agent_id,
                owner_session_id,
                state,
                updated_at_ms,
                reminder_id
            )
        """,
        "DROP INDEX idx_reminder_occurrences_owner_read_fired",
        """
        CREATE INDEX idx_reminder_occurrences_owner_read_fired
            ON reminder_occurrences (
                agent_id,
                owner_session_id,
                read_at_ms,
                fired_at_ms,
                occurrence_id
            )
        """,
        """
        CREATE TRIGGER set_channel_sessions_agent_id
        AFTER INSERT ON channel_sessions
        WHEN NEW.agent_id IS NULL
        BEGIN
            UPDATE channel_sessions
            SET agent_id = bcn_agent_id()
            WHERE id = NEW.id;
        END
        """,
        """
        CREATE TRIGGER set_bcn_sessions_agent_id
        AFTER INSERT ON bcn_sessions
        WHEN NEW.agent_id IS NULL
        BEGIN
            UPDATE bcn_sessions
            SET agent_id = bcn_agent_id()
            WHERE id = NEW.id;
        END
        """,
        """
        CREATE TRIGGER set_inbound_messages_agent_id
        AFTER INSERT ON inbound_messages
        WHEN NEW.agent_id IS NULL
        BEGIN
            UPDATE inbound_messages
            SET agent_id = bcn_agent_id()
            WHERE message_id = NEW.message_id;
        END
        """,
        """
        CREATE TRIGGER set_outbound_messages_agent_identity
        AFTER INSERT ON outbound_messages
        WHEN NEW.agent_id IS NULL OR NEW.agent_name IS NULL
        BEGIN
            UPDATE outbound_messages
            SET agent_id = COALESCE(NEW.agent_id, bcn_agent_id()),
                agent_name = COALESCE(NEW.agent_name, bcn_agent_name())
            WHERE outbound_message_id = NEW.outbound_message_id;
        END
        """,
        """
        CREATE TRIGGER set_runtime_attempts_agent_id
        AFTER INSERT ON runtime_attempts
        WHEN NEW.agent_id IS NULL
        BEGIN
            UPDATE runtime_attempts
            SET agent_id = bcn_agent_id()
            WHERE turn_id = NEW.turn_id;
        END
        """,
        """
        CREATE TRIGGER set_reminders_agent_id
        AFTER INSERT ON reminders
        WHEN NEW.agent_id IS NULL
        BEGIN
            UPDATE reminders
            SET agent_id = bcn_agent_id()
            WHERE reminder_id = NEW.reminder_id;
        END
        """,
        """
        CREATE TRIGGER set_reminder_occurrences_agent_id
        AFTER INSERT ON reminder_occurrences
        WHEN NEW.agent_id IS NULL
        BEGIN
            UPDATE reminder_occurrences
            SET agent_id = bcn_agent_id()
            WHERE occurrence_id = NEW.occurrence_id;
        END
        """,
        "DROP TABLE node_state",
        "DROP TABLE _agent_ownership_guard",
    ),
)


def install_agent_ownership_migration() -> None:
    versions = {migration.version: migration for migration in _migrations.MIGRATIONS}
    existing = versions.get(AGENT_OWNERSHIP_MIGRATION.version)
    if existing is not None:
        if (
            existing.name != AGENT_OWNERSHIP_MIGRATION.name
            or existing.checksum != AGENT_OWNERSHIP_MIGRATION.checksum
        ):
            raise RuntimeError(
                "migration version 13 is already bound to different content"
            )
        return
    if _migrations.MIGRATIONS[-1].version >= AGENT_OWNERSHIP_MIGRATION.version:
        raise RuntimeError("agent ownership migration must extend the migration ledger")
    _migrations.MIGRATIONS = (*_migrations.MIGRATIONS, AGENT_OWNERSHIP_MIGRATION)


__all__ = ["AGENT_OWNERSHIP_MIGRATION", "install_agent_ownership_migration"]
