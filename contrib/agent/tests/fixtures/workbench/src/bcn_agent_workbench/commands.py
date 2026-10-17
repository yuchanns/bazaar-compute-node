"""Provide the document tool through the same public command registration API."""

import asyncio
import json
import re
import sys
from contextlib import asynccontextmanager

import click
from bazaar_contrib_invoke import COMMANDS, CommandEntry
from bazaar_core import Context, Contrib


@asynccontextmanager
async def lifespan(ctx: Context):
    @click.group()
    def document():
        """Read a document from stdin and report its statistics."""

    @document.command()
    async def count():
        text = await asyncio.to_thread(sys.stdin.read)
        result = {
            "words": len(re.findall(r"\b\w+\b", text)),
            "lines": len(text.splitlines()),
        }
        await asyncio.to_thread(click.echo, json.dumps(result))

    with ctx.accept(COMMANDS).register(document):
        yield


command_entry = CommandEntry(contrib="document-cli", help="Inspect documents.")
commands = Contrib(accepts=[COMMANDS], lifespan=lifespan)
