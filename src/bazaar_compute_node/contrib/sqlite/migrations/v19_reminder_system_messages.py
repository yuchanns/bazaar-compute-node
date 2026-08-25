from __future__ import annotations

from .model import Migration

REMINDER_SYSTEM_MESSAGE_MIGRATION = Migration(
    version=19,
    name="reminder_system_messages",
    statements=(
        "DROP INDEX idx_messages_agent_session_target_seq",
        "DROP INDEX idx_messages_agent_direction_seq",
        "DROP INDEX idx_messages_inbound_provider_identity",
        "DROP INDEX idx_messages_outbound_command",
        "DROP INDEX idx_messages_outbound_state_created",
        "DROP INDEX idx_messages_reply_to_message",
        "ALTER TABLE messages RENAME TO _messages_v19",
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
            snapshot_seq INTEGER,
            current_inbound_seq INTEGER,
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
                    AND snapshot_seq IS NULL
                    AND current_inbound_seq IS NULL
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
                    AND snapshot_seq IS NOT NULL
                    AND current_inbound_seq IS NOT NULL
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
            provider_payload_ref, command_id, delivery_state, snapshot_seq,
            current_inbound_seq, provider_receipt_ref, created_at_ms,
            provider_attempted_at_ms, completed_at_ms, error_kind, error_message,
            metadata_json, attachments_json
        )
        SELECT
            message_id, seq, direction, agent_id, session_id, channel_session_id,
            channel, provider_thread_id, provider_message_id, provider_time_ms,
            received_at_ms, sender, message_type, target, target_kind,
            reply_to_message_id, body, mentions_agent, notifies_runtime,
            provider_payload_ref, command_id, delivery_state, snapshot_seq,
            current_inbound_seq, provider_receipt_ref, created_at_ms,
            provider_attempted_at_ms, completed_at_ms, error_kind, error_message,
            metadata_json, attachments_json
        FROM _messages_v19
        ORDER BY seq
        """,
        "DROP TABLE _messages_v19",
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
        """
        WITH migration_base AS (
            SELECT COALESCE(MAX(seq), 0) AS max_seq FROM messages
        ),
        pending_occurrences AS (
            SELECT
                occurrence.*,
                reminder.title,
                reminder.repeat_rule,
                anchor.channel_session_id,
                anchor.channel,
                anchor.provider_thread_id,
                anchor.target,
                anchor.target_kind,
                ROW_NUMBER() OVER (
                    ORDER BY occurrence.fired_at_ms, occurrence.occurrence_id
                ) AS migration_offset
            FROM reminder_occurrences AS occurrence
            JOIN reminders AS reminder
                ON reminder.agent_id = occurrence.agent_id
                AND reminder.reminder_id = occurrence.reminder_id
                AND reminder.owner_session_id = occurrence.owner_session_id
            JOIN messages AS anchor
                ON anchor.agent_id = occurrence.agent_id
                AND anchor.session_id = occurrence.owner_session_id
                AND anchor.message_id = occurrence.anchor_message_id
                AND anchor.direction = 'inbound'
            WHERE occurrence.read_at_ms IS NULL
        )
        INSERT INTO messages (
            message_id, seq, direction, agent_id, session_id, channel_session_id,
            channel, provider_thread_id, provider_message_id, provider_time_ms,
            received_at_ms, sender, message_type, target, target_kind,
            reply_to_message_id, body, mentions_agent, notifies_runtime,
            provider_payload_ref, metadata_json
        )
        SELECT
            occurrence_id,
            migration_base.max_seq + migration_offset,
            'inbound',
            agent_id,
            owner_session_id,
            channel_session_id,
            channel,
            provider_thread_id,
            NULL,
            NULL,
            fired_at_ms,
            'system',
            'text',
            target,
            target_kind,
            NULL,
            CASE
                WHEN next_fire_at_ms IS NULL THEN
                    '🔔 Reminder #' || substr(reminder_id, 1, 8)
                    || ' (one-time) — ' || target || ' — ' || json_quote(title)
                ELSE
                    '🔔 Reminder #' || substr(reminder_id, 1, 8)
                    || ' (recurring · ' || repeat_rule || ') — '
                    || target || ' — ' || json_quote(title)
                    || char(10) || 'Next iteration: '
                    || strftime(
                        '%Y-%m-%dT%H:%M:%fZ',
                        next_fire_at_ms / 1000.0,
                        'unixepoch'
                    )
            END,
            0,
            1,
            NULL,
            '{"sender_kind":"system","system_message_kind":"reminder"}'
        FROM pending_occurrences
        CROSS JOIN migration_base
        ORDER BY fired_at_ms, occurrence_id
        """,
        "DROP INDEX idx_reminder_occurrences_owner_read_fired",
        "DROP INDEX idx_reminder_occurrences_reminder_number",
        "DROP TABLE reminder_occurrences",
    ),
)

__all__ = ["REMINDER_SYSTEM_MESSAGE_MIGRATION"]
