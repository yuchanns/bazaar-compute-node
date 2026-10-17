"""SQLite sessions, connection ownership, and public storage errors."""

from __future__ import annotations

from contextlib import AsyncExitStack, asynccontextmanager, contextmanager
from enum import Enum, auto
from pathlib import Path
lazy import asyncio
lazy import os
lazy import sqlite3
lazy from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
lazy from typing import Self

import bazaar_contrib_storage as storage
from pydantic import BaseModel, ConfigDict, Field, model_validator
lazy import aiosqlite
lazy from jinja2 import Environment

from .compiler import Compiler, load_templates, schema_sql
from .executor import (
    SqliteExecutor,
    SqliteExecutorClosedError,
    SqliteQueryTimeoutError,
    SqliteSession,
)


class SqliteConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: Path | None = None
    busy_timeout_ms: int = Field(default=5_000, gt=0)
    max_idle_readers: int = Field(default=2, gt=0)
    max_readers: int | None = Field(default=None, gt=0)
    reader_idle_timeout: float = Field(default=60, gt=0, allow_inf_nan=False)
    shutdown_timeout: float = Field(default=20, gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def pool_size(self) -> Self:
        if self.max_readers is not None and self.max_readers < self.max_idle_readers:
            raise ValueError("max_readers must be at least max_idle_readers")
        return self


@contextmanager
def storage_errors() -> Iterator[None]:
    try:
        yield
    except SqliteExecutorClosedError as error:
        raise storage.StorageClosedError(str(error)) from error
    except SqliteQueryTimeoutError as error:
        raise storage.StorageQueryTimeoutError(str(error)) from error
    except sqlite3.IntegrityError as error:
        raise storage.StorageIntegrityError(str(error)) from error
    except sqlite3.OperationalError as error:
        code = getattr(error, "sqlite_errorcode", 0) & 0xFF
        if code in {
            sqlite3.SQLITE_BUSY,
            sqlite3.SQLITE_LOCKED,
            sqlite3.SQLITE_IOERR,
            sqlite3.SQLITE_CANTOPEN,
            sqlite3.SQLITE_FULL,
        }:
            raise storage.StorageUnavailableError(str(error)) from error
        if "no such module: fts5" in str(error) or "no such tokenizer: trigram" in str(
            error
        ):
            raise storage.StorageQueryError(
                "text search requires SQLite FTS5 with the trigram tokenizer"
            ) from error
        raise storage.StorageQueryError(str(error)) from error
    except sqlite3.Error as error:
        raise storage.StorageQueryError(str(error)) from error
    except OSError as error:
        raise storage.StorageUnavailableError(str(error)) from error


class _State(Enum):
    OPEN = auto()
    CLOSED = auto()


class Session:
    def __init__(
        self, executor: SqliteExecutor | SqliteSession, templates: Environment
    ) -> None:
        self._executor = executor
        self._templates = templates
        self._state = _State.OPEN

    def _require_open(self) -> None:
        if self._state is _State.CLOSED:
            raise storage.StorageClosedError("transaction session has closed")

    async def select(self, request: storage.Select) -> tuple[storage.Record, ...]:
        self._require_open()
        compiler = Compiler(self._templates)
        statement = compiler.render("select.sql.tpl", request=request)
        with storage_errors():
            rows = await self._executor.fetchall(statement, compiler.parameters)
        return tuple(dict(row) for row in rows)

    async def _mutation(self, statement: str, compiler: Compiler) -> storage.Mutation:
        self._require_open()
        with storage_errors():
            result = await self._executor.execute(statement, compiler.parameters)
        return storage.Mutation(
            count=max(result.rowcount, len(result.rows)),
            rows=tuple(dict(row) for row in result.rows),
        )

    async def copy(self, request: storage.Copy) -> storage.Mutation:
        compiler = Compiler(self._templates)
        statement = compiler.render("copy.sql.tpl", request=request)
        return await self._mutation(statement, compiler)

    async def table_exists(self, name: str) -> bool:
        self._require_open()
        with storage_errors():
            row = await self._executor.fetchone(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? "
                "UNION ALL SELECT 1 FROM sqlite_temp_master WHERE type = 'table' AND name = ?",
                (name, name),
            )
        return row is not None

    async def insert(self, request: storage.Insert) -> storage.Mutation:
        compiler = Compiler(self._templates)
        return await self._mutation(
            compiler.render("insert.sql.tpl", request=request), compiler
        )

    async def update(self, request: storage.Update) -> storage.Mutation:
        compiler = Compiler(self._templates)
        return await self._mutation(
            compiler.render("update.sql.tpl", request=request), compiler
        )

    async def delete(self, request: storage.Delete) -> storage.Mutation:
        compiler = Compiler(self._templates)
        return await self._mutation(
            compiler.render("delete.sql.tpl", request=request), compiler
        )

    async def apply_schema(
        self, operations: tuple[storage.SchemaOperation, ...]
    ) -> None:
        self._require_open()
        with storage_errors():
            for operation in operations:
                if (
                    isinstance(operation, storage.CreateIndex)
                    and operation.if_not_exists
                ):
                    row = await self._executor.fetchone(
                        "SELECT type FROM sqlite_master WHERE name = ?",
                        (operation.index.name,),
                    )
                    expected = "table" if operation.index.kind == "search" else "index"
                    if row is not None and row[0] == expected:
                        continue
                if isinstance(operation, storage.DropIndex):
                    row = await self._executor.fetchone(
                        "SELECT type FROM sqlite_master WHERE name = ?",
                        (operation.name,),
                    )
                    if row is not None and row[0] == "table":
                        triggers = await self._executor.fetchall(
                            "SELECT name FROM sqlite_master WHERE type = 'trigger' AND tbl_name = ? "
                            "AND (instr(sql, ?) > 0 OR instr(sql, ?) > 0 OR instr(sql, ?) > 0)",
                            (
                                operation.table,
                                'INSERT INTO "' + operation.name + '"',
                                "INSERT INTO " + operation.name + " ",
                                "INSERT INTO " + operation.name + "(",
                            ),
                        )
                        for trigger in triggers:
                            for statement in schema_sql(
                                storage.DropTrigger(
                                    name=trigger[0],
                                    table=operation.table,
                                    if_exists=True,
                                ),
                                self._templates,
                            ):
                                await self._executor.execute(statement)
                        for statement in schema_sql(
                            storage.DropTable(table=operation.name), self._templates
                        ):
                            await self._executor.execute(statement)
                        continue
                for statement in schema_sql(operation, self._templates):
                    await self._executor.execute(statement)


class SqliteDatabase(Session):
    def __init__(self, executor: SqliteExecutor, templates: Environment) -> None:
        super().__init__(executor, templates)
        self._writer = executor

    async def read[T](
        self,
        operation: Callable[[storage.Session], Awaitable[T]],
        *,
        snapshot: bool = False,
        query_budget: float | None = None,
    ) -> T:
        with storage_errors():
            async with self._writer.reader() as reader, AsyncExitStack() as stack:
                if snapshot:
                    await stack.enter_async_context(reader.transaction())
                if query_budget is not None:
                    await stack.enter_async_context(reader.query_budget(query_budget))
                bound = Session(reader, self._templates)
                try:
                    return await operation(bound)
                finally:
                    bound._state = _State.CLOSED

    async def write[T](
        self,
        operation: Callable[[storage.Session], Awaitable[T]],
        *,
        transactional: bool = False,
    ) -> T:
        async def apply(session: SqliteSession) -> T:
            bound = Session(session, self._templates)
            try:
                return await operation(bound)
            finally:
                bound._state = _State.CLOSED

        with storage_errors():
            if transactional:
                return await self._writer.transaction_write(apply)
            return await self._writer.write(apply)

    async def transaction[T](
        self, operation: Callable[[storage.Session], Awaitable[T]]
    ) -> T:
        return await self.write(operation, transactional=True)

    async def apply_schema(
        self, operations: tuple[storage.SchemaOperation, ...]
    ) -> None:
        await self.transaction(lambda session: session.apply_schema(operations))

    async def wait_failure(self) -> None:
        await self._writer.wait_failure()


async def _connect(
    path: Path, config: SqliteConfig, templates: Environment, *, query_only: bool
) -> aiosqlite.Connection:
    connection = await aiosqlite.connect(
        path, timeout=config.busy_timeout_ms / 1000, isolation_level=None
    )
    try:
        connection.row_factory = aiosqlite.Row
        await connection.execute("PRAGMA journal_mode = WAL")
        await connection.execute("PRAGMA synchronous = NORMAL")
        await connection.execute("PRAGMA foreign_keys = ON")
        await connection.execute(
            templates.get_template("busy_timeout.sql.tpl").render(
                timeout=config.busy_timeout_ms
            )
        )
        if query_only:
            await connection.execute("PRAGMA query_only = ON")
        return connection
    except BaseException:
        await connection.close()
        raise


@asynccontextmanager
async def open_database(config: SqliteConfig) -> AsyncIterator[SqliteDatabase]:
    templates = await asyncio.to_thread(load_templates)
    path = (
        config.path
        or Path(os.environ.get("BCN_HOME") or Path.home()) / ".bcn" / "bcn.sqlite3"
    )
    with storage_errors():
        path = await asyncio.to_thread(lambda: path.expanduser().resolve())
        await asyncio.to_thread(
            path.parent.mkdir, mode=0o700, parents=True, exist_ok=True
        )
        writer = await _connect(path, config, templates, query_only=False)
    readers: list[aiosqlite.Connection] = []
    try:
        with storage_errors():
            if os.name != "nt":
                await asyncio.to_thread(path.chmod, 0o600)
            for _ in range(config.max_idle_readers):
                readers.append(await _connect(path, config, templates, query_only=True))
        async with asyncio.TaskGroup() as tasks:
            executor = SqliteExecutor(
                writer,
                readers,
                reader_factory=lambda: _connect(
                    path, config, templates, query_only=True
                ),
                max_idle_readers=config.max_idle_readers,
                max_readers=config.max_readers,
                reader_idle_timeout=config.reader_idle_timeout,
                tasks=tasks,
            )
            await executor.start()
            try:
                yield SqliteDatabase(executor, templates)
            finally:
                try:
                    async with asyncio.timeout(config.shutdown_timeout):
                        await executor.stop()
                except BaseException:
                    await asyncio.shield(executor.abort())
                    raise
                finally:
                    tasks.cancel()
    finally:
        for reader in readers:
            await reader.close()
        await writer.close()
