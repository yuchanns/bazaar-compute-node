from __future__ import annotations

from .model import Migration

_LATEST_INBOUND_SEQ = """
    COALESCE((
        SELECT MAX(seq) FROM messages
        WHERE messages.thread_id = consumer_cursors.thread_id
          AND messages.direction = 'inbound'
    ), 0)
"""

HANDOFF_MESSAGE_REMOVAL_MIGRATION = Migration(
    version=26,
    name="remove_handoff_messages",
    statements=(
        """
        DELETE FROM messages
        WHERE json_extract(metadata_json, '$.system_message_kind') = 'handoff'
        """,
        f"""
        UPDATE consumer_cursors
        SET delivered_through_seq = {_LATEST_INBOUND_SEQ}
        WHERE delivered_through_seq > {_LATEST_INBOUND_SEQ}
        """,
    ),
)

__all__ = ["HANDOFF_MESSAGE_REMOVAL_MIGRATION"]
