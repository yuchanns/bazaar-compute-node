from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum, auto
from time import monotonic
from types import TracebackType
from typing import TypeVar

import aiosqlite
from bazaar_core import TaskFailureSignal

_LOGGER = logging.getLogger(__name__)

T = TypeVar("T")


class SqliteExecutorClosedError(RuntimeError):
    """The SQLite executor is not accepting new work."""


class SqliteQueryTimeoutError(TimeoutError):
    """SQLite interrupted a query after its connection's execution budget."""


@dataclass(frozen=True, slots=True)
class SqliteExecuteResult:
    rowcount: int
    lastrowid: int | None
    rows: tuple[aiosqlite.Row, ...]


class SqliteSession:
    """SQL operations bound to one executor-owned connection."""

    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._connection = connection

    @asynccontextmanager
    async def query_budget(self, seconds: float) -> AsyncIterator[None]:
        """Bound SQL execution and remove the handler before reusing the session."""

        deadline = monotonic() + seconds
        expired = False

        def progress() -> int:
            nonlocal expired
            expired = monotonic() >= deadline
            return int(expired)

        try:
            await self._connection.set_progress_handler(progress, 1_000)
            yield
        except sqlite3.OperationalError as error:
            if expired and error.sqlite_errorcode == sqlite3.SQLITE_INTERRUPT:
                raise SqliteQueryTimeoutError(
                    "SQLite query exceeded its execution budget"
                ) from error
            raise
        except asyncio.CancelledError:
            await self._connection.interrupt()
            raise
        finally:
            # sqlite3 removes the handler with None; aiosqlite's annotation omits it.
            await asyncio.shield(
                self._connection.set_progress_handler(None, 0)  # type: ignore[arg-type]
            )

    async def _call[T](self, operation: Awaitable[T]) -> T:
        task = asyncio.ensure_future(operation)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await self._connection.interrupt()
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    pass
                except Exception:  # noqa: BLE001 - Preserve cancellation after interrupting SQLite.
                    break
            if not task.cancelled():
                task.exception()
            raise

    async def execute(
        self,
        statement: str,
        parameters: Sequence[object] = (),
    ) -> SqliteExecuteResult:
        async def query() -> SqliteExecuteResult:
            cursor = await self._connection.execute(statement, parameters)
            try:
                rows = tuple(await cursor.fetchall())
                return SqliteExecuteResult(
                    rowcount=cursor.rowcount, lastrowid=cursor.lastrowid, rows=rows
                )
            finally:
                await cursor.close()

        return await self._call(query())

    async def executemany(
        self,
        statement: str,
        parameter_sets: Iterable[Sequence[object]],
    ) -> SqliteExecuteResult:
        async def query() -> SqliteExecuteResult:
            cursor = await self._connection.executemany(statement, parameter_sets)
            try:
                rows = tuple(await cursor.fetchall())
                return SqliteExecuteResult(
                    rowcount=cursor.rowcount, lastrowid=cursor.lastrowid, rows=rows
                )
            finally:
                await cursor.close()

        return await self._call(query())

    async def fetchone(
        self,
        statement: str,
        parameters: Sequence[object] = (),
    ) -> aiosqlite.Row | None:
        async def query() -> aiosqlite.Row | None:
            cursor = await self._connection.execute(statement, parameters)
            try:
                return await cursor.fetchone()
            finally:
                await cursor.close()

        return await self._call(query())

    async def fetchall(
        self,
        statement: str,
        parameters: Sequence[object] = (),
    ) -> list[aiosqlite.Row]:
        async def query() -> list[aiosqlite.Row]:
            cursor = await self._connection.execute(statement, parameters)
            try:
                return list(await cursor.fetchall())
            finally:
                await cursor.close()

        return await self._call(query())


class SqliteReadSession(SqliteSession):
    def transaction(self) -> AbstractAsyncContextManager[SqliteReadSession]:
        return _ReadTransaction(self)


class _TransactionState(Enum):
    NEW = auto()
    ACTIVE = auto()
    FINISHED = auto()


class _ExecutorState(Enum):
    CREATED = auto()
    RUNNING = auto()
    STOPPING = auto()
    CLOSED = auto()


class _ReadTransaction(AbstractAsyncContextManager[SqliteReadSession]):
    def __init__(self, session: SqliteReadSession) -> None:
        self._session = session
        self._state = _TransactionState.NEW

    async def __aenter__(self) -> SqliteReadSession:
        await self._session._connection.execute("BEGIN")
        self._state = _TransactionState.ACTIVE
        return self._session

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        del exc_value, traceback
        if self._state is not _TransactionState.ACTIVE:
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
            self._state = _TransactionState.FINISHED
        return False


class _WriteTransaction(AbstractAsyncContextManager[SqliteSession]):
    def __init__(self, session: SqliteSession) -> None:
        self._session = session
        self._state = _TransactionState.NEW

    async def __aenter__(self) -> SqliteSession:
        try:
            await self._session.execute("BEGIN IMMEDIATE")
        except BaseException:
            if self._session._connection.in_transaction:
                await asyncio.shield(self._session._connection.execute("ROLLBACK"))
            raise
        self._state = _TransactionState.ACTIVE
        return self._session

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        del exc_value, traceback
        if self._state is not _TransactionState.ACTIVE:
            return False
        statement = "ROLLBACK" if exc_type is not None else "COMMIT"
        try:
            await asyncio.shield(self._session._connection.execute(statement))
        except BaseException:
            if exc_type is None:
                await asyncio.shield(self._session._connection.execute("ROLLBACK"))
            raise
        finally:
            self._state = _TransactionState.FINISHED
        return False


@dataclass(frozen=True, slots=True)
class _Queued:
    pass


@dataclass(frozen=True, slots=True)
class _Executing[T]:
    task: asyncio.Task[T]


@dataclass(frozen=True, slots=True)
class _Cancelled[T]:
    task: asyncio.Task[T] | None = None


@dataclass(frozen=True, slots=True)
class _Finished:
    pass


@dataclass(slots=True)
class _WriteRequest[T]:
    operation: Callable[[SqliteSession], Awaitable[T]]
    transactional: bool
    result: asyncio.Future[T]
    done: asyncio.Event = field(default_factory=asyncio.Event)
    state: _Queued | _Executing[T] | _Cancelled[T] | _Finished = field(
        default_factory=_Queued
    )


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
        tasks: asyncio.TaskGroup,
    ) -> None:
        if not read_connections:
            raise ValueError("at least one read connection is required")
        self._tasks = tasks
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
        self._failure = TaskFailureSignal()
        self._reader_reaper_stop = asyncio.Event()
        self._state = _ExecutorState.CREATED
        self._borrowed_readers = 0
        self._opening_readers = 0
        self._reader_condition = asyncio.Condition()
        self._current_request: _WriteRequest[object] | None = None

    @property
    def is_started(self) -> bool:
        return self._writer_task is not None

    async def start(self) -> None:
        if self._writer_task is not None:
            return
        self._state = _ExecutorState.RUNNING
        self._failure.reset()
        self._reader_reaper_stop.clear()
        self._writer_task = self._tasks.create_task(
            self._failure.observe(self._run_writer(), component="SQLite writer"),
            name="bcn-sqlite-writer",
        )
        self._reader_reaper_task = self._tasks.create_task(
            self._failure.observe(
                self._reap_idle_readers(), component="SQLite reader reaper"
            ),
            name="bcn-sqlite-reader-reaper",
        )

    async def stop(self) -> None:
        if self._state is _ExecutorState.CLOSED:
            return
        writer_task = self._writer_task
        if writer_task is not None and writer_task.done():
            await self.abort()
            return
        self._failure.disable()
        self._state = _ExecutorState.STOPPING
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
        self._state = _ExecutorState.CLOSED

    async def abort(self) -> None:
        """Interrupt active work and close all connections after a failed drain."""
        if self._state is _ExecutorState.CLOSED:
            return
        self._failure.disable()
        self._state = _ExecutorState.STOPPING
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
            state = request.state
            operation_task = (
                state.task if isinstance(state, (_Executing, _Cancelled)) else None
            )
            if operation_task is not None and not operation_task.done():
                operation_task.cancel()
        await self._writer_connection.interrupt()
        for connection in tuple(self._read_connections):
            await connection.interrupt()

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
            await asyncio.gather(writer_task, return_exceptions=True)
            self._writer_task = None

        await self._stop_reader_reaper()
        for connection in tuple(self._read_connections):
            await connection.close()
        self._read_connections.clear()
        await self._writer_connection.close()
        self._state = _ExecutorState.CLOSED

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
            state = request.state
            operation_task = (
                state.task if isinstance(state, (_Executing, _Cancelled)) else None
            )
            request.state = _Cancelled(operation_task)
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
                if isinstance(request.state, _Cancelled):
                    if not request.result.done():
                        request.result.cancel()
                    continue
                operation_task = asyncio.create_task(
                    self._run_operation(request, session)
                )
                request.state = _Executing(operation_task)
                try:
                    value = await operation_task
                except asyncio.CancelledError as error:
                    if not request.result.done():
                        if isinstance(request.state, _Cancelled):
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
                request.state = _Finished()
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
            if self._state is _ExecutorState.RUNNING:
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
            if self._state is _ExecutorState.RUNNING:
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
                if self._state is not _ExecutorState.RUNNING:
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
                try:
                    await connection.close()
                except Exception:
                    _LOGGER.exception("SQLite idle reader close failed")

    async def wait_failure(self) -> None:
        await self._failure.wait()

    async def _stop_reader_reaper(self) -> None:
        task = self._reader_reaper_task
        if task is None:
            return
        self._reader_reaper_stop.set()
        await task
        self._reader_reaper_task = None

    def _require_accepting(self) -> None:
        if self._state is not _ExecutorState.RUNNING:
            raise SqliteExecutorClosedError("SQLite executor is not accepting work")


__all__ = [
    "SqliteExecuteResult",
    "SqliteExecutor",
    "SqliteExecutorClosedError",
    "SqliteQueryTimeoutError",
    "SqliteReadSession",
    "SqliteSession",
]
