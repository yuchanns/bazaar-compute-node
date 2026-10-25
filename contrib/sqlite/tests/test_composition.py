from __future__ import annotations

from contextlib import asynccontextmanager
lazy import asyncio
lazy from pathlib import Path

import pytest
from bazaar_contrib_storage import (
    DB,
    ColumnDefinition,
    CreateTable,
    Database,
    Insert,
    Select,
    StorageClosedError,
    Table,
    database,
    storage,
)
from bazaar_contrib_timer_wheel import timer_wheel
from bazaar_core import Composition, Context, Contrib, ContribEntry, ContribHub

from bazaar_contrib_sqlite import sqlite


async def test_consumer_roots_select_and_close_sqlite_dependencies(tmp_path: Path):
    services: list[Database] = []
    ready = asyncio.Event()

    @asynccontextmanager
    async def idle(ctx: Context):
        ready.set()
        yield

    @asynccontextmanager
    async def consumer(ctx: Context):
        db = ctx.accept(DB)
        services.append(db)
        await db.apply_schema(
            (
                CreateTable(
                    table=Table(
                        name="notes",
                        columns=(ColumnDefinition(name="body", type="text"),),
                    )
                ),
            )
        )
        await db.insert(Insert(table="notes", rows=({"body": "selected through DB"},)))
        yield

    composition = Composition(
        entries=(
            ContribEntry(name="timer-wheel", definition=timer_wheel),
            ContribEntry(name="storage", definition=storage),
            ContribEntry(
                name="sqlite",
                definition=sqlite,
                config={"path": tmp_path / "selected.sqlite3"},
            ),
            ContribEntry(
                name="database", definition=database, config={"backend": "sqlite"}
            ),
            ContribEntry(name="idle", definition=Contrib(lifespan=idle)),
            ContribEntry(
                name="consumer", definition=Contrib(accepts=[DB], lifespan=consumer)
            ),
        ),
        roots=("idle",),
    )
    async with (
        ContribHub() as hub,
        hub.enable(composition) as enabled,
    ):
        await ready.wait()
        await enabled.update(composition.model_copy(update={"roots": ("consumer",)}))
        assert (await services[0].select(Select(table="notes")))[0][
            "body"
        ] == "selected through DB"
        await enabled.update(composition)
        with pytest.raises(StorageClosedError):
            await services[0].select(Select(table="notes"))


async def test_writer_loss_releases_consumers_and_closes_database(tmp_path: Path):
    from bazaar_core import CONTRIB_CHANGED, ContribChange, Failed

    services: list[Database] = []
    released = asyncio.Event()
    failed = asyncio.Event()

    @asynccontextmanager
    async def consumer(ctx: Context):
        services.append(ctx.accept(DB))
        try:
            yield
        finally:
            released.set()

    def changed(ctx: Context, change: ContribChange):
        if any(
            item.name == "sqlite" and isinstance(item.state, Failed)
            for item in change.snapshots
        ):
            failed.set()

    async with (
        ContribHub() as hub,
        hub.enable(
            Composition(
                entries=(
                    ContribEntry(name="timer-wheel", definition=timer_wheel),
                    ContribEntry(name="storage", definition=storage),
                    ContribEntry(
                        name="sqlite",
                        definition=sqlite,
                        config={"path": tmp_path / "failure.sqlite3"},
                    ),
                    ContribEntry(
                        name="database",
                        definition=database,
                        config={"backend": "sqlite"},
                    ),
                    ContribEntry(
                        name="consumer",
                        definition=Contrib(accepts=[DB], lifespan=consumer),
                    ),
                    ContribEntry(
                        name="observer",
                        definition=Contrib(listen={CONTRIB_CHANGED: changed}),
                    ),
                ),
                roots=("consumer", "observer"),
            )
        ),
    ):
        writer = next(
            task
            for task in asyncio.all_tasks()
            if task.get_name() == "bcn-sqlite-writer"
        )
        writer.cancel()
        await failed.wait()
        await released.wait()
    with pytest.raises(StorageClosedError):
        await services[0].select(Select(table="sqlite_master"))
