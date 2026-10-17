"""Separate node and local server connections with records and a TCP reader."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
lazy import asyncio
lazy import json

import aiosqlite
from bazaar_core import Composition, Context, Contrib, ContribEntry, ServiceKey
from pydantic import BaseModel
lazy import aiofiles

DB = ServiceKey[aiosqlite.Connection]("example.database")


class Records:
    def __init__(self, database: aiosqlite.Connection) -> None:
        self.database = database

    async def read(self) -> list[tuple[str, str]]:
        async with self.database.execute("SELECT name, state FROM records") as cursor:
            return [(str(row[0]), str(row[1])) for row in await cursor.fetchall()]

    async def write(self, name: str, state: str) -> None:
        await self.database.execute("INSERT INTO records VALUES (?, ?)", (name, state))
        await self.database.commit()


RECORDS = ServiceKey[Records]("example.records")


class PathConfig(BaseModel):
    path: Path


class RecordConfig(BaseModel):
    name: str


@asynccontextmanager
async def database_lifespan(ctx: Context, config: PathConfig):
    async with aiosqlite.connect(config.path) as database:
        ctx.offer(DB, database)
        yield


@asynccontextmanager
async def records_lifespan(ctx: Context, config: RecordConfig):
    database = ctx.accept(DB)
    await database.execute("CREATE TABLE IF NOT EXISTS records (name TEXT, state TEXT)")
    await database.execute("INSERT INTO records VALUES (?, 'started')", (config.name,))
    await database.commit()
    ctx.offer(RECORDS, Records(database))
    try:
        yield
    finally:
        await database.execute(
            "INSERT INTO records VALUES (?, 'released')", (config.name,)
        )
        await database.commit()


@asynccontextmanager
async def api_lifespan(ctx: Context, config: PathConfig):
    records = ctx.accept(RECORDS)
    writers: set[asyncio.StreamWriter] = set()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            if await reader.readline():
                writer.write((json.dumps(await records.read()) + "\n").encode())
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
            writers.discard(writer)

    async with asyncio.TaskGroup() as tasks:

        def connected(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
            writers.add(writer)
            tasks.create_task(handle(reader, writer))

        async with await asyncio.start_server(connected, "127.0.0.1", 0) as server:
            async with aiofiles.open(config.path, "w") as stream:
                await stream.write(str(server.sockets[0].getsockname()[1]))
            try:
                yield
            finally:
                server.close()
                await server.wait_closed()
                for writer in tuple(writers):
                    writer.close()


node_database = Contrib(config=PathConfig, offers=[DB], lifespan=database_lifespan)
node_records = Contrib(
    config=RecordConfig, accepts=[DB], offers=[RECORDS], lifespan=records_lifespan
)
api = Contrib(config=PathConfig, accepts=[RECORDS], lifespan=api_lifespan)


class ServerConfig(BaseModel):
    database: Path
    port: Path
    name: str = "server"


@asynccontextmanager
async def server_lifespan(ctx: Context, config: ServerConfig):
    async with ctx.enable(
        Composition(
            entries=[
                ContribEntry(
                    name="database",
                    definition=node_database,
                    config={"path": config.database},
                ),
                ContribEntry(
                    name="records",
                    definition=node_records,
                    config={"name": config.name},
                ),
                ContribEntry(name="api", definition=api, config={"path": config.port}),
            ]
        ),
        local=(DB, RECORDS),
    ):
        yield


server = Contrib(config=ServerConfig, lifespan=server_lifespan)
