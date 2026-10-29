CREATE TRIGGER {{ (operation.index.name ~ "_update") | identifier }}
AFTER UPDATE OF {{ ((operation.index.columns | list) + ([operation.index.row_key] if operation.index.row_key else [])) | map("identifier") | join(", ") }} ON {{ operation.table | identifier }}
BEGIN
    INSERT INTO {{ operation.index.name | identifier }}
        ({{ operation.index.name | identifier }}, rowid, {{ operation.index.columns | map("identifier") | join(", ") }})
    VALUES
        ('delete', old.{{ (operation.index.row_key or "rowid") | identifier }}, {% for column in operation.index.columns %}old.{{ column | identifier }}{% if not loop.last %}, {% endif %}{% endfor %});
    INSERT INTO {{ operation.index.name | identifier }}
        (rowid, {{ operation.index.columns | map("identifier") | join(", ") }})
    VALUES
        (new.{{ (operation.index.row_key or "rowid") | identifier }}, {% for column in operation.index.columns %}new.{{ column | identifier }}{% if not loop.last %}, {% endif %}{% endfor %});
END
