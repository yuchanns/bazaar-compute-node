Read window: {{ shown }} returned, seq {% if message_lines %}{{ first_seq }}-{{ last_seq }}{% else %}none-none{% endif %}, oldest to newest.{% if referenced_lines %}
Referenced messages: {{ referenced_lines | length }}{% for line in referenced_lines %}
{{ line }}{% endfor %}
Window messages:{% endif %}{% for line in message_lines %}
{{ line }}{% endfor %}{{ "" -}}
