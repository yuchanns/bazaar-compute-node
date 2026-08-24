from __future__ import annotations

from .model import Migration

INBOUND_MESSAGE_REFERENCE_MIGRATION = Migration(
    version=5,
    name="inbound_message_references",
    statements=(
        """
        ALTER TABLE inbound_messages
            RENAME COLUMN reply_to_provider_message_id TO reply_to_message_id
        """,
        """
        UPDATE inbound_messages AS current
        SET reply_to_message_id = (
            SELECT referenced.message_id
            FROM inbound_messages AS referenced
            WHERE referenced.channel = current.channel
              AND referenced.provider_message_id = current.reply_to_message_id
            ORDER BY referenced.seq
            LIMIT 1
        )
        WHERE current.reply_to_message_id IS NOT NULL
        """,
        """
        CREATE INDEX idx_inbound_reply_to_message
            ON inbound_messages (reply_to_message_id)
        """,
    ),
)


__all__ = ["INBOUND_MESSAGE_REFERENCE_MIGRATION"]
