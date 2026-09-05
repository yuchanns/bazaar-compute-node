{% if target_lines %}Pending inbox targets: {{ target_lines | length }}.{% for line in target_lines %}
{{ line }}{% endfor %}{% else %}No pending inbox targets.{% endif %}{{ "" -}}
