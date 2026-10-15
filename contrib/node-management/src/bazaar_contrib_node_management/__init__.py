"""Register node activation and signal handling through the public command service."""

from __future__ import annotations

from contextlib import asynccontextmanager
lazy import asyncio
lazy import signal
lazy import sys

import click
from bazaar_contrib_config import CONFIGURATION
from bazaar_contrib_invoke import COMMANDS, CommandEntry
from bazaar_core import Contrib
lazy from bazaar_contrib_config import Configuration
lazy from bazaar_core import Context

__all__ = ["command_entry", "management"]


async def _run(configuration: Configuration) -> None:
    loop = asyncio.get_running_loop()
    stopped = asyncio.Event()
    if sys.platform != "win32":
        loop.add_signal_handler(signal.SIGTERM, stopped.set)
    try:
        await configuration.activate()
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
        return _run(configuration)

    with commands.register(run):
        yield


command_entry = CommandEntry(
    contrib="node-management",
    help="Run the node, serving until it is stopped.",
)
management = Contrib(accepts=[COMMANDS, CONFIGURATION], lifespan=lifespan)
