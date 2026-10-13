"""Local commands consuming the contrib's shared management services."""

from __future__ import annotations

from contextlib import asynccontextmanager
lazy import asyncio
lazy import json
lazy from collections.abc import Awaitable, Callable
lazy from typing import Any
lazy from uuid import UUID

import click
from pydantic import BaseModel, Field, ValidationError
lazy from bazaar_contrib_manager import CONTRIB_MANAGER, ContribManager, Operation
lazy from bazaar_core import Composition, Context, Contrib, ContribEntry

lazy from . import run
lazy from .boot import bootstrap


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
    """Expand repeated flat options into the plugin's ordinary parameter model."""
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
                value = [] if names[index + 1].isdecimal() else {}
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


async def invoke(
    options: dict[str, Any], action: Callable[[ContribManager], Awaitable[Any]]
) -> Any:
    entries, loader = await asyncio.to_thread(bootstrap, management=True, **options)
    selected: dict[str, ContribManager] = {}

    @asynccontextmanager
    async def lifespan(ctx: Context):
        selected["manager"] = ctx.accept(CONTRIB_MANAGER)
        yield

    async with (
        run(entries, loader) as hub,
        hub.enable(
            Composition(
                entries=[
                    ContribEntry(
                        name="contrib-command",
                        definition=Contrib(
                            accepts=[CONTRIB_MANAGER], lifespan=lifespan
                        ),
                    )
                ]
            )
        ),
    ):
        if "manager" not in selected:
            raise click.ClickException(
                "enable contrib-manager in the node configuration"
            )
        return await action(selected["manager"])


def execute(
    ctx: click.Context, action: Callable[[ContribManager], Awaitable[Any]]
) -> None:
    try:
        result = asyncio.run(invoke(ctx.find_root().obj or ctx.obj, action))
    except Exception as error:
        raise click.ClickException(str(error)) from error
    if isinstance(result, Operation):
        click.echo(result.model_dump_json(indent=2))
        if result.phase != "complete":
            raise click.ClickException(result.error or "management operation failed")
    elif isinstance(result, tuple):
        click.echo(
            json.dumps([item.model_dump(mode="json") for item in result], indent=2)
        )
    elif hasattr(result, "model_dump_json"):
        click.echo(result.model_dump_json(indent=2))
    else:
        click.echo(json.dumps(result, indent=2))


@click.group("contrib", help="Manage contrib packages and their configuration.")
@click.option("--configer", default="config-filesystem", show_default=True)
@click.option("--configer-options", multiple=True, metavar="OPTION")
@click.option(
    "--config",
    "configer_options",
    multiple=True,
    metavar="OPTION",
    help="Legacy alias for --configer-options.",
)
@click.pass_context
def contrib(
    ctx: click.Context, configer: str, configer_options: tuple[str, ...]
) -> None:
    ctx.obj = {"configer": configer, "configer_options": configer_options}


@contrib.command("list", help="List installed contrib packages and their entries.")
@click.pass_context
def list_command(ctx: click.Context) -> None:
    execute(ctx, lambda manager: manager.installed())


@contrib.command(
    "show", help="Show an installed package's entries and configuration models."
)
@click.argument("name")
@click.pass_context
def show(ctx: click.Context, name: str) -> None:
    execute(ctx, lambda manager: manager.detail(name))


@contrib.command("entries", help="List all packages in the configured indexes.")
@click.pass_context
def entries(ctx: click.Context) -> None:
    execute(ctx, lambda manager: manager.catalog.entries())


@contrib.group("index", help="Manage configured index locations.")
def index() -> None:
    pass


@index.command("list", help="Read every configured index and show its cache status.")
@click.pass_context
def index_list(ctx: click.Context) -> None:
    execute(ctx, lambda manager: manager.catalog.indexes())


@index.command("add")
@click.argument("location")
@click.pass_context
def index_add(ctx: click.Context, location: str) -> None:
    execute(ctx, lambda manager: manager.index(location))


@index.command("remove")
@click.argument("location")
@click.pass_context
def index_remove(ctx: click.Context, location: str) -> None:
    execute(ctx, lambda manager: manager.index(location, remove=True))


@contrib.command(
    "add", help="Install a package; optionally import its enabled combination."
)
@click.argument("name")
@click.option("--source", help="Select the Python package source.")
@click.option("--version", default="", help="Version constraint, such as >=1,<2.")
@click.option("--enable", is_flag=True)
@click.option("--entry", help="Select an entry from a package with multiple entries.")
@click.option(
    "--option",
    "options",
    type=PARAMETERS,
    multiple=True,
    metavar="KEY=VALUE",
    help="Plugin parameter; repeat for more fields or list values. Use dotted fields for nested values.",
)
@click.pass_context
def add(
    ctx: click.Context,
    name: str,
    source: str | None,
    version: str,
    enable: bool,
    entry: str | None,
    options: tuple[Option, ...],
) -> None:
    config = parameters(options) if options else None

    async def action(manager: ContribManager):
        package = await manager.catalog.select(name, source=source, version=version)
        return await manager.add(package, enable=enable, contrib=entry, config=config)

    execute(ctx, action)


@contrib.command(
    "remove", help="Uninstall a package after validating the remaining combination."
)
@click.argument("name")
@click.pass_context
def remove(ctx: click.Context, name: str) -> None:
    async def action(manager: ContribManager):
        package = await manager.detail(name)
        return await manager.remove(package.package)

    execute(ctx, action)


@contrib.command(
    "enable", help="Save a package's enabled combination or selected entry."
)
@click.argument("name")
@click.option("--entry")
@click.option(
    "--option",
    "options",
    type=PARAMETERS,
    multiple=True,
    metavar="KEY=VALUE",
    help="Plugin parameter; repeat for more fields or list values.",
)
@click.pass_context
def enable(
    ctx: click.Context, name: str, entry: str | None, options: tuple[Option, ...]
) -> None:
    config = parameters(options) if options else None
    execute(ctx, lambda manager: manager.enable(name, contrib=entry, config=config))


@contrib.command(
    "configured", help="List configured entries with zero-based positions."
)
@click.pass_context
def configured(ctx: click.Context) -> None:
    async def action(manager: ContribManager):
        candidate, _ = await manager.configuration.read()
        return [
            {"index": index, **entry}
            for index, entry in enumerate(candidate.payload["contribs"])
        ]

    execute(ctx, action)


@contrib.command("edit", help="Update parameters of a configured entry.")
@click.argument("index", type=click.IntRange(min=0))
@click.option(
    "--option",
    "options",
    type=PARAMETERS,
    multiple=True,
    required=True,
    metavar="KEY=VALUE",
    help="Plugin parameter; repeat for more fields or list values.",
)
@click.pass_context
def edit(ctx: click.Context, index: int, options: tuple[Option, ...]) -> None:
    execute(ctx, lambda manager: manager.edit(index, parameters(options)))


@contrib.command(
    "disable", help="Remove a configured entry by its zero-based position."
)
@click.argument("index", type=click.IntRange(min=0))
@click.pass_context
def disable(ctx: click.Context, index: int) -> None:
    execute(ctx, lambda manager: manager.disable(index))


@contrib.command(
    "upgrade", help="Upgrade the node and retain every recorded package source."
)
@click.argument("version")
@click.pass_context
def upgrade(ctx: click.Context, version: str) -> None:
    execute(ctx, lambda manager: manager.upgrade(version))


@contrib.command("operation", help="Read a management operation's persisted result.")
@click.argument("id", type=click.UUID)
@click.pass_context
def operation(ctx: click.Context, id: UUID) -> None:
    execute(ctx, lambda manager: manager.operation(id))
