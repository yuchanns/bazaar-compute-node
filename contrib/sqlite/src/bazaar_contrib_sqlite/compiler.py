"""Render SQLite statements from packaged templates and collect bound values."""

from __future__ import annotations

from functools import cache

import bazaar_contrib_storage as storage
from jinja2 import Environment, PackageLoader, StrictUndefined


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def literal(value: storage.Scalar) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bytes):
        return "X'" + value.hex() + "'"
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    if isinstance(value, bool):
        return str(int(value))
    return str(value)


@cache
def load_templates() -> Environment:
    """Load all packaged SQL before serving queries on the event loop."""
    templates = Environment(
        loader=PackageLoader("bazaar_contrib_sqlite", "resources"),
        undefined=StrictUndefined,
        autoescape=False,
        auto_reload=False,
        trim_blocks=False,
        lstrip_blocks=True,
    )
    templates.filters.update(identifier=quote, literal=literal)
    for name in templates.list_templates(extensions=["tpl"]):
        templates.get_template(name)
    return templates


class Compiler:
    def __init__(self, templates: Environment, *, inline: bool = False) -> None:
        self.templates = templates
        self.parameters: list[storage.Scalar] = []
        self.inline = inline

    def bind(self, value: storage.Scalar) -> str:
        if self.inline:
            return literal(value)
        self.parameters.append(value)
        return "?"

    def render(self, template: str, **context: object) -> str:
        return self.templates.get_template(template).render(bind=self.bind, **context)


def schema_sql(
    operation: storage.SchemaOperation, templates: Environment
) -> tuple[str, ...]:
    compiler = Compiler(templates, inline=True)
    if isinstance(operation, storage.CreateIndex) and operation.index.kind == "search":
        names = (
            "search_table.sql.tpl",
            "search_insert.sql.tpl",
            "search_delete.sql.tpl",
            "search_update.sql.tpl",
            "search_rebuild.sql.tpl",
        )
    else:
        names = (f"{operation.kind}.sql.tpl",)
    return tuple(compiler.render(name, operation=operation) for name in names)
