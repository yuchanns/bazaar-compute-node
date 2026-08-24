from __future__ import annotations

from .model import Migration

SESSION_MAPPING_INDEX_MIGRATION = Migration(
    version=2,
    name="session_mapping_indexes",
    statements=(
        """
        -- Provider identity lookup used by channel session get-or-create.
        CREATE INDEX idx_channel_sessions_provider_identity
            ON channel_sessions (
                channel,
                provider_thread_id
            )
        """,
        """
        -- Channel-to-bcn session lookup used during recovery reconciliation.
        CREATE INDEX idx_bcn_sessions_channel
            ON bcn_sessions (channel_session_id)
        """,
        """
        -- Bcn-to-runtime session lookup used during process reconciliation.
        CREATE INDEX idx_runtime_sessions_bcn
            ON runtime_sessions (bcn_session_id)
        """,
    ),
)


__all__ = ["SESSION_MAPPING_INDEX_MIGRATION"]
