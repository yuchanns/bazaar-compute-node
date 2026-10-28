"""Register node activation and signal handling through the public command service."""

from __future__ import annotations

from contextlib import asynccontextmanager
lazy import asyncio
lazy import platform
lazy import re
lazy import signal
lazy import sys
lazy from importlib.metadata import version
lazy from typing import cast

import click
from bazaar_contrib_audit import AUDIT, AuditContext
from bazaar_contrib_config import CONFIGURATION
from bazaar_contrib_invoke import COMMANDS, CommandEntry, table
from bazaar_contrib_timer import TIMER
from bazaar_core import Contrib
lazy from bazaar_contrib_config import ConfigCandidate, ConfigDocument, Configuration
lazy from bazaar_core import Context, ContribStatus, Stopping
lazy from packaging.requirements import Requirement

__all__ = ["command_entry", "management", "node_health"]


def _contribs(
    configuration: Configuration, snapshots: tuple[ContribStatus, ...]
) -> list[tuple[str, str, str, str]]:
    """Each configured contrib's package, state and what it ended with."""
    packages = [
        Requirement(package.package).name if package.package else ""
        for package in ConfigDocument.model_validate(
            cast(ConfigCandidate, configuration.candidate).payload
        ).contribs
    ]
    rows = []
    for record in snapshots:
        match = re.fullmatch(r"contribs\[(\d+)\]\.enabled\[\d+\]", record.location)
        if match is None:
            continue
        error = record.state.error if isinstance(record.state, Stopping) else None
        rows.append(
            (
                record.name,
                packages[int(match[1])],
                type(record.state).__name__.lower(),
                "" if error is None else str(error).splitlines()[0],
            )
        )
    return rows


@asynccontextmanager
async def health(ctx: Context):
    """Publish the node's health once ready and then every startup budget, as 0.2 did.

    Two beats missed tell a consumer the node is gone."""
    configuration = ctx.accept(CONFIGURATION)
    audit = ctx.accept(AUDIT)
    timer = ctx.accept(TIMER)

    async def beat() -> None:
        while True:
            await audit.publish(
                "node.health",
                context=AuditContext(agent_id=""),
                payload={
                    "contribs": [
                        dict(
                            zip(("name", "package", "state", "error"), row, strict=True)
                        )
                        for row in _contribs(configuration, ctx.snapshots)
                    ],
                    "version": await asyncio.to_thread(version, "bazaar-compute-node"),
                    "python": sys.version.split()[0],
                    "system": platform.system(),
                    "gil_enabled": sys._is_gil_enabled(),
                    "interval_ms": 60_000,
                },
            )
            await timer.create(60_000).wait()

    async with asyncio.TaskGroup() as tasks:
        task = tasks.create_task(beat())
        try:
            yield
        finally:
            task.cancel()


async def _run(ctx: Context, configuration: Configuration) -> None:
    loop = asyncio.get_running_loop()
    stopped = asyncio.Event()
    if sys.platform != "win32":
        loop.add_signal_handler(signal.SIGTERM, stopped.set)
    try:
        await configuration.activate()
        await asyncio.to_thread(
            click.echo,
            table(
                _contribs(configuration, ctx.snapshots),
                ("Contrib", "Package", "State", "Result"),
            ),
        )
        await asyncio.to_thread(click.echo, "bcn ready")
        await stopped.wait()
    finally:
        if sys.platform != "win32":
            loop.remove_signal_handler(signal.SIGTERM)


@asynccontextmanager
async def lifespan(ctx: Context):
    commands = ctx.accept(COMMANDS)
    configuration = ctx.accept(CONFIGURATION)

    @click.command("run", help=command_entry.help, short_help=command_entry.help)
    def run():
        return _run(ctx, configuration)

    with commands.register(run):
        yield


command_entry = CommandEntry(
    contrib="node-management",
    help="Run the node, serving until it is stopped.",
)
management = Contrib(accepts=[COMMANDS, CONFIGURATION], lifespan=lifespan)
node_health = Contrib(accepts=[CONFIGURATION, AUDIT, TIMER], lifespan=health)
