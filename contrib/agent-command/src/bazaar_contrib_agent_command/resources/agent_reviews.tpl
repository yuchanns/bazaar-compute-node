{% if result %}
{{ rows | table(("Thread", "Channel", "Target")) }}
{% else %}
No pending conversations
{% endif %}
