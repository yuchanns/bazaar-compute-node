Inbox targets: {{ shown }} returned, offset {{ offset }}, total {{ total }}, ordered by recent activity.{% for line in target_lines %}
{{ line }}{% endfor %}
{% if total == 0 %}No message targets.{% elif not target_lines %}No more message targets.{% elif has_more %}More message targets remain. Run `bcc inbox list --offset {{ next_offset }}`.{% else %}No more message targets.{% endif %}{{ "" -}}
