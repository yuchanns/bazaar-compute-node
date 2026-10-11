from __future__ import annotations

from pathlib import Path
lazy import asyncio
lazy from contextlib import asynccontextmanager
lazy from uuid import uuid7

import pytest
from aiofiles.threadpool.text import AsyncTextIOWrapper
from bazaar_contrib_status import CONTRIB_STATUS, IContribStatus, status
from bazaar_core import (
    COMPOSITION,
    Composition,
    Context,
    Contrib,
    ContribEntry,
    Enablement,
    EventKey,
    Running,
    ServiceKey,
)
from pydantic import BaseModel
lazy import aiofiles

from bazaar_compute_node import run

pytestmark = pytest.mark.asyncio
DIRECTORY = ServiceKey[Path]("node.directory")
WORKSPACE = ServiceKey[Path]("agent.workspace")
JOURNAL = ServiceKey[AsyncTextIOWrapper]("provider.journal")
MESSAGE = EventKey[str]("provider.message")


class Config(BaseModel):
    directory: Path


async def test_node_composition_owns_nested_local_resources(tmp_path: Path):
    queries: list[IContribStatus] = []
    handles: dict[str, AsyncTextIOWrapper] = {}
    contexts: dict[str, Context] = {}
    children: dict[str, list[Enablement]] = {}

    @asynccontextmanager
    async def directory(ctx: Context, config: Config):
        ctx.offer(DIRECTORY, config.directory)
        yield

    @asynccontextmanager
    async def journal(ctx: Context):
        path = ctx.accept(WORKSPACE) / f"{ctx.name}.txt"
        async with aiofiles.open(path, "w") as handle:
            handles[ctx.name] = handle
            ctx.offer(JOURNAL, handle)
            yield

    @asynccontextmanager
    async def writer(ctx: Context):
        contexts[ctx.name] = ctx
        handle = ctx.accept(JOURNAL)
        await handle.write(f"{ctx.name}: ready\n")
        try:
            yield
        finally:
            await handle.write("consumer released\n")
            await handle.flush()

    async def write(ctx: Context, message: str):
        await ctx.accept(JOURNAL).write(message + "\n")
        await ctx.accept(JOURNAL).flush()

    provider = Contrib(accepts=[WORKSPACE], offers=[JOURNAL], lifespan=journal)
    consumer = Contrib(accepts=[JOURNAL], lifespan=writer, listen={MESSAGE: write})

    @asynccontextmanager
    async def workspace(ctx: Context):
        path = ctx.accept(DIRECTORY) / ctx.name
        await asyncio.to_thread(path.mkdir)
        ctx.offer(WORKSPACE, path)
        from contextlib import AsyncExitStack

        try:
            async with AsyncExitStack() as stack:
                children[ctx.name] = []
                for name in ("channel", "runtime"):
                    name = f"{ctx.name}-{name}"
                    owner = await stack.enter_async_context(
                        ctx.enable(
                            Composition(
                                entries=[
                                    ContribEntry(name=name, definition=provider),
                                    ContribEntry(
                                        name=name + "-writer", definition=consumer
                                    ),
                                ]
                            ),
                            local=(JOURNAL, MESSAGE),
                        )
                    )
                    children[ctx.name].append(owner)
                yield
        finally:
            async with aiofiles.open(path / "released.txt", "w") as handle:
                await handle.write("agent released\n")

    @asynccontextmanager
    async def inspect(ctx: Context):
        queries.append(ctx.accept(CONTRIB_STATUS))
        yield

    @asynccontextmanager
    async def load(ctx: Context):
        ctx.offer(
            COMPOSITION,
            Composition(
                entries=[
                    ContribEntry(name="status", definition=status),
                    ContribEntry(
                        name="inspect",
                        definition=Contrib(accepts=[CONTRIB_STATUS], lifespan=inspect),
                    ),
                    *[
                        ContribEntry(
                            name=name,
                            definition=Contrib(
                                accepts=[DIRECTORY],
                                offers=[WORKSPACE],
                                lifespan=workspace,
                            ),
                            bindings={WORKSPACE: uuid7()},
                        )
                        for name in ("alice", "bob")
                    ],
                ]
            ),
        )
        yield

    async with (
        asyncio.timeout(5),
        run(
            Composition(
                entries=[
                    ContribEntry(
                        name="directory",
                        definition=Contrib(
                            config=Config, offers=[DIRECTORY], lifespan=directory
                        ),
                        config={"directory": tmp_path},
                    )
                ]
            ),
            ContribEntry(
                name="node-composition",
                definition=Contrib(offers=[COMPOSITION], lifespan=load),
            ),
        ),
    ):
        assert any(
            item.name == "status" and isinstance(item.state, Running)
            for item in queries[0].snapshot()
        )
        for name in ("alice", "bob"):
            for provider_name in ("channel", "runtime"):
                await contexts[f"{name}-{provider_name}-writer"].announce(
                    MESSAGE, f"{name} {provider_name} message"
                )
        for owner in children["alice"]:
            await owner.aclose()
        await contexts["bob-runtime-writer"].announce(
            MESSAGE, "bob continues after alice stops"
        )
    assert all(handle.closed for handle in handles.values())
    for name in ("alice", "bob"):
        for provider_name in ("channel", "runtime"):
            async with aiofiles.open(
                tmp_path / name / f"{name}-{provider_name}.txt"
            ) as handle:
                text = await handle.read()
            assert (
                f"{name} {provider_name} message" in text
                and "consumer released" in text
            )
            if name == "bob" and provider_name == "runtime":
                assert "bob continues after alice stops" in text
        async with aiofiles.open(tmp_path / name / "released.txt") as handle:
            assert "agent released" in await handle.read()
