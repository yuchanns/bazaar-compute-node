from __future__ import annotations

lazy import asyncio
lazy import json
lazy import sqlite3
lazy from contextlib import asynccontextmanager
lazy from pathlib import Path

import pytest
lazy import aiofiles
lazy import aiofiles.os
lazy from aiofiles.threadpool.text import AsyncTextIOWrapper
lazy from pydantic import BaseModel

from bazaar_core import (
    Candidate,
    Composition,
    Context,
    Contrib,
    ContribEntry,
    ContribHub,
    Failed,
    ServiceKey,
)

from .resources import (
    DB,
    MESSAGE_RECEIVED,
    Database,
    Message,
    line_server,
    loader,
    observe,
    recorder,
)

pytestmark = pytest.mark.asyncio


async def test_run_loads_from_bootstrap_and_persists_the_loaded_composition(
    tmp_path: Path,
):
    file_key = ServiceKey[AsyncTextIOWrapper]("config.document")

    class Config(BaseModel):
        path: Path

    path = tmp_path / "config.json"
    await asyncio.to_thread(
        path.write_text,
        json.dumps(
            {"database": str(tmp_path / "business.sqlite"), "agents": ["alice", "bob"]}
        ),
    )
    handles: list[AsyncTextIOWrapper] = []
    closed: list[str] = []
    connections: dict[str, Database] = {}

    @asynccontextmanager
    async def document(ctx: Context):
        async with aiofiles.open(path, "r") as handle:
            handles.append(handle)
            ctx.offer(file_key, handle)
            try:
                yield
            finally:
                closed.append("bootstrap")

    @asynccontextmanager
    async def database_lifespan(ctx: Context, config: Config):
        database = await Database.open(config.path)
        try:
            await database.start()
            ctx.offer(DB, database)
            yield
        finally:
            await database.aclose()
            closed.append("business")

    hub = ContribHub()

    async def load(ctx: Context) -> Candidate:
        value = json.loads(await ctx.accept(file_key).read())
        return ctx.validate(
            Composition(
                entries=[
                    observe(DB, connections),
                    *[
                        ContribEntry(name=name, definition=recorder)
                        for name in value["agents"]
                    ],
                    ContribEntry(
                        name="db",
                        definition=Contrib(
                            config=Config,
                            accepts=[file_key],
                            offers=[DB],
                            lifespan=database_lifespan,
                        ),
                        config={"path": value["database"]},
                    ),
                ],
            )
        )

    async with hub.run(
        Composition(
            entries=[
                ContribEntry(
                    name="document",
                    definition=Contrib(offers=[file_key], lifespan=document),
                    required_offers=[file_key],
                ),
            ]
        ),
        loader(load, accepts=[file_key]),
    ):
        database = connections["observe-db"]
        await hub.announce(MESSAGE_RECEIVED, Message("loaded from bootstrap"))
        rows = await database.messages()
        assert ("alice", "loaded from bootstrap") in rows
        assert ("bob", "loaded from bootstrap") in rows
    assert database.closed and database.worker.done()
    assert all(handle.closed for handle in handles)
    assert closed.index("business") < closed.index("bootstrap")
    await hub.aclose()


async def test_run_load_cancellation_releases_bootstrap_and_preserves_cancellation(
    tmp_path: Path,
    tasks: asyncio.TaskGroup,
):
    file_key = ServiceKey[AsyncTextIOWrapper]("config.document")
    path = tmp_path / "config.json"
    await asyncio.to_thread(path.write_text, "{}")
    handles: list[AsyncTextIOWrapper] = []

    @asynccontextmanager
    async def document(ctx: Context):
        handle = await aiofiles.open(path, "r")
        handles.append(handle)
        ctx.offer(file_key, handle)
        try:
            yield
        finally:
            try:
                await handle.flush()
            finally:
                await handle.close()

    hub = ContribHub()
    async with line_server() as (port, connected, release):

        async def load(ctx: Context) -> Composition:
            handle = ctx.accept(file_key)
            await handle.read()
            reader, writer = await asyncio.open_connection(
                "127.0.0.1", port, limit=4096
            )
            try:
                await reader.readline()
            finally:
                writer.close()
                await writer.wait_closed()
            return Composition()

        async def use():
            async with hub.run(
                Composition(
                    entries=[
                        ContribEntry(
                            name="document",
                            definition=Contrib(offers=[file_key], lifespan=document),
                        )
                    ]
                ),
                loader(load, accepts=[file_key]),
            ):
                await asyncio.Event().wait()

        task = tasks.create_task(use())
        await connected.wait()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert task.cancelled()
        assert all(handle.closed for handle in handles)
        release.set()
    await hub.aclose()


async def test_run_isolates_real_writer_failure_and_keeps_independent_plugins_running(
    tmp_path: Path,
):
    databases: list[Database] = []
    stopped = asyncio.Event()
    healthy_key = ServiceKey[Database]("healthy.db")
    connections: dict[str, Database] = {}
    healthy_connections: dict[str, Database] = {}

    @asynccontextmanager
    async def database_lifespan(ctx: Context):
        database = await Database.open(tmp_path / "critical.sqlite")
        databases.append(database)
        try:
            await database.start()
            ctx.offer(DB, database)
            yield
        finally:
            await database.aclose()
            stopped.set()

    @asynccontextmanager
    async def healthy_lifespan(ctx: Context):
        database = await Database.open(tmp_path / "healthy.sqlite")
        try:
            await database.start()
            ctx.offer(healthy_key, database)
            yield
        finally:
            await database.aclose()

    entry = ContribEntry(
        name="db", definition=Contrib(offers=[DB], lifespan=database_lifespan)
    )

    async def load(ctx: Context) -> Composition:
        del ctx
        return Composition(
            entries=[
                observe(DB, connections),
                observe(healthy_key, healthy_connections),
                entry,
                ContribEntry(
                    name="healthy-db",
                    definition=Contrib(offers=[healthy_key], lifespan=healthy_lifespan),
                ),
            ]
        )

    hub = ContribHub()
    async with hub.run(Composition(entries=[]), loader(load)):
        database = connections["observe-db"]
        healthy = healthy_connections["observe-healthy.db"]
        async with database.connection.execute("DROP TABLE messages"):
            pass
        await database.connection.commit()
        with pytest.raises(sqlite3.OperationalError):
            await database.insert("root", "write after external schema removal")
        await stopped.wait()
        failed = next(instance for instance in hub.snapshots if instance.name == "db")
        assert isinstance(failed.state, Failed)
        assert failed.state.error and "OperationalError" in failed.state.error

        @asynccontextmanager
        async def journal(ctx: Context):
            del ctx
            async with aiofiles.open(tmp_path / "after-failure.log", "w") as handle:
                await handle.write("new plugin started after unrelated failure\n")
                yield

        async with hub.enable(
            Composition(
                entries=[
                    ContribEntry(
                        name="after-failure", definition=Contrib(lifespan=journal)
                    )
                ]
            )
        ):
            retained = next(item for item in hub.snapshots if item.id == failed.id)
            assert isinstance(retained.state, Failed)
            await healthy.insert(
                "root", "unrelated enablement preserved healthy resource"
            )
            assert (
                "root",
                "unrelated enablement preserved healthy resource",
            ) in await healthy.messages()
        await healthy.insert("root", "independent plugin continues")
        assert ("root", "independent plugin continues") in await healthy.messages()
    assert all(database.closed and database.worker.done() for database in databases)
    replacement = ContribHub()
    async with replacement.run(Composition(entries=[]), loader(load)):
        database = connections["observe-db"]
        await database.insert("root", "database recreated after failure")
        assert ("root", "database recreated after failure") in await database.messages()
    await replacement.aclose()
