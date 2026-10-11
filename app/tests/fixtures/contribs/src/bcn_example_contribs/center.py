"""A small persistent configuration center with SQLite transactions and TCP clients."""

from __future__ import annotations

lazy import asyncio
lazy import json
lazy import sqlite3
lazy from contextlib import closing
lazy from pathlib import Path
lazy from uuid import uuid7


class ConfigCenter:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.connections: set[asyncio.StreamWriter] = set()
        self.tasks: set[asyncio.Task[None]] = set()
        self.drop_response = False
        self.pause_reads: asyncio.Event | None = None
        self.reading = asyncio.Event()
        self.read_requests: asyncio.Queue[dict] = asyncio.Queue()

    def initialize(self) -> None:
        with closing(sqlite3.connect(self.path)) as database, database:
            database.execute(
                "CREATE TABLE IF NOT EXISTS configs (key TEXT PRIMARY KEY, revision TEXT, payload TEXT)"
            )
            database.execute(
                "CREATE TABLE IF NOT EXISTS history (key TEXT, revision TEXT, payload TEXT)"
            )

    def transact(self, request: dict) -> dict:
        with closing(sqlite3.connect(self.path)) as database, database:
            database.execute("BEGIN IMMEDIATE")
            row = database.execute(
                "SELECT revision, payload FROM configs WHERE key = ?", (request["key"],)
            ).fetchone()
            if request["operation"] == "read":
                return (
                    {"revision": row[0], "payload": json.loads(row[1])}
                    if row
                    else {"missing": True}
                )
            revision = row[0] if row else None
            if revision != request["expected_revision"]:
                return {"conflict": True}
            if row:
                database.execute(
                    "INSERT INTO history VALUES (?, ?, ?)", (request["key"], *row)
                )
            revision = str(uuid7())
            database.execute(
                "INSERT OR REPLACE INTO configs VALUES (?, ?, ?)",
                (request["key"], revision, json.dumps(request["payload"])),
            )
            return {"revision": revision, "payload": request["payload"]}

    async def handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        task = asyncio.current_task()
        assert task is not None
        self.connections.add(writer)
        self.tasks.add(task)
        try:
            request = json.loads(await reader.readline())
            result = await asyncio.to_thread(self.transact, request)
            if request["operation"] == "read" and self.pause_reads is not None:
                self.reading.set()
                self.read_requests.put_nowait(request)
                await self.pause_reads.wait()
            if (
                request["operation"] == "write"
                and self.drop_response
                and "conflict" not in result
            ):
                self.drop_response = False
                return
            writer.write((json.dumps(result) + "\n").encode())
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
            self.connections.discard(writer)
            self.tasks.discard(task)

    async def start(self) -> int:
        await asyncio.to_thread(self.initialize)
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]

    async def close(self) -> None:
        self.server.close()
        await self.server.wait_closed()
        for writer in tuple(self.connections):
            writer.close()
        for task in tuple(self.tasks):
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
