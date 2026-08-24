from __future__ import annotations

from .model import Migration

MESSAGE_LOG_INDEX_MIGRATION = Migration(
    version=3,
    name="message_log_indexes",
    statements=(
        """
        -- Provider-scoped inbound deduplication lookup.
        CREATE INDEX idx_inbound_provider_identity
            ON inbound_messages (channel, provider_message_id)
        """,
        """
        -- Target-filtered history lookup for one bcn session.
        CREATE INDEX idx_inbound_session_target_seq
            ON inbound_messages (session_id, canonical_target, seq)
        """,
    ),
)


__all__ = ["MESSAGE_LOG_INDEX_MIGRATION"]
