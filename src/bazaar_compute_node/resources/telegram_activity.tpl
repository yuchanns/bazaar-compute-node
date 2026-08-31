## {{ title }}{% for row in rows %}
- {{ row.icon }} {{ row.status }} · **{{ row.name }}**{% if row.input %} — {{ input_label }}: {{ row.input }}{% endif %}{% if row.output %}{% if row.input %}; {% else %} — {% endif %}{{ output_label }}: {{ row.output }}{% endif %}{% endfor %}
