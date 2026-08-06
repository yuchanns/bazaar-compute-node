from __future__ import annotations

import asyncio
import os
from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from pathlib import Path
from time import monotonic_ns, time_ns
from types import TracebackType
from typing import Self
from uuid import uuid7

import aiosqlite

from ...core.paths import resolve_data_dir
from ...core.storage import NodeIdentity
from .migrations import MIGRATIONS

DATABASE_FILENAME = "bcn.sqlite3"
NODE_STATE_KEY = 1
DEFAULT_BUSY_TIMEOUT_MS = 5_000


class MigrationError(RuntimeError):
    """The database cannot be safely brought to the application schema."""


class MigrationChecksumError(MigrationError):
    """A migration ledger entry no longer matches the application migration."""


class NodeIdentityError(MigrationError):
    """The persistent node identity does not match the requested identity."""


@dataclass(frozen=True, slots=True)
class NodeState:
    node_id: str
    schema_version: int
    workspace_id: str
    created_at_ms: int
    updated_at_ms: int
    metadata_json: str


class SqliteTransaction(AbstractAsyncContextManager["SqliteTransaction"]):
    """An explicit IMMEDIATE transaction on the database's long-lived connection."""

    def __init__(self, database: SqliteDatabase) -> None:
        self._database = database
        self._connection: aiosqlite.Connection | None = None
        self._active = False

    async def __aenter__(self) -> Self:
        await self._database._transaction_lock.acquire()
        try:
            connection = self._database._require_connection()
            await connection.execute("BEGIN IMMEDIATE")
        except BaseException:
            self._database._transaction_lock.release()
            raise
        self._connection = connection
        self._active = True
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        connection = self._connection
        if not self._active or connection is None:
            return False
        try:
            await connection.execute("ROLLBACK" if exc_type is not None else "COMMIT")
        except (Exception, asyncio.CancelledError) as error:
            if exc_type is None:
                try:
                    await connection.execute("ROLLBACK")
                except (Exception, asyncio.CancelledError) as rollback_error:
                    raise error from rollback_error
            raise
        finally:
            self._active = False
            self._connection = None
            self._database._transaction_lock.release()
        return False

    async def execute(
        self,
        statement: str,
        parameters: Sequence[object] = (),
    ) -> aiosqlite.Cursor:
        connection = self._require_active_connection()
        return await connection.execute(statement, parameters)

    async def fetchone(
        self,
        statement: str,
        parameters: Sequence[object] = (),
    ) -> aiosqlite.Row | None:
        cursor = await self.execute(statement, parameters)
        try:
            return await cursor.fetchone()
        finally:
            await cursor.close()

    async def fetchall(
        self,
        statement: str,
        parameters: Sequence[object] = (),
    ) -> list[aiosqlite.Row]:
        cursor = await self.execute(statement, parameters)
        try:
            return list(await cursor.fetchall())
        finally:
            await cursor.close()

    def _require_active_connection(self) -> aiosqlite.Connection:
        if not self._active or self._connection is None:
            raise RuntimeError("SQLite transaction is not active")
        return self._connection


class SqliteDatabase:
    """Persistent SQLite foundation used by the storage repository adapter."""

    def __init__(
        self,
        data_dir: Path | str | None = None,
        *,
        busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    ) -> None:
        if (
            isinstance(busy_timeout_ms, bool)
            or not isinstance(busy_timeout_ms, int)
            or busy_timeout_ms <= 0
        ):
            raise ValueError("busy_timeout_ms must be a positive integer")
        self.data_dir = resolve_data_dir(data_dir)
        self.database_path = self.data_dir / DATABASE_FILENAME
        self._busy_timeout_ms = busy_timeout_ms
        self._connection: aiosqlite.Connection | None = None
        self._node_state: NodeState | None = None
        self._schema_version: int | None = None
        self._lifecycle_lock = asyncio.Lock()
        self._transaction_lock = asyncio.Lock()

    @property
    def node_state(self) -> NodeState:
        if self._node_state is None:
            raise RuntimeError("SQLite node identity has not been initialized")
        return self._node_state

    @property
    def node_id(self) -> str:
        return self.node_state.node_id

    @property
    def workspace_id(self) -> str:
        return self.node_state.workspace_id

    @property
    def is_started(self) -> bool:
        return self._connection is not None

    async def start(self, *, timeout: float) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        async with self._lifecycle_lock:
            if self._connection is not None:
                return
            connection: aiosqlite.Connection | None = None
            try:
                async with asyncio.timeout(timeout):
                    self.data_dir.mkdir(
                        parents=True,
                        exist_ok=True,
                        mode=0o700,
                    )
                    _restrict_permissions(self.data_dir, 0o700)
                    connection = await aiosqlite.connect(
                        self.database_path,
                        timeout=self._busy_timeout_ms / 1000,
                        isolation_level=None,
                    )
                    connection.row_factory = aiosqlite.Row
                    await connection.execute("PRAGMA journal_mode = WAL")
                    await connection.execute("PRAGMA synchronous = NORMAL")
                    await connection.execute("PRAGMA foreign_keys = ON")
                    await connection.execute(
                        f"PRAGMA busy_timeout = {self._busy_timeout_ms}"
                    )
                    _restrict_permissions(self.database_path, 0o600)
                    self._connection = connection
                    self._schema_version = await self._bootstrap()
            except BaseException:
                self._connection = None
                self._node_state = None
                self._schema_version = None
                if connection is not None:
                    await connection.close()
                raise

    async def stop(self, *, timeout: float) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        async with self._lifecycle_lock:
            connection = self._connection
            if connection is None:
                return
            try:
                async with asyncio.timeout(timeout):
                    async with self._transaction_lock:
                        await connection.close()
            finally:
                self._connection = None
                self._node_state = None
                self._schema_version = None

    async def initialize(
        self,
        *,
        node_id: str | None = None,
        workspace_id: str | None = None,
    ) -> NodeIdentity:
        if node_id is not None and (not isinstance(node_id, str) or not node_id):
            raise ValueError("node_id must be a non-empty string")
        if workspace_id is not None and (
            not isinstance(workspace_id, str) or not workspace_id
        ):
            raise ValueError("workspace_id must be a non-empty string")
        async with self._lifecycle_lock:
            self._require_connection()
            schema_version = self._schema_version
            if schema_version is None:
                raise RuntimeError("SQLite schema has not been initialized")
            state: NodeState | None = None
            async with SqliteTransaction(self) as transaction:
                state = await self._ensure_node_state(
                    transaction,
                    schema_version,
                    requested_node_id=node_id,
                    requested_workspace_id=workspace_id,
                )
            if state is None:
                raise RuntimeError("SQLite node initialization did not create state")
            self._node_state = state
            return NodeIdentity(
                node_id=state.node_id,
                workspace_id=state.workspace_id,
            )

    def transaction(self) -> AbstractAsyncContextManager[SqliteTransaction]:
        return SqliteTransaction(self)

    def _require_connection(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise RuntimeError("SQLite database has not been started")
        return self._connection

    async def _bootstrap(self) -> int:
        schema_version: int | None = None
        async with SqliteTransaction(self) as transaction:
            schema_version = await self._apply_migrations(transaction)
        if schema_version is None:
            raise RuntimeError("SQLite bootstrap did not produce a schema version")
        return schema_version

    async def _apply_migrations(self, transaction: SqliteTransaction) -> int:
        ledger_exists = (
            await transaction.fetchone(
                "SELECT 1 FROM sqlite_master "
                "WHERE type = 'table' AND name = 'schema_migrations'"
            )
            is not None
        )
        applied_rows: list[aiosqlite.Row] = []
        if ledger_exists:
            applied_rows = await transaction.fetchall(
                "SELECT version, migration_name, checksum "
                "FROM schema_migrations ORDER BY version"
            )
            known_versions = {migration.version for migration in MIGRATIONS}
            unknown_versions = {
                int(row["version"])
                for row in applied_rows
                if int(row["version"]) not in known_versions
            }
            if unknown_versions:
                raise MigrationError(
                    "database contains unknown migration versions: "
                    + ", ".join(str(version) for version in sorted(unknown_versions))
                )

        applied_by_version = {int(row["version"]): row for row in applied_rows}
        latest_version = 0
        for migration in MIGRATIONS:
            row = applied_by_version.get(migration.version)
            if row is not None:
                if (
                    row["migration_name"] != migration.name
                    or row["checksum"] != migration.checksum
                ):
                    raise MigrationChecksumError(
                        f"migration {migration.version} does not match its ledger entry"
                    )
                latest_version = migration.version
                continue

            if ledger_exists:
                raise MigrationError(
                    f"migration ledger is missing version {migration.version}"
                )
            started_at_ns = monotonic_ns()
            for statement in migration.statements:
                await transaction.execute(statement)
            await transaction.execute(
                "INSERT INTO schema_migrations "
                "(version, migration_name, checksum, applied_at_ms, duration_ms) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    migration.version,
                    migration.name,
                    migration.checksum,
                    _current_time_ms(),
                    (monotonic_ns() - started_at_ns) // 1_000_000,
                ),
            )
            ledger_exists = True
            latest_version = migration.version

        return latest_version

    async def _ensure_node_state(
        self,
        transaction: SqliteTransaction,
        schema_version: int,
        *,
        requested_node_id: str | None,
        requested_workspace_id: str | None,
    ) -> NodeState:
        row = await transaction.fetchone(
            "SELECT node_id, schema_version, workspace_id, created_at_ms, "
            "updated_at_ms, metadata_json FROM node_state "
            "WHERE singleton_key = ?",
            (NODE_STATE_KEY,),
        )
        now_ms = _current_time_ms()
        if row is None:
            node_id = requested_node_id or f"bcn-node-{uuid7()}"
            workspace_id = requested_workspace_id or str(uuid7())
            metadata_json = "{}"
            await transaction.execute(
                "INSERT INTO node_state "
                "(singleton_key, node_id, schema_version, workspace_id, "
                "created_at_ms, updated_at_ms, metadata_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    NODE_STATE_KEY,
                    node_id,
                    schema_version,
                    workspace_id,
                    now_ms,
                    now_ms,
                    metadata_json,
                ),
            )
            return NodeState(
                node_id=node_id,
                schema_version=schema_version,
                workspace_id=workspace_id,
                created_at_ms=now_ms,
                updated_at_ms=now_ms,
                metadata_json=metadata_json,
            )

        node_id = row["node_id"]
        workspace_id = row["workspace_id"]
        if not isinstance(node_id, str) or not node_id:
            raise NodeIdentityError("persistent node_id is missing")
        if not isinstance(workspace_id, str) or not workspace_id:
            raise NodeIdentityError("persistent workspace_id is missing")
        if requested_node_id is not None and node_id != requested_node_id:
            raise NodeIdentityError(
                f"requested node_id does not match persisted node_id: {node_id}"
            )
        if (
            requested_workspace_id is not None
            and workspace_id != requested_workspace_id
        ):
            raise NodeIdentityError(
                "requested workspace_id does not match the persisted workspace_id"
            )
        if row["schema_version"] != schema_version:
            await transaction.execute(
                "UPDATE node_state SET schema_version = ?, updated_at_ms = ? "
                "WHERE singleton_key = ?",
                (schema_version, now_ms, NODE_STATE_KEY),
            )
            updated_at_ms = now_ms
        else:
            updated_at_ms = int(row["updated_at_ms"])
        return NodeState(
            node_id=node_id,
            schema_version=schema_version,
            workspace_id=workspace_id,
            created_at_ms=int(row["created_at_ms"]),
            updated_at_ms=updated_at_ms,
            metadata_json=row["metadata_json"] or "{}",
        )


def _current_time_ms() -> int:
    return time_ns() // 1_000_000


def _restrict_permissions(path: Path, mode: int) -> None:
    if os.name != "nt":
        path.chmod(mode)


__all__ = [
    "DATABASE_FILENAME",
    "DEFAULT_BUSY_TIMEOUT_MS",
    "MigrationChecksumError",
    "MigrationError",
    "NodeIdentityError",
    "NodeState",
    "SqliteDatabase",
    "SqliteTransaction",
]
