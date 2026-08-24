from __future__ import annotations

from .model import Migration

STORAGE_ACCESS_MIGRATION = Migration(
    version=17,
    name="remove_agent_identity_triggers",
    statements=(
        "DROP TRIGGER set_channel_sessions_agent_id",
        "DROP TRIGGER set_bcn_sessions_agent_id",
        "DROP TRIGGER set_inbound_messages_agent_id",
        "DROP TRIGGER set_outbound_messages_agent_identity",
        "DROP TRIGGER set_runtime_attempts_agent_id",
        "DROP TRIGGER set_reminders_agent_id",
        "DROP TRIGGER set_reminder_occurrences_agent_id",
    ),
)


__all__ = ["STORAGE_ACCESS_MIGRATION"]
