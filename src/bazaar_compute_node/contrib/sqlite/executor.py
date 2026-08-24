from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable, Iterable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from time import monotonic
from types import TracebackType
from typing import TypeVar

import aiosqlite

T = TypeVar("T")


class SqliteExecutorClosedError(RuntimeError):
    """The SQLite executor is not accepting new work."""


@dataclass(frozen=True, slots=True)
class SqliteExecuteResult:
    rowcount: int
    lastrowid: int | None
    rows: tuple[aiosqlite.Row, ...]


class SqliteSession:
    """SQL operations bound to one executor-owned connection."""

    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._connection = connection

    async def execute(
        self,
        statement: str,
        parameters: Sequence[object] = (),
    ) -> SqliteExecuteResult:
        cursor = await self._connection.execute(statement, parameters)
        try:
            rows = tuple(await cursor.fetchall())
            return SqliteExecuteResult(
                rowcount=cursor.rowcount,
                lastrowid=cursor.lastrowid,
                rows=rows,
            )
        finally:
            await cursor.close()

    async def executemany(
        self,
        statement: str,
        parameter_sets: Iterable[Sequence[object]],
    ) -> SqliteExecuteResult:
        cursor = await self._connection.executemany(statement, parameter_sets)
        try:
            rows = tuple(await cursor.fetchall())
            return SqliteExecuteResult(
                rowcount=cursor.rowcount,
                lastrowid=cursor.lastrowid,
                rows=rows,
            )
        finally:
            await cursor.close()

    async def fetchone(
        self,
        statement: str,
        parameters: Sequence[object] = (),
    ) -> aiosqlite.Row | None:
        cursor = await self._connection.execute(statement, parameters)
        try:
            return await cursor.fetchone()
        finally:
            await cursor.close()

    async def fetchall(
        self,
        statement: str,
        parameters: Sequence[object] = (),
    ) -> list[aiosqlite.Row]:
        cursor = await self._connection.execute(statement, parameters)
        try:
            return list(await cursor.fetchall())
        finally:
            await cursor.close()


class SqliteReadSession(SqliteSession):
    def transaction(self) -> AbstractAsyncContextManager[SqliteReadSession]:
        return _ReadTransaction(self)


class _ReadTransaction(AbstractAsyncContextManager[SqliteReadSession]):
    def __init__(self, session: SqliteReadSession) -> None:
        self._session = session
        self._active = False

    async def __aenter__(self) -> SqliteReadSession:
        await self._session._connection.execute("BEGIN")
        self._active = True
        return self._session

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        del exc_value, traceback
        if not self._active:
            return False
        statement = "ROLLBACK" if exc_type is not None else "COMMIT"
        try:
            if exc_type is not None and issubclass(exc_type, asyncio.CancelledError):
                await self._session._connection.interrupt()
            await asyncio.shield(self._session._connection.execute(statement))
        except BaseException:
            if exc_type is None:
                await asyncio.shield(self._session._connection.execute("ROLLBACK"))
            raise
        finally:
            self._active = False
        return False


class _WriteTransaction(AbstractAsyncContextManager[SqliteSession]):
    def __init__(self, session: SqliteSession) -> None:
        self._session = session
        self._active = False

    async def __aenter__(self) -> SqliteSession:
        await self._session._connection.execute("BEGIN IMMEDIATE")
        self._active = True
        return self._session

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        del exc_value, traceback
        if not self._active:
            return False
        statement = "ROLLBACK" if exc_type is not None else "COMMIT"
        try:
            await asyncio.shield(self._session._connection.execute(statement))
        except BaseException:
            if exc_type is None:
                await asyncio.shield(self._session._connection.execute("ROLLBACK"))
            raise
        finally:
            self._active = False
        return False


@dataclass(slots=True)
class _WriteRequest[T]:
    operation: Callable[[SqliteSession], Awaitable[T]]
    transactional: bool
    result: asyncio.Future[T]
    done: asyncio.Event = field(default_factory=asyncio.Event)
    cancelled: bool = False
    operation_task: asyncio.Task[T] | None = None


@dataclass(frozen=True, slots=True)
class _IdleReader:
    connection: aiosqlite.Connection
    idle_since: float


class _ReaderLease(AbstractAsyncContextManager[SqliteReadSession]):
    def __init__(self, executor: SqliteExecutor) -> None:
        self._executor = executor
        self._connection: aiosqlite.Connection | None = None

    async def __aenter__(self) -> SqliteReadSession:
        connection = await self._executor._borrow_reader()
        self._connection = connection
        return SqliteReadSession(connection)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        del exc_type, exc_value, traceback
        connection = self._connection
        self._connection = None
        if connection is not None:
            await self._executor._return_reader(connection)
        return False


class SqliteExecutor:
    """Single-writer actor and bounded read-session pool."""

    def __init__(
        self,
        writer_connection: aiosqlite.Connection,
        read_connections: Sequence[aiosqlite.Connection],
        *,
        reader_factory: Callable[[], Awaitable[aiosqlite.Connection]],
        max_idle_readers: int,
        max_readers: int | None,
        reader_idle_timeout: float,
    ) -> None:
        if not read_connections:
            raise ValueError("at least one read connection is required")
        self._writer_connection = writer_connection
        self._reader_factory = reader_factory
        self._max_idle_readers = max_idle_readers
        self._max_readers = max_readers
        self._reader_idle_timeout = reader_idle_timeout
        self._read_connections = set(read_connections)
        self._idle_readers: deque[_IdleReader] = deque()
        for connection in read_connections:
            self._idle_readers.append(_IdleReader(connection, monotonic()))
        self._write_queue = asyncio.Queue[_WriteRequest[object] | None]()
        self._writer_task: asyncio.Task[None] | None = None
        self._reader_reaper_task: asyncio.Task[None] | None = None
        self._reader_reaper_stop = asyncio.Event()
        self._accepting = False
        self._borrowed_readers = 0
        self._opening_readers = 0
        self._reader_condition = asyncio.Condition()
        self._current_request: _WriteRequest[object] | None = None
        self._closed = False

    @property
    def is_started(self) -> bool:
        return self._writer_task is not None

    async def start(self) -> None:
        if self._writer_task is not None:
            return
        self._accepting = True
        self._reader_reaper_stop.clear()
        self._writer_task = asyncio.create_task(
            self._run_writer(),
            name="bcn-sqlite-writer",
        )
        self._reader_reaper_task = asyncio.create_task(
            self._reap_idle_readers(),
            name="bcn-sqlite-reader-reaper",
        )

    async def stop(self) -> None:
        if self._closed:
            return
        self._accepting = False
        await self._write_queue.join()
        async with self._reader_condition:
            self._reader_condition.notify_all()
            await self._reader_condition.wait_for(
                lambda: self._borrowed_readers == 0 and self._opening_readers == 0
            )
        await self._stop_reader_reaper()
        writer_task = self._writer_task
        if writer_task is not None:
            await self._write_queue.put(None)
            await writer_task
            self._writer_task = None
        for connection in tuple(self._read_connections):
            await connection.close()
        self._read_connections.clear()
        await self._writer_connection.close()
        self._closed = True

    async def abort(self) -> None:
        """Interrupt active work and close all connections after a failed drain."""
        if self._closed:
            return
        self._accepting = False
        async with self._reader_condition:
            self._reader_condition.notify_all()
        request = self._current_request
        if request is not None:
            if not request.result.done():
                request.result.set_exception(
                    SqliteExecutorClosedError(
                        "SQLite executor stopped before active work completed"
                    )
                )
            operation_task = request.operation_task
            if operation_task is not None and not operation_task.done():
                operation_task.cancel()
        await self._writer_connection.interrupt()

        while True:
            try:
                queued = self._write_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            try:
                if queued is not None and not queued.result.done():
                    queued.result.set_exception(
                        SqliteExecutorClosedError(
                            "SQLite executor stopped before queued work completed"
                        )
                    )
                    queued.done.set()
            finally:
                self._write_queue.task_done()

        writer_task = self._writer_task
        if writer_task is not None:
            writer_task.cancel()
            try:
                await writer_task
            except asyncio.CancelledError:
                pass
            self._writer_task = None

        await self._stop_reader_reaper()
        for connection in tuple(self._read_connections):
            await connection.close()
        self._read_connections.clear()
        await self._writer_connection.close()
        self._closed = True

    def reader(self) -> AbstractAsyncContextManager[SqliteReadSession]:
        self._require_accepting()
        return _ReaderLease(self)

    async def fetchone(
        self,
        statement: str,
        parameters: Sequence[object] = (),
    ) -> aiosqlite.Row | None:
        async with self.reader() as session:
            return await session.fetchone(statement, parameters)

    async def fetchall(
        self,
        statement: str,
        parameters: Sequence[object] = (),
    ) -> list[aiosqlite.Row]:
        async with self.reader() as session:
            return await session.fetchall(statement, parameters)

    async def execute(
        self,
        statement: str,
        parameters: Sequence[object] = (),
    ) -> SqliteExecuteResult:
        return await self._submit(
            lambda session: session.execute(statement, parameters),
            transactional=False,
        )

    async def executemany(
        self,
        statement: str,
        parameter_sets: Iterable[Sequence[object]],
    ) -> SqliteExecuteResult:
        frozen_parameters = tuple(tuple(values) for values in parameter_sets)
        return await self._submit(
            lambda session: session.executemany(statement, frozen_parameters),
            transactional=False,
        )

    async def transaction_write(
        self,
        operation: Callable[[SqliteSession], Awaitable[T]],
    ) -> T:
        return await self._submit(operation, transactional=True)

    async def write(
        self,
        operation: Callable[[SqliteSession], Awaitable[T]],
    ) -> T:
        return await self._submit(operation, transactional=False)

    async def _submit(
        self,
        operation: Callable[[SqliteSession], Awaitable[T]],
        *,
        transactional: bool,
    ) -> T:
        self._require_accepting()
        request = _WriteRequest(
            operation=operation,
            transactional=transactional,
            result=asyncio.get_running_loop().create_future(),
        )
        await self._write_queue.put(request)  # type: ignore[arg-type]
        try:
            return await asyncio.shield(request.result)
        except asyncio.CancelledError:
            request.cancelled = True
            operation_task = request.operation_task
            if operation_task is not None and not operation_task.done():
                operation_task.cancel()
                await self._writer_connection.interrupt()
            await asyncio.shield(request.done.wait())
            raise

    async def _run_writer(self) -> None:
        session = SqliteSession(self._writer_connection)
        while True:
            request = await self._write_queue.get()
            if request is None:
                self._write_queue.task_done()
                return
            self._current_request = request
            try:
                if request.cancelled:
                    if not request.result.done():
                        request.result.cancel()
                    continue
                request.operation_task = asyncio.create_task(
                    self._run_operation(request, session)
                )
                try:
                    value = await request.operation_task
                except asyncio.CancelledError as error:
                    if not request.result.done():
                        if request.cancelled:
                            request.result.cancel()
                        else:
                            request.result.set_exception(error)
                    current_task = asyncio.current_task()
                    if current_task is not None and current_task.cancelling():
                        raise
                except Exception as error:  # noqa: BLE001 - preserve operation failures
                    if not request.result.done():
                        request.result.set_exception(error)
                else:
                    if not request.result.done():
                        request.result.set_result(value)
            finally:
                self._current_request = None
                request.done.set()
                self._write_queue.task_done()

    @staticmethod
    async def _run_operation(
        request: _WriteRequest[object],
        session: SqliteSession,
    ) -> object:
        if request.transactional:
            async with _WriteTransaction(session):
                return await request.operation(session)
        return await request.operation(session)

    async def _borrow_reader(self) -> aiosqlite.Connection:
        while True:
            async with self._reader_condition:
                self._require_accepting()
                if self._idle_readers:
                    connection = self._idle_readers.pop().connection
                    self._borrowed_readers += 1
                    return connection
                if (
                    self._max_readers is None
                    or len(self._read_connections) + self._opening_readers
                    < self._max_readers
                ):
                    self._opening_readers += 1
                    break
                await self._reader_condition.wait()

        try:
            connection = await self._reader_factory()
        except BaseException:
            async with self._reader_condition:
                self._opening_readers -= 1
                self._reader_condition.notify_all()
            raise

        async with self._reader_condition:
            self._opening_readers -= 1
            if self._accepting:
                self._read_connections.add(connection)
                self._borrowed_readers += 1
                self._reader_condition.notify_all()
                return connection
            self._reader_condition.notify_all()
        await connection.close()
        raise SqliteExecutorClosedError("SQLite executor is not accepting work")

    async def _return_reader(self, connection: aiosqlite.Connection) -> None:
        async with self._reader_condition:
            self._borrowed_readers -= 1
            if self._accepting:
                self._idle_readers.append(_IdleReader(connection, monotonic()))
            self._reader_condition.notify_all()

    async def _reap_idle_readers(self) -> None:
        while not self._reader_reaper_stop.is_set():
            try:
                await asyncio.wait_for(
                    self._reader_reaper_stop.wait(),
                    timeout=self._reader_idle_timeout,
                )
            except TimeoutError:
                pass
            if self._reader_reaper_stop.is_set():
                return
            to_close: list[aiosqlite.Connection] = []
            async with self._reader_condition:
                if not self._accepting:
                    return
                excess = max(
                    0,
                    len(self._read_connections) - self._max_idle_readers,
                )
                cutoff = monotonic() - self._reader_idle_timeout
                while (
                    excess > 0
                    and self._idle_readers
                    and self._idle_readers[0].idle_since <= cutoff
                ):
                    reader = self._idle_readers.popleft()
                    self._read_connections.remove(reader.connection)
                    to_close.append(reader.connection)
                    excess -= 1
            for connection in to_close:
                await connection.close()

    async def _stop_reader_reaper(self) -> None:
        task = self._reader_reaper_task
        if task is None:
            return
        self._reader_reaper_stop.set()
        await task
        self._reader_reaper_task = None

    def _require_accepting(self) -> None:
        if not self._accepting:
            raise SqliteExecutorClosedError("SQLite executor is not accepting work")


__all__ = [
    "SqliteExecuteResult",
    "SqliteExecutor",
    "SqliteExecutorClosedError",
    "SqliteReadSession",
    "SqliteSession",
]
