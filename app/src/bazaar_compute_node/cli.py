from __future__ import annotations

from contextlib import asynccontextmanager
lazy import asyncio
lazy import signal
lazy import sys

import click
lazy from bazaar_contrib_status import status
lazy from bazaar_core import (
    COMPOSITION,
    Composition,
    Context,
    Contrib,
    ContribEntry,
)

lazy from . import run


@asynccontextmanager
async def _load(ctx: Context):
    ctx.offer(
        COMPOSITION,
        Composition(entries=[ContribEntry(name="status", definition=status)]),
    )
    yield


async def _run() -> None:
    loop = asyncio.get_running_loop()
    stopped = asyncio.Event()
    if sys.platform != "win32":
        loop.add_signal_handler(signal.SIGTERM, stopped.set)
    try:
        async with run(
            Composition(),
            ContribEntry(
                name="node-composition",
                definition=Contrib(offers=[COMPOSITION], lifespan=_load),
            ),
        ):
            click.echo("bcn ready")
            await stopped.wait()
    finally:
        if sys.platform != "win32":
            loop.remove_signal_handler(signal.SIGTERM)


@click.group()
@click.version_option(package_name="bazaar-compute-node")
def main() -> None:
    """Run Bazaar Compute Node."""


@main.command("run", help="Run the node.")
def run_command() -> None:
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass
