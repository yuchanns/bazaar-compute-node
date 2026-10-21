{% if messages %}{% if query %}Search results for: "{{ query }}" ({{ shown }} result{% if shown != 1 %}s{% endif %}){% else %}Filtered message results ({{ shown }} result{% if shown != 1 %}s{% endif %}){% endif %}

{% for message in messages %}<result ref="msg:{{ message.message_id }}">
Source: {{ message.source }}
Sender: {{ message.sender }} ({{ message.sender_kind }})
Time: {{ message.timestamp }}

<preview>
{{ message.preview }}
</preview>
</result>

{% endfor %}If a result may be relevant but its preview is not enough, read the surrounding context for that result before answering.
{% else %}No search results.
{% endif %}
Search: shown={{ shown }} offset={{ offset }} sort={{ sort }} has_more={{ "true" if has_more else "false" }}{% if next_offset is not none %} next_offset={{ next_offset }}{% endif %}{{ "" -}}
