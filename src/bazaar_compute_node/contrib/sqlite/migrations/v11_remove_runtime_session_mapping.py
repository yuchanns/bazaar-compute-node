from __future__ import annotations

from .model import Migration

RUNTIME_SESSION_MAPPING_REMOVAL_MIGRATION = Migration(
    version=11,
    name="remove_runtime_session_mapping",
    statements=(
        """
        DROP INDEX idx_runtime_sessions_bcn
        """,
        """
        DROP TABLE runtime_sessions
        """,
    ),
)


__all__ = ["RUNTIME_SESSION_MAPPING_REMOVAL_MIGRATION"]
