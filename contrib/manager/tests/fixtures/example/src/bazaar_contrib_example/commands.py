"""Independent contrib commands with their own text and real file operations."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
lazy import asyncio
lazy import json

import click
from bazaar_contrib_invoke import COMMANDS, CommandEntry
from bazaar_core import Context, Contrib


def example() -> click.Group:
    @click.group("example", help="Write a note using the example contrib.")
    def example():
        pass

    @example.command("write", help="Save text and repeated tags to a chosen file.")
    @click.argument("text")
    @click.option("--output", type=click.Path(path_type=Path), required=True)
    @click.option("--tag", multiple=True)
    async def write(text: str, output: Path, tag: tuple[str, ...]):
        payload = {"text": text, "tags": tag}
        await asyncio.to_thread(output.write_text, json.dumps(payload))
        await asyncio.to_thread(click.echo, json.dumps(payload))

    return example


@asynccontextmanager
async def lifespan(ctx: Context):
    command = await asyncio.to_thread(example)
    with ctx.accept(COMMANDS).register(command):
        yield


commands = Contrib(accepts=[COMMANDS], lifespan=lifespan)

command_entry = CommandEntry(
    contrib="example-commands",
    help="Write a note using the example contrib.",
)
