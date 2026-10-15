"""Exercise native commands, asynchronous files, and owned registrations."""

from __future__ import annotations

from contextlib import asynccontextmanager
lazy import asyncio
lazy from pathlib import Path
lazy from uuid import uuid4

import click
from bazaar_core import (
    Composition,
    Context,
    Contrib,
    ContribEntry,
    ContribHub,
)

from bazaar_contrib_invoke import COMMANDS, Commands, commands


async def test_command_lifetimes(tmp_path: Path):
    name = f"write-{uuid4().hex}"
    help = uuid4().hex
    selected: list[Commands] = []
    values = [uuid4().hex for _ in range(3)]
    path = tmp_path / "output.txt"

    @asynccontextmanager
    async def writer(ctx: Context):

        @click.command(name, help=help)
        @click.argument("value")
        @click.option("--suffix", multiple=True)
        async def write(value: str, suffix: tuple[str, ...]):
            await asyncio.to_thread(path.write_text, "\n".join((value, *suffix)))
            return path

        group = ctx.accept(COMMANDS)
        with group.register(write):
            yield
        text = await asyncio.to_thread(path.read_text)
        await asyncio.to_thread(path.write_text, text + "\nreleased\n")

    @asynccontextmanager
    async def observe(ctx: Context):
        selected.append(ctx.accept(COMMANDS))
        yield

    provider = Composition(
        entries=[
            ContribEntry(name="commands", definition=commands),
            ContribEntry(
                name="observe",
                definition=Contrib(accepts=[COMMANDS], lifespan=observe),
            ),
        ]
    )
    plugin = Composition(
        entries=[
            ContribEntry(
                name="writer", definition=Contrib(accepts=[COMMANDS], lifespan=writer)
            )
        ]
    )
    async with ContribHub() as hub, hub.enable(provider):
        group = selected[0]
        for value in values:
            async with hub.enable(plugin):
                output = await group.dispatch(
                    [name, value, "--suffix", help], prog_name="example"
                )
                assert value in await asyncio.to_thread(output.read_text)
                assert help in await asyncio.to_thread(output.read_text)
            assert "released" in await asyncio.to_thread(path.read_text)


async def test_command_cancellation(tmp_path: Path):
    path = tmp_path / "cancelled.txt"
    started = asyncio.Event()
    selected: list[Commands] = []

    @asynccontextmanager
    async def plugin(ctx: Context):
        group = ctx.accept(COMMANDS)

        @click.command("work")
        async def work():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.to_thread(path.write_text, "handler finished\n")

        with group.register(work):
            selected.append(group)
            yield
        text = await asyncio.to_thread(path.read_text)
        await asyncio.to_thread(path.write_text, text + "plugin finished\n")

    async with (
        ContribHub() as hub,
        hub.enable(
            Composition(
                entries=[
                    ContribEntry(name="commands", definition=commands),
                    ContribEntry(
                        name="plugin",
                        definition=Contrib(accepts=[COMMANDS], lifespan=plugin),
                    ),
                ]
            )
        ),
    ):
        task = asyncio.create_task(selected[0].dispatch(["work"], prog_name="example"))
        await started.wait()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    text = await asyncio.to_thread(path.read_text)
    assert "handler finished" in text and "plugin finished" in text
