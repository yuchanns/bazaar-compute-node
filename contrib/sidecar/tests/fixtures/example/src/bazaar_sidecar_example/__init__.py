"""A file journal and commands that consume only public sidecar contracts."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
lazy import json

import click
from bazaar_contrib_invoke import COMMANDS, CommandEntry
from bazaar_core import Context, Contrib, EventKey, ServiceKey
from pydantic import BaseModel
lazy import aiofiles
lazy from aiofiles.threadpool.text import AsyncTextIOWrapper

from bazaar_contrib_sidecar import CLIENT, Symbol

NOTE = EventKey[str]("journal.note")
JOURNAL = ServiceKey[AsyncTextIOWrapper]("journal.file")


class JournalConfig(BaseModel):
    path: Path
    tag: str = "journal"


@asynccontextmanager
async def journal_lifespan(ctx: Context, config: JournalConfig):
    async with aiofiles.open(config.path, "a", encoding="utf-8") as handle:
        ctx.offer(JOURNAL, handle)
        await handle.write(f"{config.tag} started\n")
        await handle.flush()
        try:
            yield
        finally:
            await handle.write(f"{config.tag} stopped\n")
            await handle.flush()


async def note(ctx: Context, text: str):
    handle = ctx.accept(JOURNAL)
    await handle.write(text + "\n")
    await handle.flush()


journal = Contrib(
    config=JournalConfig,
    offers=[JOURNAL],
    lifespan=journal_lifespan,
    listen={NOTE: note},
)


@asynccontextmanager
async def control_lifespan(ctx: Context):
    remote = ctx.accept(CLIENT).context

    @click.group()
    def command():
        pass

    @command.command()
    def refresh():
        async def apply():
            configuration = await remote.call(
                "accept", Symbol("bazaar_contrib_config:CONFIGURATION")
            )
            _, snapshot = await configuration.call("read")
            result = await configuration.call("apply", snapshot)
            click.echo(json.dumps(await result.call("model_dump", mode="json")))

        return apply()

    @command.command()
    @click.argument("text")
    def append(text: str):
        return remote.call("announce", Symbol("bazaar_sidecar_example:NOTE"), text)

    with ctx.accept(COMMANDS).register(command, "remote"):
        yield


controls = Contrib(accepts=[COMMANDS, CLIENT], lifespan=control_lifespan)
command_entry = CommandEntry(
    contrib="sidecar-controls", help="Use the running journal."
)
