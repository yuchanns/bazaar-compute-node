"""Table and index definitions for this module and its published upgrades."""

import bazaar_contrib_storage as s

IDX_REMINDERS_STATE_NEXT_V12 = s.Index(
    name="idx_reminders_state_next", columns=("state", "next_fire_at_ms", "reminder_id")
)


IDX_REMINDERS_OWNER_STATE_UPDATED_V25 = s.Index(
    name="idx_reminders_owner_state_updated",
    columns=("agent_id", "owner_thread_id", "state", "updated_at_ms", "reminder_id"),
)


REMINDERS = s.Table(
    name="reminders",
    columns=(
        s.ColumnDefinition(name="reminder_id", type="text", nullable=True),
        s.ColumnDefinition(name="owner_thread_id", type="text", nullable=True),
        s.ColumnDefinition(name="anchor_message_id", type="text", nullable=True),
        s.ColumnDefinition(name="title", type="text", nullable=True),
        s.ColumnDefinition(name="state", type="text", nullable=True),
        s.ColumnDefinition(name="next_fire_at_ms", type="integer", nullable=True),
        s.ColumnDefinition(name="repeat_rule", type="text", nullable=True),
        s.ColumnDefinition(name="timezone", type="text", nullable=True),
        s.ColumnDefinition(name="revision", type="integer", nullable=True),
        s.ColumnDefinition(name="last_occurrence_no", type="integer", nullable=True),
        s.ColumnDefinition(name="created_at_ms", type="integer", nullable=True),
        s.ColumnDefinition(name="updated_at_ms", type="integer", nullable=True),
        s.ColumnDefinition(name="last_fired_at_ms", type="integer", nullable=True),
        s.ColumnDefinition(name="canceled_at_ms", type="integer", nullable=True),
        s.ColumnDefinition(name="agent_id", type="text", nullable=True),
    ),
    primary_key=("reminder_id",),
)
