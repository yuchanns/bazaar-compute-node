### {{ title }} · {{ state }}
{% if line %}
- {{ line.icon }} {{ line.label }}{% if line.name %} · **{{ line.name }}**{% endif %}{% endif %}{% for row in overview %}
- {{ row }}{% endfor %}{% if note %}

_{{ note }}_{% endif %}
