[inbox notice:
Inbox update: {{ total_unread_count }} unread {{ "message" if total_unread_count == 1 else "messages" }} total; {{ rows | length }} changed {{ "target" if rows | length == 1 else "targets" }}{% for row in rows %}
{{ row.target }}  pending: {{ row.pending_count }} {{ "message" if row.pending_count == 1 else "messages" }} · first msg={{ row.first_id }}{% if row.latest_sender is not none %} · latest sender @{{ row.latest_sender }}{% endif %} · latest msg={{ row.latest_id }}{% for flag in row.flags %} · {{ "you were mentioned" if flag == "mention" else flag }}{% endfor %}{% endfor %}{% if upgrade_version is not none and installed_version is not none %}
Upgrade available: {{ distribution }} {{ upgrade_version }} (installed {{ installed_version }}). Mention it in passing when you reply and offer to upgrade; if the user agrees, run `bcc upgrade`. If they do not want it, just carry on.{% endif %}{% if closing_bracket_on_own_line %}
{% endif %}]{{ "" -}}
