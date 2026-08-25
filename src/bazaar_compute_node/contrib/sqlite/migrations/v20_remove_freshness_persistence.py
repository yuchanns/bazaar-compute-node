from __future__ import annotations

from .model import Migration

FRESHNESS_PERSISTENCE_REMOVAL_MIGRATION = Migration(
    version=20,
    name="remove_freshness_persistence",
    statements=(
        "DROP INDEX idx_messages_agent_session_target_seq",
        "DROP INDEX idx_messages_agent_direction_seq",
        "DROP INDEX idx_messages_inbound_provider_identity",
        "DROP INDEX idx_messages_outbound_command",
        "DROP INDEX idx_messages_outbound_state_created",
        "DROP INDEX idx_messages_reply_to_message",
        "ALTER TABLE messages RENAME TO _messages_v20",
        """
        CREATE TABLE messages (
            message_id TEXT PRIMARY KEY,
            seq INTEGER NOT NULL UNIQUE CHECK (seq > 0),
            direction TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
            agent_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            channel_session_id TEXT NOT NULL,
            channel TEXT NOT NULL,
            provider_thread_id TEXT NOT NULL,
            provider_message_id TEXT,
            provider_time_ms INTEGER,
            received_at_ms INTEGER,
            sender TEXT,
            message_type TEXT NOT NULL,
            target TEXT NOT NULL,
            target_kind TEXT NOT NULL CHECK (target_kind IN ('dm', 'group')),
            reply_to_message_id TEXT,
            body TEXT NOT NULL,
            mentions_agent INTEGER CHECK (mentions_agent IN (0, 1)),
            notifies_runtime INTEGER CHECK (notifies_runtime IN (0, 1)),
            provider_payload_ref TEXT,
            command_id TEXT,
            delivery_state TEXT CHECK (
                delivery_state IN (
                    'pending', 'queued', 'sent', 'partial', 'failed', 'unknown'
                )
            ),
            provider_receipt_ref TEXT,
            created_at_ms INTEGER,
            provider_attempted_at_ms INTEGER,
            completed_at_ms INTEGER,
            error_kind TEXT,
            error_message TEXT,
            metadata_json TEXT,
            attachments_json TEXT,
            CHECK (
                (
                    direction = 'inbound'
                    AND received_at_ms IS NOT NULL
                    AND mentions_agent IS NOT NULL
                    AND notifies_runtime IS NOT NULL
                    AND command_id IS NULL
                    AND delivery_state IS NULL
                    AND provider_receipt_ref IS NULL
                    AND created_at_ms IS NULL
                    AND provider_attempted_at_ms IS NULL
                    AND completed_at_ms IS NULL
                    AND error_kind IS NULL
                    AND error_message IS NULL
                    AND attachments_json IS NULL
                ) OR (
                    direction = 'outbound'
                    AND command_id IS NOT NULL
                    AND delivery_state IS NOT NULL
                    AND created_at_ms IS NOT NULL
                    AND provider_attempted_at_ms IS NOT NULL
                    AND received_at_ms IS NULL
                    AND mentions_agent IS NULL
                    AND notifies_runtime IS NULL
                    AND provider_payload_ref IS NULL
                    AND attachments_json IS NOT NULL
                )
            )
        )
        """,
        """
        INSERT INTO messages (
            message_id, seq, direction, agent_id, session_id, channel_session_id,
            channel, provider_thread_id, provider_message_id, provider_time_ms,
            received_at_ms, sender, message_type, target, target_kind,
            reply_to_message_id, body, mentions_agent, notifies_runtime,
            provider_payload_ref, command_id, delivery_state,
            provider_receipt_ref, created_at_ms, provider_attempted_at_ms,
            completed_at_ms, error_kind, error_message, metadata_json,
            attachments_json
        )
        SELECT
            message_id, seq, direction, agent_id, session_id, channel_session_id,
            channel, provider_thread_id, provider_message_id, provider_time_ms,
            received_at_ms, sender, message_type, target, target_kind,
            reply_to_message_id, body, mentions_agent, notifies_runtime,
            provider_payload_ref, command_id, delivery_state,
            provider_receipt_ref, created_at_ms, provider_attempted_at_ms,
            completed_at_ms, error_kind, error_message, metadata_json,
            attachments_json
        FROM _messages_v20
        ORDER BY seq
        """,
        "DROP TABLE _messages_v20",
        """
        CREATE INDEX idx_messages_agent_session_target_seq
            ON messages (agent_id, session_id, target, seq)
        """,
        """
        CREATE INDEX idx_messages_agent_direction_seq
            ON messages (agent_id, direction, seq)
        """,
        """
        CREATE UNIQUE INDEX idx_messages_inbound_provider_identity
            ON messages (
                agent_id,
                channel,
                provider_thread_id,
                provider_message_id
            )
            WHERE direction = 'inbound'
        """,
        """
        CREATE UNIQUE INDEX idx_messages_outbound_command
            ON messages (agent_id, command_id)
            WHERE direction = 'outbound'
        """,
        """
        CREATE INDEX idx_messages_outbound_state_created
            ON messages (agent_id, delivery_state, created_at_ms)
            WHERE direction = 'outbound'
        """,
        """
        CREATE INDEX idx_messages_reply_to_message
            ON messages (reply_to_message_id)
        """,
        "ALTER TABLE consumer_cursors RENAME TO _consumer_cursors_v20",
        """
        CREATE TABLE consumer_cursors (
            session_id TEXT PRIMARY KEY,
            delivered_through_seq INTEGER NOT NULL,
            last_check_at_ms INTEGER,
            last_read_at_ms INTEGER,
            updated_at_ms INTEGER NOT NULL
        )
        """,
        """
        INSERT INTO consumer_cursors (
            session_id, delivered_through_seq, last_check_at_ms,
            last_read_at_ms, updated_at_ms
        )
        SELECT
            session_id, delivered_through_seq, last_check_at_ms,
            last_read_at_ms, updated_at_ms
        FROM _consumer_cursors_v20
        """,
        "DROP TABLE _consumer_cursors_v20",
    ),
)

__all__ = ["FRESHNESS_PERSISTENCE_REMOVAL_MIGRATION"]
