from __future__ import annotations

from .model import Migration
from .registry import (
    MIGRATIONS,
    MigrationChecksumError,
    MigrationError,
    apply_migrations,
)
from .v01_initial_node_schema import SCHEMA_MIGRATION
from .v02_session_mapping_indexes import SESSION_MAPPING_INDEX_MIGRATION
from .v03_message_log_indexes import MESSAGE_LOG_INDEX_MIGRATION
from .v04_runtime_attempt_facts import RUNTIME_ATTEMPT_FACT_MIGRATION
from .v05_inbound_message_references import INBOUND_MESSAGE_REFERENCE_MIGRATION
from .v06_inbound_message_reference_integrity import (
    INBOUND_MESSAGE_REFERENCE_INTEGRITY_MIGRATION,
)
from .v07_inbound_provider_identity import INBOUND_PROVIDER_IDENTITY_MIGRATION
from .v08_transient_stream_events import TRANSIENT_STREAM_EVENT_MIGRATION
from .v09_remove_runtime_events import RUNTIME_EVENTS_REMOVAL_MIGRATION
from .v10_add_outbound_attachments import OUTBOUND_ATTACHMENTS_MIGRATION
from .v11_remove_runtime_session_mapping import (
    RUNTIME_SESSION_MAPPING_REMOVAL_MIGRATION,
)
from .v12_add_reminders import REMINDER_MIGRATION
from .v13_add_agent_ownership import AGENT_OWNERSHIP_MIGRATION
from .v14_add_inbox_discovery_indexes import INBOX_DISCOVERY_MIGRATION
from .v15_add_handoffs import HANDOFF_MIGRATION
from .v16_remove_outbound_drafts import OUTBOUND_DRAFT_REMOVAL_MIGRATION
from .v17_remove_agent_identity_triggers import STORAGE_ACCESS_MIGRATION
from .v18_unify_messages import MESSAGE_UNIFICATION_MIGRATION
from .v19_reminder_system_messages import REMINDER_SYSTEM_MESSAGE_MIGRATION

__all__ = [
    "AGENT_OWNERSHIP_MIGRATION",
    "HANDOFF_MIGRATION",
    "INBOUND_MESSAGE_REFERENCE_INTEGRITY_MIGRATION",
    "INBOUND_MESSAGE_REFERENCE_MIGRATION",
    "INBOUND_PROVIDER_IDENTITY_MIGRATION",
    "INBOX_DISCOVERY_MIGRATION",
    "MESSAGE_LOG_INDEX_MIGRATION",
    "MESSAGE_UNIFICATION_MIGRATION",
    "MIGRATIONS",
    "OUTBOUND_ATTACHMENTS_MIGRATION",
    "OUTBOUND_DRAFT_REMOVAL_MIGRATION",
    "REMINDER_MIGRATION",
    "REMINDER_SYSTEM_MESSAGE_MIGRATION",
    "RUNTIME_ATTEMPT_FACT_MIGRATION",
    "RUNTIME_EVENTS_REMOVAL_MIGRATION",
    "RUNTIME_SESSION_MAPPING_REMOVAL_MIGRATION",
    "SCHEMA_MIGRATION",
    "SESSION_MAPPING_INDEX_MIGRATION",
    "STORAGE_ACCESS_MIGRATION",
    "TRANSIENT_STREAM_EVENT_MIGRATION",
    "Migration",
    "MigrationChecksumError",
    "MigrationError",
    "apply_migrations",
]
