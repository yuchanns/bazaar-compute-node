{% if result.installed %}
{% set package = result.package or "bazaar-compute-node" %}
{% if result.version %}
{% set package = package ~ "==" ~ result.version %}
{% endif %}
{% if result.environment_changed %}
{% if result.kind == "remove" %}
Uninstalled 1 package
 - {{ package }}
{% else %}
Installed 1 package
 + {{ package }}
{% endif %}
{% else %}
Already installed: {{ package }}
{% endif %}
{% endif %}
{% if result.config_saved %}
{% if result.phase != "complete" %}
Saved configuration
{% elif result.kind == "index-add" and details.get("location") %}
Added contrib index: {{ details.location }}
{% elif result.kind == "index-remove" and details.get("location") %}
Removed contrib index: {{ details.location }}
{% elif result.kind == "add" and details.get("enable") %}
Enabled contribs from {{ details.name }}
{% elif result.kind == "enable" and details.get("entry") %}
Enabled contrib: {{ details.entry }}
{% elif result.kind == "enable" and details.get("name") %}
Enabled contribs from {{ details.name }}
{% elif result.kind == "edit" and "index" in details %}
Updated contrib options at index {{ details.index }}
{% elif result.kind == "disable" and "index" in details %}
Disabled contrib at index {{ details.index }}
{% else %}
Saved configuration
{% endif %}
{% endif %}
{% if result.applied %}
Applied configuration
{% endif %}
{% if result.pending_restart %}
Changes take effect on the next node start
{% endif %}
{% if result.phase != "complete" %}
Operation: {{ result.id }}
{% if result.recovery %}
Recovery: {{ result.recovery }}
{% endif %}
{% endif %}
