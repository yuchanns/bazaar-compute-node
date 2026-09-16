"""The server's storage on sqlite."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid7

import aiosqlite

from ...clock import now_ms
from ...protocol import Event
from ...secrets import hash_secret, new_secret, verify_secret
from ...storage import Computer, Enrolment, IStorage
from .migrations.registry import apply_migrations

_DAY_MS = 24 * 60 * 60 * 1000


# readers kept open between requests; more are opened while they are needed
# and closed on return, the same shape as the node's own sqlite layer
_IDLE_READERS = 2


class SqliteStorage(IStorage):
    """One connection writes, readers come and go.

    Every aiosqlite connection is its own thread. sqlite serialises writers
    on the file, so one writing connection means our writes queue in order
    instead of contending; a reader in WAL mode runs alongside it, and the
    reads themselves happen in sqlite's C with the GIL released.
    """

    @property
    def name(self) -> str:
        return "sqlite"

    def __init__(self, path: Path, *, retention_days: int) -> None:
        self._path = path
        self._retention_ms = retention_days * _DAY_MS
        self._writer: aiosqlite.Connection | None = None
        self._idle_readers: list[aiosqlite.Connection] = []
        self._last_sweep_ms = 0

    async def start(self) -> None:
        await asyncio.to_thread(self._path.parent.mkdir, parents=True, exist_ok=True)
        writer = await self._connect()
        await apply_migrations(writer)
        self._writer = writer
        await self.sweep()

    async def stop(self) -> None:
        readers, self._idle_readers = self._idle_readers, []
        writer, self._writer = self._writer, None
        for connection in (*readers, *([writer] if writer is not None else [])):
            await connection.close()

    @asynccontextmanager
    async def _reader(self) -> AsyncIterator[aiosqlite.Connection]:
        connection = (
            self._idle_readers.pop() if self._idle_readers else await self._connect()
        )
        try:
            yield connection
        finally:
            if self._writer is not None and len(self._idle_readers) < _IDLE_READERS:
                self._idle_readers.append(connection)
            else:
                await connection.close()

    async def _connect(self) -> aiosqlite.Connection:
        connection = await aiosqlite.connect(self._path)
        connection.row_factory = aiosqlite.Row
        await connection.execute("PRAGMA journal_mode=WAL")
        await connection.execute("PRAGMA foreign_keys=ON")
        return connection

    # ---- computers -------------------------------------------------------

    async def add_computer(self, name: str) -> Enrolment:
        computer = Computer(id=str(uuid7()), name=name, created_at_ms=now_ms())
        secret = new_secret()
        # scrypt is deliberately slow; the event loop must not wait on it
        secret_hash = await asyncio.to_thread(hash_secret, secret)
        await self._db.execute(
            "INSERT INTO computers (id, name, secret_hash, created_at_ms)"
            " VALUES (?, ?, ?, ?)",
            (computer.id, computer.name, secret_hash, computer.created_at_ms),
        )
        await self._db.commit()
        return Enrolment(computer=computer, token=f"{computer.id}:{secret}")

    async def list_computers(
        self, *, limit: int, after: Computer | None = None
    ) -> list[Computer]:
        # keyset paging on (created_at_ms, id): the page after a row is the
        # rows past it, whatever was added or removed meanwhile
        async with (
            self._reader() as reader,
            reader.execute(
                "SELECT id, name, created_at_ms FROM computers"
                " WHERE (created_at_ms, id) > (?, ?)"
                " ORDER BY created_at_ms, id LIMIT ?",
                (
                    -1 if after is None else after.created_at_ms,
                    "" if after is None else after.id,
                    limit,
                ),
            ) as cursor,
        ):
            return [
                Computer(
                    id=row["id"], name=row["name"], created_at_ms=row["created_at_ms"]
                )
                async for row in cursor
            ]

    async def authenticate(self, token: str) -> Computer | None:
        """The computer a token speaks for, or nothing for any other token."""

        computer_id, separator, secret = token.partition(":")
        if not separator or not computer_id or not secret:
            return None
        async with (
            self._reader() as reader,
            reader.execute(
                "SELECT id, name, secret_hash, created_at_ms FROM computers WHERE id = ?",
                (computer_id,),
            ) as cursor,
        ):
            row = await cursor.fetchone()
        if row is None or not await asyncio.to_thread(
            verify_secret, secret, row["secret_hash"]
        ):
            return None
        return Computer(
            id=row["id"], name=row["name"], created_at_ms=row["created_at_ms"]
        )

    # ---- events ----------------------------------------------------------

    async def record_events(
        self, computer_id: str, run_id: str, events: Sequence[Event]
    ) -> int:
        """Keep the events that are new; say how many those were."""

        received_at_ms = now_ms()
        rows = [
            (
                computer_id,
                run_id,
                event.seq,
                event.correlation.get("node_id"),
                event.event_name,
                event.correlation.get("thread_id"),
                event.created_at_ms,
                received_at_ms,
                json.dumps(
                    event.model_dump(exclude={"seq"}),
                    ensure_ascii=False,
                    separators=(",", ":"),
                    default=str,
                ),
            )
            for event in events
        ]
        before = self._db.total_changes
        await self._db.executemany(
            "INSERT OR IGNORE INTO events (computer_id, run_id, seq, agent_id,"
            " event_name, thread_id, created_at_ms, received_at_ms, payload)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        accepted = self._db.total_changes - before
        # only the newest health record says anything a consumer wants
        await self._db.execute(
            "DELETE FROM events WHERE computer_id = ? AND event_name = 'node.health'"
            " AND id < (SELECT MAX(id) FROM events"
            " WHERE computer_id = ? AND event_name = 'node.health')",
            (computer_id, computer_id),
        )
        await self._db.commit()
        if received_at_ms - self._last_sweep_ms >= _DAY_MS:
            await self.sweep()
        return accepted

    async def sweep(self) -> None:
        """Drop what is older than the retention, except the last health beat."""

        now = now_ms()
        await self._db.execute(
            "DELETE FROM events WHERE received_at_ms < ? AND NOT ("
            " event_name = 'node.health' AND id IN ("
            " SELECT MAX(id) FROM events WHERE event_name = 'node.health'"
            " GROUP BY computer_id))",
            (now - self._retention_ms,),
        )
        await self._db.commit()
        self._last_sweep_ms = now

    async def count_events(self, computer_id: str) -> int:
        async with (
            self._reader() as reader,
            reader.execute(
                "SELECT COUNT(*) AS n FROM events WHERE computer_id = ?", (computer_id,)
            ) as cursor,
        ):
            row = await cursor.fetchone()
        return 0 if row is None else int(row["n"])

    @property
    def _db(self) -> aiosqlite.Connection:
        """The one connection that writes."""

        if self._writer is None:
            raise RuntimeError("storage is not started")
        return self._writer


__all__ = ["SqliteStorage"]
