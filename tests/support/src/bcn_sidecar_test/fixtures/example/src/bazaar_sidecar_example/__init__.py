"""A file journal and commands that consume only public sidecar contracts."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
lazy import asyncio
lazy import json

import click
from bazaar_contrib_invoke import COMMANDS, CommandEntry
from bazaar_contrib_sidecar import CLIENT, RemoteEntry
from bazaar_core import Context, Contrib, EventKey, ServiceKey
from pydantic import BaseModel
lazy import aiofiles
lazy from aiofiles.threadpool.text import AsyncTextIOWrapper

NOTE = EventKey[str]("journal.note")


class JournalConfig(BaseModel):
    path: Path
    tag: str = "journal"


class Journal:
    """Write to the journal file directly or through its note event."""

    def __init__(self, ctx: Context, handle: AsyncTextIOWrapper, tag: str) -> None:
        self.ctx = ctx
        self.handle = handle
        self.tag = tag

    async def write(self, text: str) -> None:
        await self.handle.write(text)
        await self.handle.flush()

    async def append(self, text: str) -> None:
        await self.ctx.announce(NOTE, text)

    async def wait(self, delay_ms: int) -> None:
        try:
            await asyncio.sleep(delay_ms / 1000)
        except asyncio.CancelledError:
            await self.write(f"wait {delay_ms} cancelled\n")
            raise


JOURNAL = ServiceKey[Journal]("journal.file")


@asynccontextmanager
async def journal_lifespan(ctx: Context, config: JournalConfig):
    async with aiofiles.open(config.path, "a", encoding="utf-8") as handle:
        journal = Journal(ctx, handle, config.tag)
        ctx.offer(JOURNAL, journal)
        await journal.write(f"{config.tag} started\n")
        try:
            yield
        finally:
            await journal.write(f"{config.tag} stopped\n")


async def note(ctx: Context, text: str):
    await ctx.accept(JOURNAL).write(text + "\n")


journal = Contrib(
    config=JournalConfig,
    offers=[JOURNAL],
    lifespan=journal_lifespan,
    listen={NOTE: note},
)
remote = RemoteEntry(JOURNAL, ("tag", "write", "append", "wait"))


@asynccontextmanager
async def control_lifespan(ctx: Context):
    client = ctx.accept(CLIENT)

    @click.group()
    def command():
        pass

    @command.command()
    def refresh():
        async def apply():
            _, snapshot = await client.call("configuration", "read")
            result = await client.call("configuration", "apply", snapshot)
            click.echo(json.dumps(result))

        return apply()

    @command.command()
    @click.argument("text")
    def append(text: str):
        return client.call("journal", "append", text)

    with ctx.accept(COMMANDS).register(command, "remote"):
        yield


controls = Contrib(accepts=[COMMANDS, CLIENT], lifespan=control_lifespan)
command_entry = CommandEntry(
    contrib="sidecar-controls", help="Use the running journal."
)
