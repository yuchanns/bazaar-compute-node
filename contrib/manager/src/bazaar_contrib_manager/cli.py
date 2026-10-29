"""Manager-owned CLI definitions, inputs, and output."""

from __future__ import annotations

lazy import asyncio
lazy from collections.abc import Awaitable, Callable, Iterable
lazy from typing import Any
lazy from uuid import UUID

import click
from jinja2 import Environment, PackageLoader, StrictUndefined
from pydantic import BaseModel, Field, ValidationError

from .contracts import InstalledPackage, Operation, Positions
from .service import ContribManager


class Option(BaseModel):
    name: str = Field(
        pattern=r"^[A-Za-z_][A-Za-z0-9_-]*(?:\.(?:[A-Za-z_][A-Za-z0-9_-]*|[0-9]+))*$"
    )
    value: str


class Parameters(click.ParamType):
    name = "KEY=VALUE"

    def convert(self, value, param, ctx):
        try:
            name, separator, value = value.partition("=")
            if not separator:
                self.fail("use KEY=VALUE", param, ctx)
            return Option(name=name, value=value)
        except ValidationError as error:
            self.fail(str(error), param, ctx)


PARAMETERS = Parameters()


def parameters(options: tuple[Option, ...]) -> dict[str, Any]:
    """Expand repeated flat options into the contrib's ordinary parameter model."""
    result: dict[str, Any] = {}
    for option in options:
        names = option.name.split(".")
        current: Any = result
        for index, name in enumerate(names):
            if isinstance(current, list):
                if not name.isdecimal():
                    raise click.BadParameter(
                        "use a numeric list position", param_hint="--option"
                    )
                while len(current) <= int(name):
                    current.append(None)
                key = int(name)
                value = current[int(name)]
            else:
                key = name
                value = current.get(name)
            if index == len(names) - 1:
                value = (
                    option.value
                    if value is None
                    else [*value, option.value]
                    if isinstance(value, list)
                    else [value, option.value]
                )
            elif value is None:
                value = Positions() if names[index + 1].isdecimal() else {}
            elif not isinstance(value, (dict, list)):
                raise click.BadParameter(
                    "a parameter cannot also contain nested fields",
                    param_hint="--option",
                )
            if isinstance(current, list):
                current[int(key)] = value
            else:
                current[str(key)] = value
            current = value
    return result


def table(rows: Iterable[tuple[str, ...]], headers: tuple[str, ...]) -> str:
    rows = list(rows)
    if not rows:
        return ""
    widths = [max(map(len, column)) for column in zip(headers, *rows, strict=True)]
    return "\n".join(
        " ".join(
            value.ljust(width) for value, width in zip(row, widths, strict=True)
        ).rstrip()
        for row in (headers, tuple("-" * width for width in widths), *rows)
    )


async def execute[T](
    manager: ContribManager,
    action: Callable[[ContribManager], Awaitable[T]],
    render: Callable[[T], str],
    *,
    err: bool = False,
) -> None:
    try:
        result = await action(manager)
    except Exception as error:
        raise click.ClickException(str(error)) from error

    output = (await asyncio.to_thread(render, result)).rstrip("\n")
    if output:
        await asyncio.to_thread(click.echo, output, err=err)
    if isinstance(result, Operation) and result.phase != "complete":
        raise click.ClickException(result.error or "management operation failed")


def create_commands(manager: ContribManager) -> click.Group:
    """Create the contrib command group with callbacks bound to the manager."""
    templates = Environment(
        loader=PackageLoader("bazaar_contrib_manager", "resources"),
        undefined=StrictUndefined,
        autoescape=False,
        auto_reload=False,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    templates.filters["table"] = table

    def outcome(result: Operation, **details: object) -> str:
        return templates.get_template("outcome.tpl").render(
            result=result, details=details
        )

    @click.group("contrib", help="Manage contribs")
    def contrib():
        pass

    @contrib.command("sync", help="Update the contrib environment")
    @click.option("--upgrade", is_flag=True, help="Upgrade package versions")
    def sync(upgrade: bool):
        return execute(
            manager,
            lambda manager: manager.sync(upgrade=upgrade),
            outcome,
            err=True,
        )

    @contrib.command("list", help="List installed packages")
    def list_command():
        return execute(
            manager,
            lambda manager: manager.installed(),
            lambda packages: templates.get_template("list.tpl").render(
                rows=((package.package, package.version) for package in packages)
            ),
        )

    @contrib.command("show", help="Show information about an installed package")
    @click.argument("name", metavar="PACKAGE")
    def show(name: str):
        def render(package: InstalledPackage) -> str:
            entries: list[dict[str, Any]] = []
            for entry in package.entries:
                sections = []
                if entry.config_schema:
                    for title, schema in (
                        ("Options", entry.config_schema),
                        *entry.config_schema.get("$defs", {}).items(),
                    ):
                        sections.append(
                            {
                                "title": title,
                                "values": schema.get("enum", ()),
                                "rows": [
                                    (
                                        name,
                                        " | ".join(
                                            field.get("type")
                                            or field.get("$ref", "value").rsplit(
                                                "/", 1
                                            )[-1]
                                            for field in option.get("anyOf", (option,))
                                        ),
                                        "required"
                                        if name in schema.get("required", ())
                                        else str(option.get("default", "")),
                                        option.get("description", ""),
                                    )
                                    for name, option in schema.get(
                                        "properties", {}
                                    ).items()
                                ],
                            }
                        )
                entries.append(
                    {"name": entry.name, "error": entry.error, "sections": sections}
                )
            return templates.get_template("show.tpl").render(
                package=package, entries=entries
            )

        return execute(manager, lambda manager: manager.detail(name), render)

    @contrib.command("entries", help="List available packages")
    def entries():
        return execute(
            manager,
            lambda manager: manager.catalog.entries(),
            lambda packages: templates.get_template("entries.tpl").render(
                rows=((package.package, package.source) for package in packages),
            ),
        )

    @contrib.group("index", help="Manage contrib indexes")
    def index():
        pass

    @index.command("list", help="List configured contrib indexes")
    def index_list():
        return execute(
            manager,
            lambda manager: manager.catalog.indexes(),
            lambda indexes: templates.get_template("indexes.tpl").render(
                rows=(
                    (
                        index.location,
                        str(len(index.entries)),
                        f"cached ({index.error})"
                        if index.cached and index.error
                        else "cached"
                        if index.cached
                        else index.error or "ready",
                    )
                    for index in indexes
                ),
            ),
        )

    @index.command("add", help="Add a contrib index")
    @click.argument("location")
    def index_add(location: str):
        return execute(
            manager,
            lambda manager: manager.index(location),
            lambda result: outcome(result, location=location),
            err=True,
        )

    @index.command("remove", help="Remove a contrib index")
    @click.argument("location")
    def index_remove(location: str):
        return execute(
            manager,
            lambda manager: manager.index(location, remove=True),
            lambda result: outcome(result, location=location),
            err=True,
        )

    @contrib.command("add", help="Install a contrib package")
    @click.argument("name", metavar="PACKAGE")
    @click.option("--source", metavar="INDEX", help="Use the given package index")
    @click.option(
        "--version",
        default="",
        metavar="CONSTRAINT",
        help="Constrain the package version",
    )
    @click.option(
        "--enable",
        is_flag=False,
        flag_value="",
        default=None,
        metavar="[ENTRY]",
        help="Enable one or all contribs",
    )
    @click.option(
        "--option",
        "options",
        type=PARAMETERS,
        multiple=True,
        metavar="KEY=VALUE",
        help="Set a contrib option",
    )
    def add(
        name: str,
        source: str | None,
        version: str,
        enable: str | None,
        options: tuple[Option, ...],
    ):
        config = parameters(options) if options else None

        async def action(manager: ContribManager):
            package = await manager.catalog.select(name, source=source, version=version)
            return await manager.add(
                package,
                enable=enable is not None,
                contrib=enable or None,
                config=config,
            )

        return execute(
            manager,
            action,
            lambda result: outcome(
                result, name=name, enable=enable is not None, entry=enable
            ),
            err=True,
        )

    @contrib.command("remove", help="Uninstall a contrib package")
    @click.argument("name", metavar="PACKAGE")
    def remove(name: str):
        async def action(manager: ContribManager):
            package = await manager.detail(name)
            return await manager.remove(package.package)

        return execute(manager, action, outcome, err=True)

    @contrib.command("enable", help="Enable contribs from an installed package")
    @click.argument("name", metavar="PACKAGE")
    @click.option("--entry", metavar="CONTRIB", help="Enable the specified contrib")
    @click.option(
        "--option",
        "options",
        type=PARAMETERS,
        multiple=True,
        metavar="KEY=VALUE",
        help="Set a contrib option",
    )
    def enable(name: str, entry: str | None, options: tuple[Option, ...]):
        config = parameters(options) if options else None
        return execute(
            manager,
            lambda manager: manager.enable(name, contrib=entry, config=config),
            lambda result: outcome(result, name=name, entry=entry),
            err=True,
        )

    @contrib.command("configured", help="List configured contribs")
    def configured():
        async def action(manager: ContribManager):
            candidate, _ = await manager.configuration.read()
            rows = []
            index = 0
            for package in candidate.payload.get("contribs", ()):
                for entry in package.get("enabled", ()) or ({},):
                    options = []
                    pending = list(entry.get("config", {}).items())
                    while pending:
                        name, value = pending.pop(0)
                        if isinstance(value, (dict, list)) and value:
                            items = (
                                value.items()
                                if isinstance(value, dict)
                                else enumerate(value)
                            )
                            pending[0:0] = [
                                (f"{name}.{key}", value) for key, value in items
                            ]
                        else:
                            options.append(f"{name}={value}")
                    rows.append(
                        (
                            str(index) if entry else "-",
                            entry.get("entry") or "(installed only)",
                            package.get("package", ""),
                            package.get("source", ""),
                            ", ".join(options),
                        )
                    )
                    if entry:
                        index += 1
            return rows

        return execute(
            manager,
            action,
            lambda rows: templates.get_template("configured.tpl").render(rows=rows),
        )

    @contrib.command("edit", help="Update contrib options")
    @click.argument("index", type=click.IntRange(min=0))
    @click.option(
        "--option",
        "options",
        type=PARAMETERS,
        multiple=True,
        required=True,
        metavar="KEY=VALUE",
        help="Set a contrib option",
    )
    def edit(index: int, options: tuple[Option, ...]):
        return execute(
            manager,
            lambda manager: manager.edit(index, parameters(options)),
            lambda result: outcome(result, index=index),
            err=True,
        )

    @contrib.command("disable", help="Disable a contrib")
    @click.argument("index", type=click.IntRange(min=0))
    @click.option(
        "--force",
        is_flag=True,
        help="Disable the contrib and stop dependent contribs",
    )
    def disable(index: int, force: bool):
        return execute(
            manager,
            lambda manager: manager.disable(index, force=force),
            lambda result: outcome(result, index=index),
            err=True,
        )

    @contrib.command("upgrade", help="Upgrade Bazaar Compute Node")
    @click.argument("version")
    def upgrade(version: str):
        return execute(
            manager, lambda manager: manager.upgrade(version), outcome, err=True
        )

    @contrib.command("operation", help="Show operation details")
    @click.argument("id", type=click.UUID)
    def operation(id: UUID):
        return execute(
            manager,
            lambda manager: manager.operation(id),
            lambda result: templates.get_template("operation.tpl").render(
                result=result, details={}
            ),
        )

    return contrib
