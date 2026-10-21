Unreviewed synced context for this target: {{ total }} {{ "message" if total == 1 else "messages" }}.
Your message has been saved as a draft. Review this target's synced context before sending.

Read window: {{ shown }} returned, seq {% if message_lines %}{{ first_seq }}-{{ last_seq }}{% else %}none-none{% endif %}, oldest to newest. {% if total > shown %}Older unreviewed messages are omitted.{% else %}No older unreviewed messages.{% endif %} No newer unreviewed messages.

{% if referenced_lines %}Referenced messages: {{ referenced_lines | length }}
{% for line in referenced_lines %}{{ line }}
{% endfor %}Window messages:
{% endif %}{% for line in message_lines %}{{ line }}
{% endfor %}
End of window: {{ shown }}/{{ total }} shown.

To update the draft, send revised content normally:
  bcc message send --target {{ target }} <<'BCCMSG'
  revised message
  BCCMSG
To send the current draft unchanged:
  bcc message send --send-draft --target {{ target }}
You can also choose not to send anything.{{ "" -}}
