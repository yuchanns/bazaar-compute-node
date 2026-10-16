Name: {{ package.package }}
Version: {{ package.version }}
Summary: {{ package.summary }}
Requires: {{ package.requirements | join(", ") }}
{% for entry in entries %}

Contrib: {{ entry.name }}
{% if entry.error %}
Error: {{ entry.error }}
{% endif %}
{% for section in entry.sections %}
{{ section.title }}:
{% if section["values"] %}
Values: {{ section["values"] | join(", ") }}
{% endif %}
{{ section.rows | table(("Option", "Type", "Default", "Description")) }}
{% endfor %}
{% endfor %}
