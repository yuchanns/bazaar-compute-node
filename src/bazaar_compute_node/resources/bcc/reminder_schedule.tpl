Reminder scheduled: #{{ reminder_id }} ({% if repeat_rule is none %}one-time{% else %}{{ repeat_rule }}{% endif %}) {{ title }}
Next: {{ next_fire }}{{ "" -}}
