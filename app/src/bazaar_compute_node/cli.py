from __future__ import annotations

lazy import asyncio
lazy import signal
lazy import sys

import click

from .contrib import contrib
lazy from .boot import node


async def _run(configer: str, configer_options: tuple[str, ...]) -> None:
    loop = asyncio.get_running_loop()
    stopped = asyncio.Event()
    if sys.platform != "win32":
        loop.add_signal_handler(signal.SIGTERM, stopped.set)
    try:
        async with node(
            configer=configer,
            configer_options=configer_options,
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


main.add_command(contrib)


@main.command("run", help="Run the node.")
@click.option(
    "--configer",
    default="config-filesystem",
    show_default=True,
    help="Configuration contrib to use.",
)
@click.option(
    "--configer-options",
    multiple=True,
    metavar="OPTION",
    help="Option passed to the configuration contrib; may be repeated.",
)
@click.option(
    "--config",
    "configer_options",
    multiple=True,
    metavar="OPTION",
    help="Legacy alias for --configer-options.",
)
def run_command(configer: str, configer_options: tuple[str, ...]) -> None:
    try:
        asyncio.run(_run(configer, configer_options))
    except KeyboardInterrupt:
        pass
    except Exception as error:
        pending: list[BaseException] = [error]
        errors: list[str] = []
        while pending:
            error = pending.pop()
            if isinstance(error, BaseExceptionGroup):
                pending.extend(error.exceptions)
            else:
                errors.append(str(error))
        raise click.ClickException("; ".join(errors)) from error
