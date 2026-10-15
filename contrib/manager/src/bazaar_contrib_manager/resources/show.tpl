Name: {{ package.package }}
Version: {{ package.version }}
Summary: {{ package.summary }}
Requires: {{ package.requirements | join(", ") }}
{% if package.defaults %}
Default contribs: {{ package.defaults.get("contribs", ()) | map(attribute="contrib") | join(", ") }}
{% endif %}
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
