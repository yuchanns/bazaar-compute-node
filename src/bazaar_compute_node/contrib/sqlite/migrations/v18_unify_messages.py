from __future__ import annotations

from .model import Migration

MESSAGE_UNIFICATION_MIGRATION = Migration(
    version=18,
    name="unify_messages",
    statements=(
        """
        CREATE TEMP TABLE _message_migration_guard_v18 (
            ok INTEGER NOT NULL CHECK (ok = 1)
        )
        """,
        """
        INSERT INTO _message_migration_guard_v18 (ok)
        SELECT CASE WHEN EXISTS (
            SELECT 1
            FROM inbound_messages AS inbound
            INNER JOIN outbound_messages AS outbound
                ON outbound.outbound_message_id = inbound.message_id
        ) THEN 0 ELSE 1 END
        """,
        """
        CREATE TEMP TABLE _message_order_v18 AS
        WITH legacy_messages AS (
            SELECT
                'inbound' AS direction,
                message_id,
                seq AS inbound_seq,
                COALESCE(provider_time_ms, received_at_ms) AS event_time_ms
            FROM inbound_messages
            UNION ALL
            SELECT
                'outbound' AS direction,
                outbound_message_id AS message_id,
                NULL AS inbound_seq,
                created_at_ms AS event_time_ms
            FROM outbound_messages
        )
        SELECT
            direction,
            message_id,
            inbound_seq,
            ROW_NUMBER() OVER (
                ORDER BY event_time_ms, message_id
            ) AS seq
        FROM legacy_messages
        """,
        """
        CREATE UNIQUE INDEX _idx_message_order_id_v18
            ON _message_order_v18 (message_id)
        """,
        """
        CREATE UNIQUE INDEX _idx_message_order_inbound_seq_v18
            ON _message_order_v18 (inbound_seq)
            WHERE direction = 'inbound'
        """,
        """
        INSERT INTO _message_migration_guard_v18 (ok)
        SELECT CASE WHEN EXISTS (
            SELECT 1
            FROM consumer_cursors AS cursor
            WHERE (
                cursor.delivered_through_seq > 0
                AND NOT EXISTS (
                    SELECT 1
                    FROM _message_order_v18 AS message_order
                    WHERE message_order.direction = 'inbound'
                      AND message_order.inbound_seq = cursor.delivered_through_seq
                )
            ) OR (
                cursor.inbox_snapshot_seq > 0
                AND NOT EXISTS (
                    SELECT 1
                    FROM _message_order_v18 AS message_order
                    WHERE message_order.direction = 'inbound'
                      AND message_order.inbound_seq = cursor.inbox_snapshot_seq
                )
            )
        ) OR EXISTS (
            SELECT 1
            FROM outbound_messages AS outbound
            WHERE (
                outbound.snapshot_seq > 0
                AND NOT EXISTS (
                    SELECT 1
                    FROM _message_order_v18 AS message_order
                    WHERE message_order.direction = 'inbound'
                      AND message_order.inbound_seq = outbound.snapshot_seq
                )
            ) OR (
                outbound.current_inbound_seq > 0
                AND NOT EXISTS (
                    SELECT 1
                    FROM _message_order_v18 AS message_order
                    WHERE message_order.direction = 'inbound'
                      AND message_order.inbound_seq = outbound.current_inbound_seq
                )
            )
        ) THEN 0 ELSE 1 END
        """,
        """
        UPDATE consumer_cursors
        SET delivered_through_seq = CASE
                WHEN delivered_through_seq = 0 THEN 0
                ELSE (
                    SELECT seq
                    FROM _message_order_v18
                    WHERE direction = 'inbound'
                      AND inbound_seq = consumer_cursors.delivered_through_seq
                )
            END,
            inbox_snapshot_seq = CASE
                WHEN inbox_snapshot_seq IS NULL OR inbox_snapshot_seq = 0
                    THEN inbox_snapshot_seq
                ELSE (
                    SELECT seq
                    FROM _message_order_v18
                    WHERE direction = 'inbound'
                      AND inbound_seq = consumer_cursors.inbox_snapshot_seq
                )
            END
        """,
        """
        UPDATE outbound_messages
        SET snapshot_seq = CASE
                WHEN snapshot_seq = 0 THEN 0
                ELSE (
                    SELECT seq
                    FROM _message_order_v18
                    WHERE direction = 'inbound'
                      AND inbound_seq = outbound_messages.snapshot_seq
                )
            END,
            current_inbound_seq = CASE
                WHEN current_inbound_seq = 0 THEN 0
                ELSE (
                    SELECT seq
                    FROM _message_order_v18
                    WHERE direction = 'inbound'
                      AND inbound_seq = outbound_messages.current_inbound_seq
                )
            END
        """,
        """
        CREATE TABLE _messages_v18 (
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
                    AND provider_message_id IS NOT NULL
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
        INSERT INTO _messages_v18 (
            message_id,
            seq,
            direction,
            agent_id,
            session_id,
            channel_session_id,
            channel,
            provider_thread_id,
            provider_message_id,
            provider_time_ms,
            received_at_ms,
            sender,
            message_type,
            target,
            target_kind,
            reply_to_message_id,
            body,
            mentions_agent,
            notifies_runtime,
            provider_payload_ref,
            metadata_json
        )
        SELECT
            inbound.message_id,
            message_order.seq,
            'inbound',
            inbound.agent_id,
            inbound.session_id,
            inbound.channel_session_id,
            inbound.channel,
            inbound.provider_thread_id,
            inbound.provider_message_id,
            inbound.provider_time_ms,
            inbound.received_at_ms,
            inbound.sender,
            inbound.message_type,
            inbound.canonical_target,
            inbound.target_kind,
            inbound.reply_to_message_id,
            inbound.body,
            inbound.mentions_agent,
            inbound.notifies_runtime,
            inbound.provider_payload_ref,
            inbound.metadata_json
        FROM inbound_messages AS inbound
        INNER JOIN _message_order_v18 AS message_order
            ON message_order.direction = 'inbound'
           AND message_order.message_id = inbound.message_id
        """,
        """
        INSERT INTO _messages_v18 (
            message_id,
            seq,
            direction,
            agent_id,
            session_id,
            channel_session_id,
            channel,
            provider_thread_id,
            provider_message_id,
            sender,
            message_type,
            target,
            target_kind,
            reply_to_message_id,
            body,
            command_id,
            delivery_state,
            snapshot_seq,
            current_inbound_seq,
            provider_receipt_ref,
            created_at_ms,
            provider_attempted_at_ms,
            completed_at_ms,
            error_kind,
            error_message,
            metadata_json,
            attachments_json
        )
        SELECT
            outbound.outbound_message_id,
            message_order.seq,
            'outbound',
            outbound.agent_id,
            outbound.session_id,
            outbound.channel_session_id,
            channel_session.channel,
            channel_session.provider_thread_id,
            outbound.provider_message_id,
            outbound.agent_name,
            'text',
            outbound.target,
            channel_session.target_kind,
            outbound.reply_to_message_id,
            outbound.body,
            outbound.command_id,
            outbound.state,
            outbound.snapshot_seq,
            outbound.current_inbound_seq,
            outbound.provider_receipt_ref,
            outbound.created_at_ms,
            outbound.provider_attempted_at_ms,
            outbound.completed_at_ms,
            outbound.error_kind,
            outbound.error_message,
            outbound.metadata_json,
            outbound.attachments_json
        FROM outbound_messages AS outbound
        INNER JOIN _message_order_v18 AS message_order
            ON message_order.direction = 'outbound'
           AND message_order.message_id = outbound.outbound_message_id
        INNER JOIN channel_sessions AS channel_session
            ON channel_session.agent_id = outbound.agent_id
           AND channel_session.id = outbound.channel_session_id
        """,
        """
        INSERT INTO _message_migration_guard_v18 (ok)
        SELECT CASE WHEN
            (SELECT COUNT(*) FROM _messages_v18)
                != (
                    (SELECT COUNT(*) FROM inbound_messages)
                    + (SELECT COUNT(*) FROM outbound_messages)
                )
            OR (SELECT COUNT(*) FROM _messages_v18 WHERE direction = 'inbound')
                != (SELECT COUNT(*) FROM inbound_messages)
            OR (SELECT COUNT(*) FROM _messages_v18 WHERE direction = 'outbound')
                != (SELECT COUNT(*) FROM outbound_messages)
            OR EXISTS (
                SELECT 1
                FROM inbound_attachments AS attachment
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM _messages_v18 AS message
                    WHERE message.direction = 'inbound'
                      AND message.message_id = attachment.message_id
                )
            )
        THEN 0 ELSE 1 END
        """,
        "DROP TABLE inbound_messages",
        "DROP TABLE outbound_messages",
        "ALTER TABLE _messages_v18 RENAME TO messages",
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
        "DROP TABLE _message_order_v18",
        "DROP TABLE _message_migration_guard_v18",
    ),
)


__all__ = ["MESSAGE_UNIFICATION_MIGRATION"]
