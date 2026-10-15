"""Select resource-owning contrib roots through their concrete service bindings."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import aiofiles
import pytest

from bazaar_core import (
    Composition,
    Context,
    Contrib,
    ContribEntry,
    ContribHub,
    Running,
)

from .resources import DB, Database
from .test_bindings import STORE

pytestmark = pytest.mark.asyncio


async def test_selected_root_uses_its_bound_database_and_releases_resources(
    tmp_path: Path,
):
    connections: list[Database] = []
    output = tmp_path / "result.txt"
    text = uuid4().hex

    @asynccontextmanager
    async def writer(ctx: Context):
        database = ctx.accept(DB)
        connections.append(database)
        await database.insert(ctx.name, text)
        async with aiofiles.open(output, "w") as stream:
            await stream.write(str(await database.messages()))
            await stream.flush()
            yield
            await stream.write("\nreleased\n")

    database = ContribEntry(
        name="database",
        definition=STORE,
        config={"path": str(tmp_path / "selected.sqlite")},
        local=(DB,),
    )
    entries = (
        database,
        ContribEntry(
            name="other-database",
            definition=STORE,
            config={"path": str(tmp_path / "other.sqlite")},
        ),
        ContribEntry(name="unconfigured", definition=STORE),
        ContribEntry(
            name="writer",
            definition=Contrib(accepts=[DB], lifespan=writer),
            bindings={DB: database.ref(DB)},
        ),
    )
    async with ContribHub() as hub:
        async with hub.enable(Composition(entries=entries, roots=("writer",))):
            async with aiofiles.open(output) as stream:
                assert text in await stream.read()
            active = [
                record for record in hub.snapshots if isinstance(record.state, Running)
            ]
            assert len(active) == len(connections) + 1
            assert connections[0].path.name == "selected.sqlite"
        assert all(database.closed for database in connections)
    async with aiofiles.open(output) as stream:
        assert "released" in await stream.read()


async def test_selected_scope_can_expand_without_reopening_shared_database(
    tmp_path: Path,
):
    databases: list[Database] = []
    contexts: list[Context] = []
    text = uuid4().hex

    @asynccontextmanager
    async def observe(ctx: Context):
        databases.append(ctx.accept(DB))
        contexts.append(ctx)
        yield

    @asynccontextmanager
    async def write(ctx: Context):
        await ctx.accept(DB).insert(ctx.name, text)
        yield

    entries = (
        ContribEntry(
            name="database",
            definition=STORE,
            config={"path": str(tmp_path / "shared.sqlite")},
        ),
        ContribEntry(
            name="observer", definition=Contrib(accepts=[DB], lifespan=observe)
        ),
        ContribEntry(name="writer", definition=Contrib(accepts=[DB], lifespan=write)),
    )
    async with ContribHub() as hub:
        candidate = hub.validate(Composition(entries=entries, roots=("observer",)))
        async with hub.enable(candidate):
            # Replacing a root selection reuses the existing binding graph.
            database, context = databases[-1], contexts[-1]
            preview = hub.validate(Composition(entries=entries), replacing=candidate)
            async with hub.enable(preview):
                assert text in str(await databases[0].messages())
                assert databases[-1] is database
                assert contexts[-1] is context
    assert all(database.closed for database in databases)
