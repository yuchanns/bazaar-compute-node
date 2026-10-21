Error: {{ message }}
Code: {{ code }}{% if draft_saved %}
Draft saved: yes{% endif %}{% if next_action is not none %}
Next action: {{ next_action }}{% endif %}{{ "" -}}
