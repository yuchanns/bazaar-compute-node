{% if referenced_lines %}Referenced messages: {{ referenced_lines | length }}{% for line in referenced_lines %}
{{ line }}{% endfor %}
New messages:{% endif %}{% for line in message_lines %}{% if not loop.first or referenced_lines %}
{% endif %}{{ line }}{% endfor %}{% if not referenced_lines and not message_lines %}No more new messages.{% endif %}{{ "" -}}
