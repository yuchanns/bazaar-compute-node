CREATE TRIGGER {{ (operation.index.name ~ "_ai") | identifier }}
AFTER INSERT ON {{ operation.table | identifier }}
BEGIN
    INSERT INTO {{ operation.index.name | identifier }}
        (rowid, {{ operation.index.columns | map("identifier") | join(", ") }})
    VALUES
        (new.{{ (operation.index.row_key or "rowid") | identifier }}, {% for column in operation.index.columns %}new.{{ column | identifier }}{% if not loop.last %}, {% endif %}{% endfor %});
END
