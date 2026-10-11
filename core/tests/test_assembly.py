from __future__ import annotations

lazy import asyncio
lazy from contextlib import AsyncExitStack, asynccontextmanager
lazy from pathlib import Path

import pytest
lazy import aiofiles
lazy import aiofiles.os
lazy from aiofiles.threadpool.text import AsyncTextIOWrapper

from bazaar_core import (
    Composition,
    Context,
    Contrib,
    ContribEntry,
    ContribHub,
    ServiceKey,
)

from .resources import (
    DB,
    MESSAGE_RECEIVED,
    STORAGE,
    BackendConfig,
    BackendRegistry,
    Database,
    Message,
    backend_key,
    backend_lifespan,
    database,
    observe,
    recorder,
    storage,
)

pytestmark = pytest.mark.asyncio


async def test_unordered_backends_share_selected_connection_across_enablements(
    tmp_path: Path,
    tasks: asyncio.TaskGroup,
    lifespans: AsyncExitStack,
):
    primary = Contrib(
        config=BackendConfig,
        accepts=[STORAGE],
        offers=[backend_key("primary")],
        lifespan=backend_lifespan,
    )
    archive = Contrib(
        config=BackendConfig,
        accepts=[STORAGE],
        offers=[backend_key("archive")],
        lifespan=backend_lifespan,
    )
    connections: dict[str, Database] = {}
    registries: dict[str, BackendRegistry] = {}
    archives: dict[str, Database] = {}
    entries = [
        observe(DB, connections),
        observe(STORAGE, registries),
        observe(backend_key("archive"), archives),
        ContribEntry(
            name="database", definition=database, config={"backend": "archive"}
        ),
        ContribEntry(
            name="primary",
            definition=primary,
            config={"name": "primary", "path": tmp_path / "primary.sqlite"},
        ),
        ContribEntry(
            name="archive",
            definition=archive,
            config={"name": "archive", "path": tmp_path / "archive.sqlite"},
        ),
        ContribEntry(name="storage", definition=storage),
    ]
    hub = await lifespans.enter_async_context(ContribHub())
    candidate = hub.validate(Composition(entries=entries))
    assert not await aiofiles.os.listdir(tmp_path)
    key = ServiceKey[AsyncTextIOWrapper]("recorder.journal")
    handles: dict[str, AsyncTextIOWrapper] = {}

    @asynccontextmanager
    async def journal(ctx: Context):
        async with aiofiles.open(tmp_path / f"{ctx.name}.txt", "w") as handle:
            handles[ctx.name] = handle
            ctx.offer(key, handle)
            yield

    async def write(ctx: Context, value: Message):
        handle = ctx.accept(key)
        await handle.write(value.text + "\n")
        await handle.flush()
        await ctx.accept(DB).insert(ctx.name, value.text)

    async with hub.enable(candidate):
        selected = connections["observe-db"]
        registry = registries["observe-storage"]
        assert (
            selected is registry.get("archive")
            and selected is archives["observe-storage.backend.archive"]
        )
        assert registry.get("primary") is not selected
        async with (
            hub.enable(
                Composition(
                    entries=[
                        ContribEntry(
                            name="journal",
                            definition=Contrib(
                                accepts=[DB],
                                offers=[key],
                                lifespan=journal,
                                listen={MESSAGE_RECEIVED: write},
                            ),
                        )
                    ]
                )
            ) as first,
            hub.enable(
                Composition(
                    entries=[ContribEntry(name="recorder", definition=recorder)]
                )
            ),
        ):
            await tasks.create_task(
                hub.announce(MESSAGE_RECEIVED, Message("hello from a shared database"))
            )
            await first.aclose()
            await first.aclose()
            assert handles["journal"].closed
            await hub.announce(MESSAGE_RECEIVED, Message("remaining recorder"))
            assert ("recorder", "remaining recorder") in await selected.messages()
            async with aiofiles.open(tmp_path / "journal.txt") as handle:
                assert "hello" in await handle.read()
    assert selected.closed and selected.worker.done()
    reopened = await Database.open(tmp_path / "archive.sqlite")
    try:
        assert ("recorder", "remaining recorder") in await reopened.messages()
    finally:
        await reopened.aclose()
    await hub.aclose()


async def test_lifespan_owned_hub_has_independent_service_bindings(tmp_path: Path):
    from pydantic import BaseModel

    class Config(BaseModel):
        path: Path

    @asynccontextmanager
    async def connection(ctx: Context, config: Config):
        value = await Database.open(config.path)
        try:
            await value.start()
            ctx.offer(DB, value)
            yield
        finally:
            await value.aclose()

    backend = Contrib(config=Config, offers=[DB], lifespan=connection)
    nested_key = ServiceKey[Database]("nested.db")
    connections: dict[str, Database] = {}
    nested_connections: dict[str, Database] = {}
    compositions: dict[str, Database] = {}

    @asynccontextmanager
    async def composition(ctx: Context):
        async with (
            ContribHub() as nested,
            nested.enable(
                Composition(
                    entries=[
                        observe(DB, nested_connections),
                        ContribEntry(
                            name="nested-db",
                            definition=backend,
                            config={"path": tmp_path / "nested.sqlite"},
                        ),
                    ]
                )
            ),
        ):
            ctx.offer(nested_key, nested_connections["observe-db"])
            yield

    async with (
        ContribHub() as hub,
        hub.enable(
            Composition(
                entries=[
                    observe(DB, connections),
                    observe(nested_key, compositions),
                    ContribEntry(
                        name="db",
                        definition=backend,
                        config={"path": tmp_path / "outer.sqlite"},
                    ),
                    ContribEntry(
                        name="nested",
                        definition=Contrib(lifespan=composition, offers=[nested_key]),
                    ),
                ]
            )
        ),
    ):
        outer, inner = connections["observe-db"], compositions["observe-nested.db"]
        assert outer is not inner
        await outer.insert("alice", "outer")
        await inner.insert("alice", "inner")
        assert ("alice", "outer") in await outer.messages()
        assert ("alice", "inner") in await inner.messages()
    assert outer.closed and inner.closed
