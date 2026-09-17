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
from ...storage import (
    Computer,
    ComputerHealth,
    Enrolment,
    IStorage,
    StoredEvent,
    ThreadKey,
)
from .migrations.registry import apply_migrations

_DAY_MS = 24 * 60 * 60 * 1000


# readers kept open between requests; more are opened while they are needed
# and closed on return, the same shape as the node's own sqlite layer
_IDLE_READERS = 2
_COLUMNS = (
    "id, computer_id, agent_id, event_name, thread_id,"
    " created_at_ms, received_at_ms, payload"
)


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

    async def computer_health(
        self, computers: Sequence[Computer]
    ) -> list[ComputerHealth]:
        if not computers:
            return []
        ids = [computer.id for computer in computers]
        # two independent reads, each on its own reader
        health, last = await asyncio.gather(
            self._latest_health(ids), self._last_received(ids)
        )
        return [
            ComputerHealth(
                computer=computer,
                health=health.get(computer.id),
                last_event_at_ms=last.get(computer.id),
            )
            for computer in computers
        ]

    async def _latest_health(self, ids: Sequence[str]) -> dict[str, StoredEvent]:
        async with (
            self._reader() as reader,
            reader.execute(
                f"SELECT {_COLUMNS} FROM events WHERE id IN ("
                " SELECT MAX(id) FROM events WHERE event_name = 'node.health'"
                f" AND computer_id IN ({_marks(ids)}) GROUP BY computer_id)",
                ids,
            ) as cursor,
        ):
            return {row["computer_id"]: _stored(row) async for row in cursor}

    async def _last_received(self, ids: Sequence[str]) -> dict[str, int]:
        async with (
            self._reader() as reader,
            reader.execute(
                "SELECT computer_id, MAX(received_at_ms) AS at FROM events"
                f" WHERE computer_id IN ({_marks(ids)}) GROUP BY computer_id",
                ids,
            ) as cursor,
        ):
            return {row["computer_id"]: row["at"] async for row in cursor}

    async def recent_activity(
        self, computer_id: str, agent_id: str, *, limit: int
    ) -> list[StoredEvent]:
        async with (
            self._reader() as reader,
            reader.execute(
                f"SELECT {_COLUMNS} FROM events"
                " WHERE computer_id = ? AND agent_id = ?"
                " AND event_name != 'node.health'"
                " ORDER BY id DESC LIMIT ?",
                (computer_id, agent_id, limit),
            ) as cursor,
        ):
            return [_stored(row) async for row in cursor]

    async def latest_per_agent(
        self, computer_ids: Sequence[str], names: Sequence[str]
    ) -> list[StoredEvent]:
        if not computer_ids or not names:
            return []
        async with (
            self._reader() as reader,
            reader.execute(
                f"SELECT {_COLUMNS} FROM events WHERE id IN ("
                f" SELECT MAX(id) FROM events WHERE event_name IN ({_marks(names)})"
                f" AND computer_id IN ({_marks(computer_ids)})"
                " GROUP BY computer_id, agent_id)",
                (*names, *computer_ids),
            ) as cursor,
        ):
            return [_stored(row) async for row in cursor]

    async def latest_per_thread(
        self, name: str, threads: Sequence[ThreadKey]
    ) -> list[StoredEvent]:
        if not threads:
            return []
        rows = ", ".join("(?, ?, ?)" for _ in threads)
        async with (
            self._reader() as reader,
            reader.execute(
                f"SELECT {_COLUMNS} FROM events WHERE id IN ("
                " SELECT MAX(id) FROM events WHERE event_name = ?"
                f" AND (computer_id, agent_id, thread_id) IN (VALUES {rows})"
                " GROUP BY computer_id, agent_id, thread_id)",
                (name, *(part for key in threads for part in key)),
            ) as cursor,
        ):
            return [_stored(row) async for row in cursor]

    async def usage_since(
        self, computer_id: str, agent_id: str, since_ms: int
    ) -> list[StoredEvent]:
        # the newest usage row per runtime session is that session's running
        # total, so one row per session is the whole picture
        async with (
            self._reader() as reader,
            reader.execute(
                f"SELECT {_COLUMNS} FROM events"
                " WHERE id IN ("
                "  SELECT MAX(id) FROM events"
                "  WHERE computer_id = ? AND agent_id = ?"
                "  AND event_name = 'usage.updated' AND created_at_ms >= ?"
                "  GROUP BY json_extract(payload, '$.correlation.runtime_session_id'))",
                (computer_id, agent_id, since_ms),
            ) as cursor,
        ):
            return [_stored(row) async for row in cursor]

    @property
    def _db(self) -> aiosqlite.Connection:
        """The one connection that writes."""

        if self._writer is None:
            raise RuntimeError("storage is not started")
        return self._writer


def _marks(values: Sequence[object]) -> str:
    """One placeholder per value, for an IN list."""

    return ", ".join("?" for _ in values)


def _stored(row: aiosqlite.Row) -> StoredEvent:
    return StoredEvent(
        id=row["id"],
        computer_id=row["computer_id"],
        agent_id=row["agent_id"],
        event_name=row["event_name"],
        thread_id=row["thread_id"],
        created_at_ms=row["created_at_ms"],
        received_at_ms=row["received_at_ms"],
        payload=json.loads(row["payload"]),
    )


__all__ = ["SqliteStorage"]
