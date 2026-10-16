from __future__ import annotations

lazy import asyncio
lazy from pathlib import Path

import pytest
from bazaar_contrib_storage import (
    Binary,
    Column,
    ColumnDefinition,
    Compare,
    CreateTable,
    Database,
    Insert,
    Select,
    Session,
    StorageClosedError,
    StorageIntegrityError,
    Table,
    Update,
    Value,
)
from workflow import migration_workflow, workflow

from bazaar_contrib_sqlite import SqliteConfig, open_database


async def test_ordinary_data_queries_search_and_transaction(tmp_path: Path):
    async with open_database(SqliteConfig(path=tmp_path / "notes.sqlite3")) as database:
        await workflow(database)
        await migration_workflow(database)


async def counter(database: Database):
    await database.apply_schema(
        (
            CreateTable(
                table=Table(
                    name="counter",
                    columns=(
                        ColumnDefinition(name="id", type="integer"),
                        ColumnDefinition(name="value", type="integer"),
                    ),
                    primary_key=("id",),
                )
            ),
        )
    )
    await database.insert(Insert(table="counter", rows=({"id": 1, "value": 0},)))


async def test_concurrent_writes_and_integrity_rollback(tmp_path: Path):
    async with open_database(
        SqliteConfig(path=tmp_path / "counter.sqlite3", max_readers=2)
    ) as database:
        await counter(database)

        async def increment(session: Session):
            row = (await session.select(Select(table="counter")))[0]
            await session.update(
                Update(
                    table="counter",
                    values={
                        "value": Binary(
                            left=Value(value=row["value"]),
                            operator="+",
                            right=Value(value=1),
                        )
                    },
                )
            )

        await asyncio.gather(*(database.transaction(increment) for _ in range(40)))
        assert (await database.select(Select(table="counter")))[0]["value"] == 40

        async def duplicate(session: Session):
            await session.update(
                Update(table="counter", values={"value": Value(value=999)})
            )
            await session.insert(
                Insert(table="counter", rows=({"id": 1, "value": 999},))
            )

        with pytest.raises(StorageIntegrityError):
            await database.transaction(duplicate)
        assert (await database.select(Select(table="counter")))[0]["value"] == 40


async def test_cancelled_transaction_rolls_back_and_releases_writer(tmp_path: Path):
    started = asyncio.Event()
    sessions: list[Session] = []
    async with open_database(
        SqliteConfig(path=tmp_path / "cancel.sqlite3")
    ) as database:
        await counter(database)

        async def hold(session: Session):
            sessions.append(session)
            await session.update(
                Update(table="counter", values={"value": Value(value=99)})
            )
            started.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(database.transaction(hold))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await database.update(Update(table="counter", values={"value": Value(value=7)}))
        assert (await database.select(Select(table="counter")))[0]["value"] == 7
        with pytest.raises(StorageClosedError):
            await sessions[0].select(Select(table="counter"))
    with pytest.raises(StorageClosedError):
        await database.select(Select(table="counter"))


async def test_lifecycle_drains_accepted_writes(tmp_path: Path):
    started = asyncio.Event()
    release = asyncio.Event()
    async with open_database(SqliteConfig(path=tmp_path / "drain.sqlite3")) as database:
        await counter(database)

        async def write(session: Session):
            started.set()
            await release.wait()
            await session.update(
                Update(table="counter", values={"value": Value(value=8)})
            )

        task = asyncio.create_task(database.transaction(write))
        await started.wait()
        asyncio.get_running_loop().call_soon(release.set)
    await task
    async with open_database(SqliteConfig(path=tmp_path / "drain.sqlite3")) as reopened:
        rows = await reopened.select(
            Select(
                table="counter",
                where=Compare(
                    left=Column(name="id"), operator="=", right=Value(value=1)
                ),
            )
        )
        assert rows[0]["value"] == 8


async def test_cancelled_expensive_read_releases_a_single_reader(tmp_path: Path):
    from bazaar_contrib_storage import All, Call, Join

    async with open_database(
        SqliteConfig(path=tmp_path / "read.sqlite3", max_idle_readers=1, max_readers=1)
    ) as database:
        await database.apply_schema(
            (
                CreateTable(
                    table=Table(
                        name="numbers",
                        columns=(ColumnDefinition(name="value", type="integer"),),
                    )
                ),
            )
        )
        await database.insert(
            Insert(
                table="numbers", rows=tuple({"value": value} for value in range(1000))
            )
        )
        query = Select(
            table="numbers",
            alias="a",
            columns={"total": Call(name="count")},
            joins=(
                Join(table="numbers", alias="b", on=All(conditions=())),
                Join(table="numbers", alias="c", on=All(conditions=())),
            ),
        )
        task = asyncio.create_task(database.select(query))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        rows = await database.select(
            Select(table="numbers", columns={"total": Call(name="count")})
        )
        assert rows[0]["total"] == 1000
