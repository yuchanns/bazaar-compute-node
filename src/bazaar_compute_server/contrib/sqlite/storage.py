"""The server's storage on sqlite."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid7

import aiosqlite

from ...clock import now_ms
from ...protocol import Event
from ...secrets import derive, hash_secret, new_secret, verify_secret
from ...storage import (
    Account,
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
# a hash nobody's password matches, verified for names nobody has
_NOBODY = hash_secret(new_secret())

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
        # writes queue up for the one writer task, so each runs whole and in
        # turn; the queue is what keeps two requests' statements apart
        self._writes: asyncio.Queue[_Write[Any] | None] = asyncio.Queue()
        self._writing: asyncio.Task[None] | None = None

    async def start(self) -> None:
        # the database holds message text and credential hashes: the directory
        # is the owner's alone, made so or made so again
        await asyncio.to_thread(_private_directory, self._path.parent)
        writer = await self._connect()
        await apply_migrations(writer)
        self._writer = writer
        self._writing = asyncio.create_task(
            self._run_writes(), name="bcs-sqlite-writer"
        )
        await self.sweep()

    async def stop(self) -> None:
        if self._writing is not None:
            await self._writes.put(None)
            await self._writing
            self._writing = None
        readers, self._idle_readers = self._idle_readers, []
        writer, self._writer = self._writer, None
        for connection in (*readers, *([writer] if writer is not None else [])):
            await connection.close()

    async def _write[T](
        self, operation: Callable[[aiosqlite.Connection], Awaitable[T]]
    ) -> T:
        """Run one write on the writer connection, after those before it,
        as one transaction."""

        if self._writing is None:
            raise RuntimeError("storage is not started")
        pending = _Write(operation, asyncio.get_running_loop().create_future())
        await self._writes.put(pending)
        return await pending.result

    async def _run_writes(self) -> None:
        while (pending := await self._writes.get()) is not None:
            writer = self._writer
            if writer is None:
                if not pending.result.done():
                    pending.result.set_exception(RuntimeError("storage is stopping"))
                continue
            try:
                value = await pending.operation(writer)
                await writer.commit()
            except Exception as error:  # noqa: BLE001 - the caller gets the failure
                await writer.rollback()
                if not pending.result.done():
                    pending.result.set_exception(error)
            else:
                # the write stands whether or not its caller is still waiting
                if not pending.result.done():
                    pending.result.set_result(value)

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

    # ---- accounts --------------------------------------------------------

    async def add_account(self, name: str, password: str) -> Account:
        account = Account(
            id=str(uuid7()),
            name=name,
            password_hash=await derive(hash_secret, password),
            created_at_ms=now_ms(),
        )
        await self._write(
            lambda db: db.execute(
                "INSERT INTO accounts (id, name, password_hash, created_at_ms)"
                " VALUES (?, ?, ?, ?)",
                (
                    account.id,
                    account.name,
                    account.password_hash,
                    account.created_at_ms,
                ),
            )
        )
        return account

    async def find_account(self, name: str) -> Account | None:
        return await self._account("name = ?", name)

    async def get_account(self, account_id: str) -> Account | None:
        return await self._account("id = ?", account_id)

    async def verify_login(self, name: str, password: str) -> Account | None:
        account = await self.find_account(name)
        # an unknown name costs the same as a wrong password, so neither
        # can be told apart by timing
        stored = _NOBODY if account is None else account.password_hash
        if not await derive(verify_secret, password, stored):
            return None
        return account

    async def change_password(self, account_id: str, password: str) -> Account:
        password_hash = await derive(hash_secret, password)
        await self._write(
            lambda db: db.execute(
                "UPDATE accounts SET password_hash = ? WHERE id = ?",
                (password_hash, account_id),
            )
        )
        account = await self.get_account(account_id)
        if account is None:
            raise LookupError(f"no account {account_id}")
        return account

    async def set_preferences(
        self, account_id: str, *, language: str | None, theme: str | None
    ) -> Account:
        await self._write(
            lambda db: db.execute(
                "UPDATE accounts SET language = ?, theme = ? WHERE id = ?",
                (language, theme, account_id),
            )
        )
        account = await self.get_account(account_id)
        if account is None:
            raise LookupError(f"no account {account_id}")
        return account

    async def _account(self, where: str, value: str) -> Account | None:
        async with (
            self._reader() as reader,
            reader.execute(
                "SELECT id, name, password_hash, created_at_ms, language, theme"
                f" FROM accounts WHERE {where}",
                (value,),
            ) as cursor,
        ):
            row = await cursor.fetchone()
        if row is None:
            return None
        return Account(
            id=row["id"],
            name=row["name"],
            password_hash=row["password_hash"],
            created_at_ms=row["created_at_ms"],
            language=row["language"],
            theme=row["theme"],
        )

    # ---- computers -------------------------------------------------------

    async def add_computer(self, name: str) -> Enrolment:
        computer = Computer(id=str(uuid7()), name=name, created_at_ms=now_ms())
        secret = new_secret()
        # scrypt is deliberately slow; the event loop must not wait on it
        secret_hash = await derive(hash_secret, secret)
        await self._write(
            lambda db: db.execute(
                "INSERT INTO computers (id, name, secret_hash, created_at_ms)"
                " VALUES (?, ?, ?, ?)",
                (computer.id, computer.name, secret_hash, computer.created_at_ms),
            )
        )
        return Enrolment(computer=computer, token=f"{computer.id}:{secret}")

    async def remove_computer(self, computer_id: str) -> bool:
        async def remove(db: aiosqlite.Connection) -> bool:
            # the events point at the computer, so they go first
            await db.execute("DELETE FROM events WHERE computer_id = ?", (computer_id,))
            cursor = await db.execute(
                "DELETE FROM computers WHERE id = ?", (computer_id,)
            )
            return cursor.rowcount > 0

        return await self._write(remove)

    async def find_computer(self, computer_id: str) -> Computer | None:
        async with (
            self._reader() as reader,
            reader.execute(
                "SELECT id, name, created_at_ms FROM computers WHERE id = ?",
                (computer_id,),
            ) as cursor,
        ):
            row = await cursor.fetchone()
        return None if row is None else _computer(row)

    async def list_computers(
        self,
        *,
        after: str | None = None,
        until: str | None = None,
        limit: int | None = None,
    ) -> list[Computer]:
        # ids are uuid7, so id order is enrolment order and an id is a
        # cursor: the rows past it stay the rows past it, whatever is added
        # or removed meanwhile
        async with (
            self._reader() as reader,
            reader.execute(
                "SELECT id, name, created_at_ms FROM computers"
                " WHERE id > ? AND id <= ? ORDER BY id LIMIT ?",
                (
                    "" if after is None else after,
                    "\uffff" if until is None else until,
                    -1 if limit is None else limit,
                ),
            ) as cursor,
        ):
            return [_computer(row) async for row in cursor]

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
        if row is None or not await derive(verify_secret, secret, row["secret_hash"]):
            return None
        return _computer(row)

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
                event.correlation.node_id,
                event.event_name,
                event.correlation.thread_id,
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

        async def record(db: aiosqlite.Connection) -> int:
            cursor = await db.executemany(
                "INSERT OR IGNORE INTO events (computer_id, run_id, seq, agent_id,"
                " event_name, thread_id, created_at_ms, received_at_ms, payload)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            # the rows this statement inserted; ignored duplicates do not count
            accepted = cursor.rowcount
            # only the newest health record says anything a consumer wants
            await db.execute(
                "DELETE FROM events WHERE computer_id = ? AND event_name = 'node.health'"
                " AND id < (SELECT MAX(id) FROM events"
                " WHERE computer_id = ? AND event_name = 'node.health')",
                (computer_id, computer_id),
            )
            if received_at_ms - self._last_sweep_ms >= _DAY_MS:
                await self._sweep(db)
            return accepted

        return await self._write(record)

    async def sweep(self) -> None:
        """Drop what is older than the retention, except the last health beat."""

        await self._write(self._sweep)

    async def _sweep(self, db: aiosqlite.Connection) -> None:
        # what outlives the retention: the last health beat per computer, and
        # the last usage of a runtime session that is still heard from, which
        # today's usage is counted from. a session silent for the whole
        # retention goes with everything it said
        now = now_ms()
        cutoff = now - self._retention_ms
        await db.execute(
            "DELETE FROM events WHERE received_at_ms < ? AND id NOT IN ("
            " SELECT MAX(id) FROM events WHERE event_name = 'node.health'"
            " GROUP BY computer_id) AND id NOT IN ("
            " SELECT MAX(id) FROM events WHERE event_name = 'usage.updated'"
            " AND json_extract(payload, '$.correlation.runtime_session_id') IN ("
            "  SELECT json_extract(payload, '$.correlation.runtime_session_id')"
            "  FROM events WHERE received_at_ms >= ?)"
            " GROUP BY computer_id, agent_id,"
            " json_extract(payload, '$.correlation.runtime_session_id'))",
            (cutoff, cutoff),
        )
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

    async def usage_around(
        self, computer_id: str, agent_id: str, at_ms: int
    ) -> list[StoredEvent]:
        # a usage row is its runtime session's running total, so the newest
        # row on each side of the moment is all the arithmetic needs
        async with (
            self._reader() as reader,
            reader.execute(
                f"SELECT {_COLUMNS} FROM events"
                " WHERE id IN ("
                "  SELECT MAX(id) FROM events"
                "  WHERE computer_id = ? AND agent_id = ? AND event_name = 'usage.updated'"
                "  GROUP BY json_extract(payload, '$.correlation.runtime_session_id'),"
                "  created_at_ms >= ?)",
                (computer_id, agent_id, at_ms),
            ) as cursor,
        ):
            return [_stored(row) async for row in cursor]


@dataclass(frozen=True, slots=True)
class _Write[T]:
    operation: Callable[[aiosqlite.Connection], Awaitable[T]]
    result: asyncio.Future[T]


def _marks(values: Sequence[object]) -> str:
    """One placeholder per value, for an IN list."""

    return ", ".join("?" for _ in values)


def _private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        path.chmod(0o700)


def _computer(row: aiosqlite.Row) -> Computer:
    return Computer(id=row["id"], name=row["name"], created_at_ms=row["created_at_ms"])


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
