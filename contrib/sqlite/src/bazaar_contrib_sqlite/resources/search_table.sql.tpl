CREATE VIRTUAL TABLE {{ operation.index.name | identifier }}
USING fts5(
    {{ operation.index.columns | map("identifier") | join(", ") }},
    content={{ operation.table | literal }},
    content_rowid={{ (operation.index.row_key or "rowid") | literal }},
    tokenize='trigram'
)
