## {{ title }}{% for row in rows %}
- {{ row.icon }} {{ row.kind }}{% if row.name %} · **{{ row.name }}**{% endif %}{% endfor %}
