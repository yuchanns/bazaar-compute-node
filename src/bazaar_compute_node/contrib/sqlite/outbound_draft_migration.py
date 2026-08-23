from __future__ import annotations

from .migration import Migration

OUTBOUND_DRAFT_REMOVAL_MIGRATION = Migration(
    version=16,
    name="remove_outbound_drafts",
    statements=(
        "DROP TRIGGER set_outbound_messages_agent_identity",
        "DROP INDEX idx_outbound_session_created",
        "DROP INDEX idx_outbound_state_created",
        """
        CREATE TABLE _outbound_messages_provider_attempts (
            outbound_message_id TEXT PRIMARY KEY,
            command_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            channel_session_id TEXT NOT NULL,
            target TEXT NOT NULL,
            reply_to_message_id TEXT,
            body TEXT NOT NULL,
            state TEXT NOT NULL CHECK (
                state IN ('pending', 'queued', 'sent', 'partial', 'failed', 'unknown')
            ),
            snapshot_seq INTEGER NOT NULL,
            current_inbound_seq INTEGER NOT NULL,
            provider_message_id TEXT,
            provider_receipt_ref TEXT,
            created_at_ms INTEGER NOT NULL,
            provider_attempted_at_ms INTEGER NOT NULL,
            completed_at_ms INTEGER,
            error_kind TEXT,
            error_message TEXT,
            metadata_json TEXT,
            attachments_json TEXT NOT NULL DEFAULT '[]',
            agent_id TEXT,
            agent_name TEXT
        )
        """,
        """
        INSERT INTO _outbound_messages_provider_attempts (
            outbound_message_id,
            command_id,
            session_id,
            channel_session_id,
            target,
            reply_to_message_id,
            body,
            state,
            snapshot_seq,
            current_inbound_seq,
            provider_message_id,
            provider_receipt_ref,
            created_at_ms,
            provider_attempted_at_ms,
            completed_at_ms,
            error_kind,
            error_message,
            metadata_json,
            attachments_json,
            agent_id,
            agent_name
        )
        SELECT
            outbound_message_id,
            command_id,
            session_id,
            channel_session_id,
            target,
            reply_to_message_id,
            body,
            state,
            snapshot_seq,
            current_inbound_seq,
            provider_message_id,
            provider_receipt_ref,
            created_at_ms,
            provider_attempted_at_ms,
            completed_at_ms,
            error_kind,
            error_message,
            metadata_json,
            attachments_json,
            agent_id,
            agent_name
        FROM outbound_messages
        WHERE state IN ('pending', 'queued', 'sent', 'partial', 'failed', 'unknown')
        """,
        "DROP TABLE outbound_messages",
        "ALTER TABLE _outbound_messages_provider_attempts RENAME TO outbound_messages",
        """
        CREATE INDEX idx_outbound_session_created
            ON outbound_messages (session_id, created_at_ms)
        """,
        """
        CREATE INDEX idx_outbound_state_created
            ON outbound_messages (state, created_at_ms)
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
    ),
)

__all__ = ["OUTBOUND_DRAFT_REMOVAL_MIGRATION"]
