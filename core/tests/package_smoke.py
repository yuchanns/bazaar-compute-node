"""Run a filesystem composition using only the installed core distribution."""

from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager
from importlib.metadata import version
from importlib.resources import files
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TextIO

from pydantic import BaseModel

from bazaar_core import (
    COMPOSITION,
    Composition,
    Context,
    Contrib,
    ContribEntry,
    ContribHub,
    EventKey,
    ServiceKey,
)

PATH = ServiceKey[Path]("journal.path")
JOURNAL = ServiceKey[TextIO]("journal")
MESSAGE = EventKey[str]("message")


class Config(BaseModel):
    path: Path


@asynccontextmanager
async def path_lifespan(ctx: Context, config: Config):
    ctx.offer(PATH, config.path)
    yield


@asynccontextmanager
async def journal_lifespan(ctx: Context):
    handle = await asyncio.to_thread(ctx.accept(PATH).open, "w")
    try:
        ctx.offer(JOURNAL, handle)
        yield
    finally:
        await asyncio.to_thread(handle.close)


async def write(ctx: Context, message: str):
    handle = ctx.accept(JOURNAL)
    await asyncio.to_thread(handle.write, f"{ctx.name}: {message}\n")
    await asyncio.to_thread(handle.flush)


async def use(directory: Path):
    hub = ContribHub()
    handles: list[TextIO] = []
    contexts: list[Context] = []
    compositions: list[Composition] = []

    @asynccontextmanager
    async def inspect(ctx: Context):
        handles.append(ctx.accept(JOURNAL))
        yield

    @asynccontextmanager
    async def load(ctx: Context):
        ctx.accept(PATH)
        composition = Composition(
            entries=[
                ContribEntry(
                    name="inspect",
                    definition=Contrib(accepts=[JOURNAL], lifespan=inspect),
                ),
                ContribEntry(
                    name="alice",
                    definition=Contrib(
                        accepts=[JOURNAL],
                        listen={MESSAGE: write},
                    ),
                ),
                ContribEntry(
                    name="journal",
                    definition=Contrib(
                        accepts=[PATH],
                        offers=[JOURNAL],
                        lifespan=journal_lifespan,
                    ),
                ),
            ],
        )
        contexts.append(ctx)
        compositions.append(composition)
        ctx.offer(COMPOSITION, ctx.validate(composition))
        yield

    async with hub.run(
        Composition(
            entries=[
                ContribEntry(
                    name="path",
                    definition=Contrib(
                        config=Config, offers=[PATH], lifespan=path_lifespan
                    ),
                    config={"path": directory / "journal.txt"},
                    required_offers=[PATH],
                ),
            ]
        ),
        ContribEntry(
            name="loader",
            definition=Contrib(accepts=[PATH], offers=[COMPOSITION], lifespan=load),
        ),
    ):
        handle = handles[0]
        await hub.announce(MESSAGE, "installed standalone wheel")
        local = Composition(
            entries=[
                ContribEntry(
                    name="path",
                    definition=Contrib(
                        config=Config, offers=[PATH], lifespan=path_lifespan
                    ),
                    config={"path": directory / "private.txt"},
                ),
                ContribEntry(
                    name="journal",
                    definition=Contrib(
                        accepts=[PATH], offers=[JOURNAL], lifespan=journal_lifespan
                    ),
                ),
                ContribEntry(
                    name="inspect",
                    definition=Contrib(accepts=[JOURNAL], lifespan=inspect),
                ),
                ContribEntry(
                    name="private",
                    definition=Contrib(accepts=[JOURNAL], listen={MESSAGE: write}),
                ),
            ]
        )
        async with contexts[0].enable(local, local=(PATH, JOURNAL, MESSAGE)):
            if handles[-1] is handle:
                raise RuntimeError("private journal reused the shared file")
            await asyncio.to_thread(
                handles[-1].write, "private installed composition\n"
            )
            await asyncio.to_thread(handles[-1].flush)
        if not handles[-1].closed or handle.closed:
            raise RuntimeError("private cleanup closed the shared journal")

    if not handle.closed:
        raise RuntimeError("journal lifespan did not close its file")
    document = await asyncio.to_thread((directory / "journal.txt").read_text)
    if "alice: installed" not in document:
        raise RuntimeError("agent event consumers did not persist their messages")


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: package_smoke.py VERSION")
    if version("bazaar-core") != sys.argv[1]:
        raise RuntimeError("installed core distribution has a different version")
    if not files("bazaar_core").joinpath("py.typed").is_file():
        raise RuntimeError("core wheel is missing its typing marker")
    with TemporaryDirectory(prefix="bazaar-core-package-") as directory:
        asyncio.run(use(Path(directory)))


if __name__ == "__main__":
    main()
