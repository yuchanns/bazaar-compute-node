from __future__ import annotations

from .model import Migration

INBOUND_PROVIDER_IDENTITY_MIGRATION = Migration(
    version=7,
    name="inbound_provider_identity",
    statements=(
        """
        DROP INDEX idx_inbound_provider_identity
        """,
        """
        CREATE UNIQUE INDEX idx_inbound_provider_identity
            ON inbound_messages (
                channel,
                provider_thread_id,
                provider_message_id
            )
        """,
    ),
)


__all__ = ["INBOUND_PROVIDER_IDENTITY_MIGRATION"]
