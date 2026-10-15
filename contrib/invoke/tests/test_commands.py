"""Exercise native commands, asynchronous files, and owned registrations."""

from __future__ import annotations

from contextlib import asynccontextmanager
lazy import asyncio
lazy from pathlib import Path
lazy from typing import TextIO
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
    source = tmp_path / "input.txt"
    await asyncio.to_thread(source.write_text, help)
    resources: list[TextIO] = []

    @asynccontextmanager
    async def writer(ctx: Context):

        @click.group(name, help=help)
        @click.pass_context
        async def files(ctx: click.Context):
            resource = await asyncio.to_thread(source.open)
            ctx.obj = ctx.with_resource(resource)
            resources.append(resource)

        @files.command("write")
        @click.argument("value")
        @click.argument("input", type=click.File("r"))
        @click.option("--suffix", multiple=True)
        async def write(value: str, input: TextIO, suffix: tuple[str, ...]):
            resources.append(input)
            text = await asyncio.to_thread(input.read)
            resource = click.get_current_context().obj
            text += await asyncio.to_thread(resource.read)
            await asyncio.to_thread(path.write_text, "\n".join((value, *suffix, text)))
            return path

        group = ctx.accept(COMMANDS)
        with group.register(files):
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
                    [name, "write", value, str(source), "--suffix", help],
                    prog_name="example",
                )
                assert value in await asyncio.to_thread(output.read_text)
                assert help in await asyncio.to_thread(output.read_text)
                assert all(resource.closed for resource in resources)
            assert "released" in await asyncio.to_thread(path.read_text)


async def test_command_cancellation(tmp_path: Path):
    path = tmp_path / "cancelled.txt"
    started = asyncio.Event()
    selected: list[Commands] = []
    resources: list[TextIO] = []

    @asynccontextmanager
    async def plugin(ctx: Context):
        group = ctx.accept(COMMANDS)

        @click.command("work")
        @click.argument("output", type=click.File("w", lazy=False))
        async def work(output: TextIO):
            resources.append(output)
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.to_thread(output.write, "handler finished\n")

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
        task = asyncio.create_task(
            selected[0].dispatch(["work", str(path)], prog_name="example")
        )
        await started.wait()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    text = await asyncio.to_thread(path.read_text)
    assert "handler finished" in text and "plugin finished" in text
    assert all(resource.closed for resource in resources)
