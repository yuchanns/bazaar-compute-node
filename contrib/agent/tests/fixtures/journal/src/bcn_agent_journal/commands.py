"""Save reports with the actual invoking agent and session context."""

import asyncio
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import click
from bazaar_contrib_invoke import COMMANDS, CommandEntry
from bazaar_core import Context, Contrib


@asynccontextmanager
async def lifespan(ctx: Context):
    @click.group()
    def journal():
        """Save a report in the workspace."""

    @journal.command()
    @click.argument("path", type=click.Path(path_type=Path))
    @click.pass_obj
    async def save(context: dict[str, str], path: Path):
        body = await asyncio.to_thread(sys.stdin.read)
        data = json.loads(body)
        data["context"] = context
        await asyncio.to_thread(path.write_text, json.dumps(data) + "\n")
        await asyncio.to_thread(click.echo, f"Saved {path}")

    with ctx.accept(COMMANDS).register(journal):
        yield


command_entry = CommandEntry(contrib="journal-cli", help="Save document reports.")
commands = Contrib(accepts=[COMMANDS], lifespan=lifespan)
