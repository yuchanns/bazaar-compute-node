{% from "fragments.sql.tpl" import predicate with context %}
CREATE TRIGGER {{ operation.name | identifier }} AFTER {{ operation.event | upper }} ON {{ operation.table | identifier }}
{% if operation.when is not none %}WHEN {{ predicate(operation.when) }}{% endif %}
BEGIN
    {% set request = operation.action %}
    {% include "update.sql.tpl" %};
END
