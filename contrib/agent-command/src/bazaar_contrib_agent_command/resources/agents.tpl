{% if result[0].agents %}
{{ rows | agent_table }}
{% else %}
No agents
{% endif %}
