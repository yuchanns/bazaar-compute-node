"""Table and index definitions for this module and its published upgrades."""

import bazaar_contrib_storage as s

IDX_RUNTIME_ATTEMPTS_SESSION_STARTED_V13 = s.Index(
    name="idx_runtime_attempts_session_started",
    columns=("agent_id", "session_id", "started_at_ms"),
)


RUNTIME_ATTEMPTS = s.Table(
    name="runtime_attempts",
    columns=(
        s.ColumnDefinition(name="turn_id", type="text", nullable=True),
        s.ColumnDefinition(name="session_id", type="text", nullable=True),
        s.ColumnDefinition(name="client_user_message_id", type="text", nullable=True),
        s.ColumnDefinition(name="started_at_ms", type="integer", nullable=True),
        s.ColumnDefinition(name="agent_id", type="text", nullable=True),
    ),
    primary_key=("turn_id",),
)
