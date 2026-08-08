from __future__ import annotations

import asyncio
import os
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from pathlib import Path
from time import time_ns
from uuid import uuid7

import aiosqlite

from ...core.paths import resolve_data_dir
from ...core.storage import NodeIdentity
from .migrations import (
    MigrationChecksumError,
    MigrationError,
    apply_migrations,
)
from .repository import SqliteTransaction

DATABASE_FILENAME = "bcn.sqlite3"
NODE_STATE_KEY = 1
DEFAULT_BUSY_TIMEOUT_MS = 5_000


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


class SqliteDatabase:
    """Persistent SQLite foundation used by the storage repository adapter."""

    @property
    def name(self) -> str:
        return "sqlite"

    def __init__(
        self,
        *,
        busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    ) -> None:
        if (
            isinstance(busy_timeout_ms, bool)
            or not isinstance(busy_timeout_ms, int)
            or busy_timeout_ms <= 0
        ):
            raise ValueError("busy_timeout_ms must be a positive integer")
        self.data_dir = resolve_data_dir()
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
                    async with SqliteTransaction(self) as transaction:
                        self._schema_version = await apply_migrations(
                            transaction,
                            clock=_current_time_ms,
                        )
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
