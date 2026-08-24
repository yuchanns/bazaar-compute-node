from __future__ import annotations

import asyncio

import aiosqlite
import pytest

from bazaar_compute_node.contrib.sqlite import SqliteDatabase
from bazaar_compute_node.contrib.sqlite.executor import (
    SqliteExecutorClosedError,
    SqliteSession,
)


@pytest.mark.asyncio
async def test_read_pool_and_writer_run_concurrently_with_snapshot_isolation() -> None:
    database = SqliteDatabase(max_idle_readers=1, max_readers=2)
    await database.start(timeout=2)
    try:
        await database.execute("CREATE TABLE executor_probe (value INTEGER)")
        await database.execute("INSERT INTO executor_probe VALUES (1)")
        entered = [asyncio.Event(), asyncio.Event()]
        release = asyncio.Event()

        async def hold_reader(index: int) -> None:
            async with database.reader():
                entered[index].set()
                await release.wait()

        readers = [asyncio.create_task(hold_reader(index)) for index in range(2)]
        await asyncio.gather(*(event.wait() for event in entered))
        await database.execute("INSERT INTO executor_probe VALUES (2)")
        release.set()
        await asyncio.gather(*readers)

        async with database.reader() as session, session.transaction():
            before = await session.fetchone("SELECT COUNT(*) FROM executor_probe")
            await database.execute("INSERT INTO executor_probe VALUES (3)")
            after = await session.fetchone("SELECT COUNT(*) FROM executor_probe")
        latest = await database.fetchone("SELECT COUNT(*) FROM executor_probe")

        assert before is not None and before[0] == 2
        assert after is not None and after[0] == 2
        assert latest is not None and latest[0] == 3
    finally:
        await database.stop(timeout=2)


@pytest.mark.asyncio
async def test_writer_fifo_and_transaction_rollback_preserve_liveness() -> None:
    database = SqliteDatabase()
    await database.start(timeout=2)
    try:
        await database.execute("CREATE TABLE writer_probe (value INTEGER)")
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        order: list[str] = []

        async def first(session: SqliteSession) -> None:
            order.append("first-start")
            first_started.set()
            await release_first.wait()
            await session.execute("INSERT INTO writer_probe VALUES (1)")
            order.append("first-end")

        async def second(session: SqliteSession) -> None:
            order.append("second")
            await session.execute("INSERT INTO writer_probe VALUES (2)")

        first_task = asyncio.create_task(database.transaction_write(first))
        await first_started.wait()
        second_task = asyncio.create_task(database.transaction_write(second))
        release_first.set()
        await asyncio.gather(first_task, second_task)

        async def fail(session: SqliteSession) -> None:
            await session.execute("INSERT INTO writer_probe VALUES (3)")
            raise RuntimeError("fail")

        with pytest.raises(RuntimeError, match="fail"):
            await database.transaction_write(fail)
        await database.execute("INSERT INTO writer_probe VALUES (4)")
        rows = await database.fetchall("SELECT value FROM writer_probe ORDER BY value")

        assert order == ["first-start", "first-end", "second"]
        assert [row[0] for row in rows] == [1, 2, 4]
    finally:
        await database.stop(timeout=2)


@pytest.mark.asyncio
async def test_writer_cancellation_skips_queued_work_and_rolls_back_active_work() -> (
    None
):
    database = SqliteDatabase()
    await database.start(timeout=2)
    try:
        await database.execute("CREATE TABLE cancel_probe (value INTEGER)")
        blocker_started = asyncio.Event()
        release_blocker = asyncio.Event()

        async def blocker(_: SqliteSession) -> None:
            blocker_started.set()
            await release_blocker.wait()

        blocker_task = asyncio.create_task(database.transaction_write(blocker))
        await blocker_started.wait()
        queued_task = asyncio.create_task(
            database.execute("INSERT INTO cancel_probe VALUES (1)")
        )
        await asyncio.sleep(0)
        queued_task.cancel()
        release_blocker.set()
        await blocker_task
        with pytest.raises(asyncio.CancelledError):
            await queued_task

        active_started = asyncio.Event()

        async def active(session: SqliteSession) -> None:
            await session.execute("INSERT INTO cancel_probe VALUES (2)")
            active_started.set()
            await session.fetchone(
                "WITH RECURSIVE counter(value) AS ("
                "VALUES(0) UNION ALL SELECT value + 1 FROM counter WHERE value < 100000000"
                ") SELECT SUM(value) FROM counter"
            )

        active_task = asyncio.create_task(database.transaction_write(active))
        await active_started.wait()
        active_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await active_task
        await database.execute("INSERT INTO cancel_probe VALUES (3)")
        rows = await database.fetchall("SELECT value FROM cancel_probe")
        assert [row[0] for row in rows] == [3]
    finally:
        await database.stop(timeout=2)


@pytest.mark.asyncio
async def test_query_only_reader_is_reused_and_shutdown_drains_borrowers() -> None:
    database = SqliteDatabase(max_idle_readers=1, max_readers=2)
    await database.start(timeout=2)
    borrower_started = asyncio.Event()
    release_borrower = asyncio.Event()

    async def hold_reader() -> None:
        async with database.reader() as session:
            with pytest.raises(aiosqlite.OperationalError, match="readonly"):
                await session.execute("CREATE TABLE forbidden (value INTEGER)")
            assert await session.fetchone("SELECT 1") is not None
            borrower_started.set()
            await release_borrower.wait()

    borrower_task = asyncio.create_task(hold_reader())
    await borrower_started.wait()
    stop_task = asyncio.create_task(database.stop(timeout=2))
    try:
        await asyncio.sleep(0)
        with pytest.raises(SqliteExecutorClosedError):
            await database.fetchone("SELECT 1")
        assert not stop_task.done()
    finally:
        release_borrower.set()
        await borrower_task
        await stop_task
