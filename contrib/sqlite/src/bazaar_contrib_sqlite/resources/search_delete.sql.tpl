CREATE TRIGGER {{ (operation.index.name ~ "_ad") | identifier }}
AFTER DELETE ON {{ operation.table | identifier }}
BEGIN
    INSERT INTO {{ operation.index.name | identifier }}
        ({{ operation.index.name | identifier }}, rowid, {{ operation.index.columns | map("identifier") | join(", ") }})
    VALUES
        ('delete', old.{{ (operation.index.row_key or "rowid") | identifier }}, {% for column in operation.index.columns %}old.{{ column | identifier }}{% if not loop.last %}, {% endif %}{% endfor %});
END
