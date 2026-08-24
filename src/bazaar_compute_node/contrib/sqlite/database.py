from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable, Iterable, Sequence
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from time import time_ns
from typing import TypeVar

import aiosqlite

from ...core.paths import resolve_data_dir
from .executor import (
    SqliteExecuteResult,
    SqliteExecutor,
    SqliteReadSession,
    SqliteSession,
)
from .migrations import (
    RUNTIME_EVENTS_REMOVAL_MIGRATION,
    MigrationChecksumError,
    MigrationError,
    apply_migrations,
)

DATABASE_FILENAME = "bcn.sqlite3"
DEFAULT_BUSY_TIMEOUT_MS = 5_000
DEFAULT_MAX_IDLE_READERS = 2
DEFAULT_MAX_READERS: int | None = None
DEFAULT_READER_IDLE_TIMEOUT = 60.0

T = TypeVar("T")


class SqliteDatabase:
    """Lifecycle and SQL execution façade for the SQLite adapter."""

    @property
    def name(self) -> str:
        return "sqlite"

    def __init__(
        self,
        *,
        busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
        database_name: str | None = None,
        max_idle_readers: int = DEFAULT_MAX_IDLE_READERS,
        max_readers: int | None = DEFAULT_MAX_READERS,
        reader_idle_timeout: float = DEFAULT_READER_IDLE_TIMEOUT,
    ) -> None:
        if (
            isinstance(busy_timeout_ms, bool)
            or not isinstance(busy_timeout_ms, int)
            or busy_timeout_ms <= 0
        ):
            raise ValueError("busy_timeout_ms must be a positive integer")
        if (
            isinstance(max_idle_readers, bool)
            or not isinstance(max_idle_readers, int)
            or max_idle_readers <= 0
        ):
            raise ValueError("max_idle_readers must be a positive integer")
        if max_readers is not None and (
            isinstance(max_readers, bool)
            or not isinstance(max_readers, int)
            or max_readers < max_idle_readers
        ):
            raise ValueError("max_readers must be at least max_idle_readers")
        if (
            isinstance(reader_idle_timeout, bool)
            or not isinstance(reader_idle_timeout, (int, float))
            or reader_idle_timeout <= 0
        ):
            raise ValueError("reader_idle_timeout must be positive")
        database_name = DATABASE_FILENAME if database_name is None else database_name
        if (
            not database_name
            or database_name in {".", ".."}
            or "/" in database_name
            or "\\" in database_name
        ):
            raise ValueError("database_name must be a single path component")
        self.data_dir = resolve_data_dir()
        self.database_path = self.data_dir / database_name
        self._busy_timeout_ms = busy_timeout_ms
        self._max_idle_readers = max_idle_readers
        self._max_readers = max_readers
        self._reader_idle_timeout = float(reader_idle_timeout)
        self._executor: SqliteExecutor | None = None
        self._schema_version: int | None = None
        self._lifecycle_lock = asyncio.Lock()

    @property
    def is_started(self) -> bool:
        return self._executor is not None

    async def start(self, *, timeout: float) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        async with self._lifecycle_lock:
            if self._executor is not None:
                return
            writer: aiosqlite.Connection | None = None
            readers: list[aiosqlite.Connection] = []
            executor: SqliteExecutor | None = None
            try:
                async with asyncio.timeout(timeout):
                    await asyncio.to_thread(
                        self.data_dir.mkdir,
                        parents=True,
                        exist_ok=True,
                        mode=0o700,
                    )
                    await asyncio.to_thread(_restrict_permissions, self.data_dir, 0o700)
                    writer = await self._open_connection(query_only=False)
                    await asyncio.to_thread(
                        _restrict_permissions,
                        self.database_path,
                        0o600,
                    )
                    writer_session = SqliteSession(writer)
                    await writer.execute("BEGIN IMMEDIATE")
                    try:
                        self._schema_version = await apply_migrations(
                            writer_session,
                            clock=_current_time_ms,
                        )
                    except BaseException:
                        await writer.execute("ROLLBACK")
                        raise
                    else:
                        await writer.execute("COMMIT")

                    compaction_row = await writer_session.fetchone(
                        "SELECT compaction_completed_at_ms "
                        "FROM schema_migrations WHERE version = ?",
                        (RUNTIME_EVENTS_REMOVAL_MIGRATION.version,),
                    )
                    if compaction_row is None:
                        raise MigrationError(
                            "runtime event removal migration is missing from ledger"
                        )
                    if compaction_row["compaction_completed_at_ms"] is None:
                        await writer.execute("VACUUM")
                        await writer_session.execute(
                            "UPDATE schema_migrations "
                            "SET compaction_completed_at_ms = ? WHERE version = ?",
                            (
                                _current_time_ms(),
                                RUNTIME_EVENTS_REMOVAL_MIGRATION.version,
                            ),
                        )
                    checkpoint_row = await writer_session.fetchone(
                        "PRAGMA wal_checkpoint(TRUNCATE)"
                    )
                    if checkpoint_row is None or checkpoint_row[0] != 0:
                        raise MigrationError("SQLite WAL checkpoint could not complete")

                    for _ in range(self._max_idle_readers):
                        readers.append(await self._open_connection(query_only=True))
                    executor = SqliteExecutor(
                        writer,
                        readers,
                        reader_factory=lambda: self._open_connection(query_only=True),
                        max_idle_readers=self._max_idle_readers,
                        max_readers=self._max_readers,
                        reader_idle_timeout=self._reader_idle_timeout,
                    )
                    await executor.start()
                    self._executor = executor
            except BaseException:
                self._executor = None
                self._schema_version = None
                if executor is not None and executor.is_started:
                    await executor.stop()
                else:
                    for connection in readers:
                        await connection.close()
                    if writer is not None:
                        await writer.close()
                raise

    async def stop(self, *, timeout: float) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        async with self._lifecycle_lock:
            executor = self._executor
            if executor is None:
                return
            try:
                async with asyncio.timeout(timeout):
                    await executor.stop()
            except TimeoutError:
                await asyncio.shield(executor.abort())
                raise
            finally:
                self._executor = None
                self._schema_version = None

    async def fetchone(
        self,
        statement: str,
        parameters: Sequence[object] = (),
    ) -> aiosqlite.Row | None:
        return await self._require_executor().fetchone(statement, parameters)

    async def fetchall(
        self,
        statement: str,
        parameters: Sequence[object] = (),
    ) -> list[aiosqlite.Row]:
        return await self._require_executor().fetchall(statement, parameters)

    async def execute(
        self,
        statement: str,
        parameters: Sequence[object] = (),
    ) -> SqliteExecuteResult:
        return await self._require_executor().execute(statement, parameters)

    async def executemany(
        self,
        statement: str,
        parameter_sets: Iterable[Sequence[object]],
    ) -> SqliteExecuteResult:
        return await self._require_executor().executemany(statement, parameter_sets)

    def reader(self) -> AbstractAsyncContextManager[SqliteReadSession]:
        return self._require_executor().reader()

    async def transaction_write(
        self,
        operation: Callable[[SqliteSession], Awaitable[T]],
    ) -> T:
        return await self._require_executor().transaction_write(operation)

    async def _write(
        self,
        operation: Callable[[SqliteSession], Awaitable[T]],
    ) -> T:
        return await self._require_executor().write(operation)

    async def _open_connection(
        self,
        *,
        query_only: bool,
    ) -> aiosqlite.Connection:
        connection = await aiosqlite.connect(
            self.database_path,
            timeout=self._busy_timeout_ms / 1000,
            isolation_level=None,
        )
        connection.row_factory = aiosqlite.Row
        await connection.execute("PRAGMA journal_mode = WAL")
        await connection.execute("PRAGMA synchronous = NORMAL")
        await connection.execute("PRAGMA foreign_keys = ON")
        await connection.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
        if query_only:
            await connection.execute("PRAGMA query_only = ON")
        return connection

    def _require_executor(self) -> SqliteExecutor:
        executor = self._executor
        if executor is None:
            raise RuntimeError("SQLite database has not been started")
        return executor


def _current_time_ms() -> int:
    return time_ns() // 1_000_000


def _restrict_permissions(path: Path, mode: int) -> None:
    if os.name != "nt":
        path.chmod(mode)


__all__ = [
    "DATABASE_FILENAME",
    "DEFAULT_BUSY_TIMEOUT_MS",
    "DEFAULT_MAX_IDLE_READERS",
    "DEFAULT_MAX_READERS",
    "DEFAULT_READER_IDLE_TIMEOUT",
    "MigrationChecksumError",
    "MigrationError",
    "SqliteDatabase",
]
