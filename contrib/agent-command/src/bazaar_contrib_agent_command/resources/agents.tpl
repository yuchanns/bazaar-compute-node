{% if result.agents %}
{{ rows | table(("ID", "Name", "Channels", "Harnesses")) }}
{% else %}
No agents
{% endif %}
