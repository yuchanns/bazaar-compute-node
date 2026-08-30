{% if delivery_state == "sent" %}Message sent to {{ target }}. Message ID: {{ message_id }}{% else %}Message queued to {{ target }}. Message ID: {{ message_id }}{% endif %}{{ "" -}}
