"""Public Click command registration and lifetime-bound dispatch."""

from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
lazy import asyncio
lazy import inspect
lazy from collections.abc import Collection, Iterator, Sequence
lazy from importlib.metadata import Distribution, entry_points
lazy from typing import Any, cast

import click
from bazaar_core import Context, Contrib, ServiceKey, discover
from pydantic import BaseModel, ConfigDict, Field

__all__ = ["COMMANDS", "CommandCatalog", "CommandEntry", "Commands", "commands"]


class CommandEntry(BaseModel):
    """An installed command name's contrib, loaded before its lifespan starts."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    contrib: str = Field(min_length=1)
    help: str = ""


class CommandCatalog(click.Group):
    """Invoke's own entry-point protocol, independent of the core discovery group."""

    def __init__(self, *, contribs: Collection[str], **options: Any) -> None:
        super().__init__(**options)
        packages = {source.package for source in discover() if source.name in contribs}
        for point in entry_points(group="bazaar_compute_node.commands"):
            if cast(Distribution, point.dist).metadata["Name"] not in packages:
                continue
            entry = CommandEntry.model_validate(point.load())
            if entry.contrib not in contribs:
                continue
            if point.name in self.commands:
                raise ValueError(
                    f"command entry {point.name!r} is declared more than once"
                )
            self.add_command(
                click.Command(
                    point.name,
                    help=entry.help,
                    short_help=entry.help,
                    callback=lambda entry=entry: entry,
                    add_help_option=False,
                    context_settings={
                        "allow_extra_args": True,
                        "ignore_unknown_options": True,
                    },
                )
            )

    def select(
        self, args: Sequence[str], *, prog_name: str | None
    ) -> CommandEntry | None:
        """Use native Click parsing to locate an owner without invoking business code."""
        try:
            with self.make_context(prog_name, list(args)) as ctx:
                return self.invoke(ctx)
        except click.exceptions.Exit as error:
            if error.exit_code:
                raise
            return None


class Commands(click.Group):
    """A composition's native Click group and owned command registrations."""

    def add_command(self, cmd: click.Command, name: str | None = None) -> None:
        name = name or cmd.name
        if name in self.commands:
            raise ValueError(f"command {name!r} is already registered")
        super().add_command(cmd, name)

    @contextmanager
    def register(
        self, command: click.Command, name: str | None = None
    ) -> Iterator[click.Command]:
        """Register a command until its owning lifespan exits."""
        name = name or command.name
        self.add_command(command, name)
        assert name is not None
        try:
            yield command
        finally:
            if self.commands.get(name) is command:
                del self.commands[name]

    async def dispatch(
        self, args: Sequence[str], *, prog_name: str | None, **context: Any
    ) -> Any:
        """Parse on a worker and await business callbacks on the owning loop."""

        def invoke():
            with self.make_context(prog_name, list(args), **context) as ctx:
                return self.invoke(ctx)

        result = await asyncio.to_thread(invoke)
        if inspect.isawaitable(result):
            return await result
        return result


COMMANDS = ServiceKey[Commands]("invoke.commands")


@asynccontextmanager
async def lifespan(ctx: Context):
    ctx.offer(COMMANDS, Commands())
    yield


commands = Contrib(offers=[COMMANDS], lifespan=lifespan)
