"""Public Click command registration and lifetime-bound dispatch."""

from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
lazy import asyncio
lazy import copy
lazy import inspect
lazy from collections.abc import Awaitable, Callable, Collection, Iterator, Sequence
lazy from concurrent.futures import CancelledError as FutureCancelledError
lazy from functools import partial
lazy from importlib.metadata import Distribution, entry_points
lazy from importlib.resources import files
lazy from string import Template
lazy from threading import get_ident
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


class CommandLoadError(click.ClickException):
    def __init__(self, command: str, package: str, reason: Exception):
        super().__init__(str(reason))
        self.command = command
        self.package = package

    def show(self, file=None, *, level: str = "error") -> None:
        template = Template(
            files(__package__).joinpath("resources/command-error.tpl").read_text()
        )
        click.echo(
            template.substitute(
                level=level,
                command=self.command,
                package=self.package,
                reason=self.message,
            ).rstrip(),
            file=file,
            err=file is None,
        )


class CommandCatalog(click.Group):
    """Invoke's own entry-point protocol, independent of the core discovery group."""

    def __init__(
        self,
        *,
        contribs: Collection[str],
        entry_point_group: str = "bazaar_compute_node.commands",
        **options: Any,
    ) -> None:
        super().__init__(**options)
        self.failures: dict[str, CommandLoadError] = {}
        packages = {source.package for source in discover() if source.name in contribs}
        for point in entry_points(group=entry_point_group):
            if cast(Distribution, point.dist).metadata["Name"] not in packages:
                continue
            try:
                entry = point.load()
                if entry is None:
                    continue
                entry = CommandEntry.model_validate(entry)
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
            except Exception as error:  # noqa: BLE001 - A broken command entry must not disable healthy commands.
                self.failures[point.name] = CommandLoadError(
                    point.name, cast(Distribution, point.dist).metadata["Name"], error
                )
                self.commands.pop(point.name, None)

    def get_command(self, ctx: click.Context, name: str) -> click.Command | None:
        if name in self.failures:
            raise self.failures[name]
        return super().get_command(ctx, name)

    def select(
        self, args: Sequence[str], *, prog_name: str | None
    ) -> CommandEntry | None:
        """Use native Click parsing to locate an owner without invoking business code."""
        failure = None
        try:
            with self.make_context(prog_name, list(args)) as ctx:
                return self.invoke(ctx)
        except CommandLoadError as error:
            failure = error
            raise
        except click.exceptions.Exit as error:
            if error.exit_code:
                raise
            return None
        finally:
            for error in self.failures.values():
                if error is not failure:
                    error.show(level="warning")


class Commands(click.Group):
    """A composition's native Click group and owned command registrations."""

    def __init__(self, *args: Any, **options: Any) -> None:
        super().__init__(*args, **options)
        self._lock = asyncio.Lock()

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

        async with self._lock:
            loop = asyncio.get_running_loop()
            thread = get_ident()
            callbacks: set[asyncio.Task[Any]] = set()
            cancelled = False

            async def wait(result: Awaitable[Any], ctx: click.Context):
                task = cast(asyncio.Task[Any], asyncio.current_task())
                callbacks.add(task)
                try:
                    if cancelled:
                        task.cancel()
                    with ctx.scope(cleanup=False):
                        return await result
                finally:
                    callbacks.discard(task)

            def call(callback: Callable[..., Any], *args: Any, **kwargs: Any):
                result = callback(*args, **kwargs)
                if not inspect.isawaitable(result):
                    return result
                future = asyncio.run_coroutine_threadsafe(
                    wait(result, click.get_current_context()), loop
                )
                try:
                    return future.result()
                except FutureCancelledError:
                    raise asyncio.CancelledError from None

            class InvocationContext(click.Context):
                def invoke(
                    self,
                    callback: click.Command | Callable[..., Any],
                    /,
                    *args: Any,
                    **kwargs: Any,
                ) -> Any:
                    if get_ident() == thread:
                        raise RuntimeError(
                            "Async command callbacks cannot invoke or forward Click "
                            "callbacks; call the shared service directly."
                        )
                    if isinstance(callback, click.Command):
                        callback = copy.copy(callback)
                        if callback.callback is not None:
                            callback.callback = partial(call, callback.callback)
                    else:
                        callback = partial(call, callback)
                    return super().invoke(callback, *args, **kwargs)

            def bind(command: click.Command) -> click.Command:
                command = copy.copy(command)
                command.context_class = InvocationContext
                if isinstance(command, click.Group):
                    command.commands = {
                        name: bind(child) for name, child in command.commands.items()
                    }
                return command

            def invoke():
                command = bind(self)
                with command.make_context(prog_name, list(args), **context) as ctx:
                    return command.invoke(ctx)

            worker = asyncio.create_task(asyncio.to_thread(invoke))
            try:
                return await asyncio.shield(worker)
            except asyncio.CancelledError as error:
                cancelled = True
                for task in callbacks:
                    task.cancel()
                while not worker.done():
                    try:
                        await asyncio.shield(worker)
                    except asyncio.CancelledError:
                        pass
                    except BaseException:  # noqa: BLE001 - Read the worker failure below and preserve the caller's cancellation.
                        break
                if (
                    not worker.cancelled()
                    and (cleanup := worker.exception()) is not None
                ):
                    error.add_note(f"command cleanup failed: {cleanup!r}")
                raise


COMMANDS = ServiceKey[Commands]("invoke.commands")


@asynccontextmanager
async def lifespan(ctx: Context):
    ctx.offer(COMMANDS, Commands())
    yield


commands = Contrib(offers=[COMMANDS], lifespan=lifespan)
