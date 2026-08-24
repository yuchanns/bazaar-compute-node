from __future__ import annotations

from .model import Migration

INBOX_DISCOVERY_MIGRATION = Migration(
    version=14,
    name="add_inbox_discovery_indexes",
    statements=(
        """
        CREATE INDEX idx_inbound_agent_session_seq
            ON inbound_messages (agent_id, session_id, seq DESC)
        """,
        """
        CREATE INDEX idx_inbound_agent_target_session
            ON inbound_messages (agent_id, canonical_target, session_id)
        """,
    ),
)
__all__ = ["INBOX_DISCOVERY_MIGRATION"]
