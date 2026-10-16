{% macro expression(node) -%}
{% if node.kind == "column" %}
    {% if node.table %}{{ node.table | identifier }}.{% endif %}{{ node.name | identifier }}
{% elif node.kind == "value" %}
    {{ bind(node.value) }}
{% elif node.kind == "binary" %}
    ({{ expression(node.left) }} {{ node.operator }} {{ expression(node.right) }})
{% elif node.kind == "row_number" %}
    ROW_NUMBER() OVER (ORDER BY
    {% for sort in node.order_by %}
        {{ expression(sort.expression) }} {{ "DESC" if sort.descending else "ASC" }} NULLS {{ sort.nulls | upper }}{{ ", " if not loop.last else "" }}
    {% endfor %}
    )
{% elif node.kind == "call" %}
    {{ node.name | upper }}(
    {% for argument in node.arguments %}
        {{ expression(argument) }}{{ ", " if not loop.last else "" }}
    {% else %}
        {% if node.name == "count" %}*{% endif %}
    {% endfor %}
    )
{% endif %}
{%- endmacro %}

{% macro match_query(node, term) -%}
{ {{ node.columns | join(" ") }} } : "{{ term | replace('"', '""') }}"
{%- endmacro %}

{% macro predicate(node) -%}
{% if node.kind == "compare" %}
    ({{ expression(node.left) }} {{ node.operator }} {{ expression(node.right) }})
{% elif node.kind == "in" %}
    {% if node.values %}
        ({{ expression(node.expression) }} {% if node.exclude %}NOT {% endif %}IN (
        {% for value in node.values %}
            {{ bind(value) }}{{ ", " if not loop.last else "" }}
        {% endfor %}
        ))
    {% else %}
        {{ 1 if node.exclude else 0 }}
    {% endif %}
{% elif node.kind == "null" %}
    ({{ expression(node.expression) }} IS {% if node.exclude %}NOT {% endif %}NULL)
{% elif node.kind == "contains" %}
    (instr(lower({{ expression(node.expression) }}), lower({{ bind(node.text) }})) > 0)
{% elif node.kind in ("all", "any") %}
    {% if node.conditions %}
        (
        {% for condition in node.conditions %}
            {{ predicate(condition) }}
            {% if not loop.last %}{{ "AND" if node.kind == "all" else "OR" }}{% endif %}
        {% endfor %}
        )
    {% else %}
        {{ 1 if node.kind == "all" else 0 }}
    {% endif %}
{% elif node.kind == "not" %}
    (NOT {{ predicate(node.condition) }})
{% elif node.kind == "search" %}
    (
    {% for term in node.terms %}
        {% if term | length >= 3 %}
            {{ node.table | identifier }}.{{ (node.row_key or "rowid") | identifier }} IN (
                SELECT rowid FROM {{ node.index | identifier }}
                WHERE {{ node.index | identifier }} MATCH {{ bind(match_query(node, term)) }}
            )
        {% else %}
            (
            {% for column in node.columns %}
                instr(
                    lower({{ node.table | identifier }}.{{ column | identifier }}),
                    lower({{ bind(term) }})
                ) > 0
                {% if not loop.last %}OR{% endif %}
            {% endfor %}
            )
        {% endif %}
        {% if not loop.last %}AND{% endif %}
    {% endfor %}
    )
{% endif %}
{%- endmacro %}

{% macro column_definition(column) -%}

{{ column.name | identifier }} {{ column.type | upper }}
{% if column.generated %}PRIMARY KEY AUTOINCREMENT{% endif %}
{% if not column.nullable %}NOT NULL{% endif %}
{% if column.default is not none %}DEFAULT {{ column.default | literal }}{% endif %}
{% if column.unique %}UNIQUE{% endif %}
{% if column.check is not none %}CHECK ({{ predicate(column.check) }}){% endif %}
{% if column.references is not none %}
REFERENCES {{ column.references.table | identifier }} ({{ column.references.column | identifier }}) ON DELETE {{ column.references.on_delete | upper }}
{% endif %}
{%- endmacro %}

{% macro returning(columns) %}
{% if columns %}
RETURNING {{ columns | map("identifier") | join(", ") }}
{% endif %}
{% endmacro %}
