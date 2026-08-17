from __future__ import annotations

import asyncio
import os
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from time import time_ns

import aiosqlite

from ...core.paths import resolve_data_dir
from ...core.storage import NodeIdentity
from .migrations import (
    RUNTIME_EVENTS_REMOVAL_MIGRATION,
    MigrationChecksumError,
    MigrationError,
    apply_migrations,
)
from .repository import SqliteTransaction

DATABASE_FILENAME = "bcn.sqlite3"
DEFAULT_BUSY_TIMEOUT_MS = 5_000


class SqliteDatabase:
    """Persistent SQLite foundation used by the storage repository adapter."""

    @property
    def name(self) -> str:
        return "sqlite"

    def __init__(
        self,
        *,
        busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
        database_name: str | None = None,
    ) -> None:
        if (
            isinstance(busy_timeout_ms, bool)
            or not isinstance(busy_timeout_ms, int)
            or busy_timeout_ms <= 0
        ):
            raise ValueError("busy_timeout_ms must be a positive integer")
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
        self._connection: aiosqlite.Connection | None = None
        self._schema_version: int | None = None
        self._agent_id: str | None = None
        self._agent_name = "default"
        self._lifecycle_lock = asyncio.Lock()
        self._transaction_lock = asyncio.Lock()

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
                    await asyncio.to_thread(
                        self.data_dir.mkdir,
                        parents=True,
                        exist_ok=True,
                        mode=0o700,
                    )
                    await asyncio.to_thread(_restrict_permissions, self.data_dir, 0o700)
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
                    await connection.create_function(
                        "bcn_agent_id",
                        0,
                        self._current_agent_id,
                        deterministic=False,
                    )
                    await connection.create_function(
                        "bcn_agent_name",
                        0,
                        self._current_agent_name,
                        deterministic=False,
                    )
                    await asyncio.to_thread(
                        _restrict_permissions, self.database_path, 0o600
                    )
                    self._connection = connection
                    async with SqliteTransaction(self) as transaction:
                        self._schema_version = await apply_migrations(
                            transaction,
                            clock=_current_time_ms,
                        )
                    compaction_row: aiosqlite.Row | None = None
                    async with SqliteTransaction(self) as transaction:
                        compaction_row = await transaction.fetchone(
                            "SELECT compaction_completed_at_ms "
                            "FROM schema_migrations WHERE version = ?",
                            (RUNTIME_EVENTS_REMOVAL_MIGRATION.version,),
                        )
                    if compaction_row is None:
                        raise MigrationError(
                            "runtime event removal migration is missing from ledger"
                        )
                    if compaction_row["compaction_completed_at_ms"] is None:
                        async with self._transaction_lock:
                            await connection.execute("VACUUM")
                        async with SqliteTransaction(self) as transaction:
                            await transaction.execute(
                                "UPDATE schema_migrations "
                                "SET compaction_completed_at_ms = ? WHERE version = ?",
                                (
                                    _current_time_ms(),
                                    RUNTIME_EVENTS_REMOVAL_MIGRATION.version,
                                ),
                            )
                    async with self._transaction_lock:
                        checkpoint_cursor = await connection.execute(
                            "PRAGMA wal_checkpoint(TRUNCATE)"
                        )
                        try:
                            checkpoint_row = await checkpoint_cursor.fetchone()
                        finally:
                            await checkpoint_cursor.close()
                    if checkpoint_row is None or checkpoint_row[0] != 0:
                        raise MigrationError("SQLite WAL checkpoint could not complete")
            except BaseException:
                self._connection = None
                self._schema_version = None
                self._agent_id = None
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
                self._schema_version = None
                self._agent_id = None

    async def initialize(
        self,
        *,
        node_id: str | None = None,
        workspace_id: str | None = None,
    ) -> NodeIdentity:
        """Bind the current single-Agent composition without persistent node state."""

        if node_id is not None and (not isinstance(node_id, str) or not node_id):
            raise ValueError("node_id must be a non-empty string")
        if not isinstance(workspace_id, str) or not workspace_id:
            raise ValueError("workspace_id must be a non-empty string")
        async with self._lifecycle_lock:
            self._require_connection()
            if self._agent_id is not None and self._agent_id != workspace_id:
                raise RuntimeError("SQLite storage is already bound to another agent")
            self._agent_id = workspace_id
            return NodeIdentity(
                node_id=node_id or f"bcn-agent-{workspace_id}",
                workspace_id=workspace_id,
            )

    def transaction(self) -> AbstractAsyncContextManager[SqliteTransaction]:
        return SqliteTransaction(self)

    def _require_connection(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise RuntimeError("SQLite database has not been started")
        return self._connection

    def _current_agent_id(self) -> str:
        if self._agent_id is None:
            raise RuntimeError("SQLite Agent scope has not been initialized")
        return self._agent_id

    def _current_agent_name(self) -> str:
        return self._agent_name


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
    "SqliteDatabase",
    "SqliteTransaction",
]
