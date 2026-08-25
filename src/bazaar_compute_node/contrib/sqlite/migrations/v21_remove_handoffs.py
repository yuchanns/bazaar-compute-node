from __future__ import annotations

from .model import Migration

HANDOFF_REMOVAL_MIGRATION = Migration(
    version=21,
    name="remove_handoffs",
    statements=(
        "DROP INDEX idx_handoffs_agent_target_read_seq",
        "DROP TABLE handoffs",
    ),
)

__all__ = ["HANDOFF_REMOVAL_MIGRATION"]
