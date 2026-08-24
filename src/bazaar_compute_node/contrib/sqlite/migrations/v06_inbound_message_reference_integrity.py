from __future__ import annotations

from .model import Migration

INBOUND_MESSAGE_REFERENCE_INTEGRITY_MIGRATION = Migration(
    version=6,
    name="inbound_message_reference_integrity",
    statements=(
        """
        UPDATE inbound_messages AS current
        SET reply_to_message_id = NULL
        WHERE current.reply_to_message_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1
              FROM inbound_messages AS referenced
              WHERE referenced.message_id = current.reply_to_message_id
                AND referenced.session_id = current.session_id
                AND referenced.seq < current.seq
          )
        """,
    ),
)


__all__ = ["INBOUND_MESSAGE_REFERENCE_INTEGRITY_MIGRATION"]
