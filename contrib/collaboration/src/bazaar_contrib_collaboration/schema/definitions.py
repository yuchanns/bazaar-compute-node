"""Table and index definitions for this module and its published upgrades."""

import bazaar_contrib_storage as s

SETTINGS_V31 = s.Table(
    name="settings",
    columns=(
        s.ColumnDefinition(name="agent_id", type="text"),
        s.ColumnDefinition(name="key", type="text"),
        s.ColumnDefinition(name="value", type="text"),
        s.ColumnDefinition(name="updated_at_ms", type="integer"),
    ),
    primary_key=("agent_id", "key"),
)
