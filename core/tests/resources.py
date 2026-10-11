"""Executable contribs backed by local SQLite databases and files."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
lazy import sqlite3
lazy from collections.abc import Awaitable, Callable, Iterator, Sequence
lazy from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field
lazy import aiosqlite

from bazaar_core import (
    COMPOSITION,
    Candidate,
    Composition,
    Context,
    Contrib,
    ContribEntry,
    EventKey,
    ServiceKey,
    TaskFailureSignal,
)


@dataclass
class Write:
    agent: str
    text: str
    done: asyncio.Event = field(default_factory=asyncio.Event)
    error: sqlite3.Error | None = None


class Database:
    """A SQLite connection with an owned asynchronous write queue."""

    def __init__(self, path: Path, connection: aiosqlite.Connection) -> None:
        self.path = path
        self.connection = connection
        self.lock = asyncio.Lock()
        self.queue: asyncio.Queue[Write | None] = asyncio.Queue(16)
        self.progress = asyncio.Event()
        self.progress.set()
        self.ready = asyncio.Event()
        self.failure = TaskFailureSignal()
        self.failure.reset()
        self.tasks: asyncio.TaskGroup | None = None
        self.worker: asyncio.Task[None]
        self.closed = False

    @classmethod
    async def open(cls, path: Path) -> Database:
        connection = await aiosqlite.connect(path)
        database = cls(path, connection)
        try:
            async with connection.execute(
                "CREATE TABLE IF NOT EXISTS messages (agent TEXT, text TEXT)"
            ):
                pass
            await connection.commit()
            return database
        except BaseException:
            await database.aclose()
            raise

    async def start(self) -> None:
        self.tasks = asyncio.TaskGroup()
        await self.tasks.__aenter__()
        self.worker = self.tasks.create_task(
            self.failure.observe(
                self._write(), component=f"SQLite writer {self.path.name}"
            ),
            name=f"sqlite:{self.path.name}",
            eager_start=True,
        )
        await self.ready.wait()

    async def _write(self) -> None:
        self.ready.set()
        while True:
            request = await self.queue.get()
            if request is None:
                return
            try:
                async with self.lock:
                    async with self.connection.execute(
                        "INSERT INTO messages VALUES (?, ?)",
                        (request.agent, request.text),
                    ):
                        pass
                    await self.connection.commit()
            except sqlite3.Error as error:
                request.error = error
                raise
            finally:
                request.done.set()

    async def insert(self, agent: str, text: str) -> None:
        await self.progress.wait()
        request = Write(agent, text)
        await self.queue.put(request)
        await request.done.wait()
        if request.error is not None:
            raise request.error

    async def messages(self) -> list[tuple[str, str]]:
        async with (
            self.lock,
            self.connection.execute("SELECT agent, text FROM messages") as cursor,
        ):
            return cast(list[tuple[str, str]], await cursor.fetchall())

    async def wait_failure(self) -> None:
        await self.failure.wait()

    async def aclose(self) -> None:
        self.failure.disable()
        try:
            if self.tasks is not None:
                if not self.worker.done():
                    await self.queue.put(None)
                await self.tasks.__aexit__(None, None, None)
        finally:
            await self.connection.close()
            self.closed = True


class BackendRegistry:
    def __init__(self) -> None:
        self.backends: dict[str, Database] = {}

    @contextmanager
    def register(self, name: str, database: Database) -> Iterator[None]:
        self.backends[name] = database
        try:
            yield
        finally:
            del self.backends[name]

    def get(self, name: str) -> Database:
        return self.backends[name]


STORAGE = ServiceKey[BackendRegistry]("storage")
DB = ServiceKey[Database]("db")


@cache
def backend_key(name: str) -> ServiceKey[Database]:
    return ServiceKey[Database](f"storage.backend.{name}")


class BackendConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    path: Path


class DatabaseConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    backend: str = Field(min_length=1)


@asynccontextmanager
async def storage_lifespan(ctx: Context):
    ctx.offer(STORAGE, BackendRegistry())
    yield


@asynccontextmanager
async def backend_lifespan(ctx: Context, config: BackendConfig):
    database = await Database.open(config.path)
    try:
        await database.start()
        with ctx.accept(STORAGE).register(config.name, database):
            ctx.offer(backend_key(config.name), database)
            yield
    finally:
        await database.aclose()


def database_accepts(
    config: DatabaseConfig,
) -> list[ServiceKey[BackendRegistry] | ServiceKey[Database]]:
    return [STORAGE, backend_key(config.backend)]


@asynccontextmanager
async def database_lifespan(ctx: Context, config: DatabaseConfig):
    ctx.offer(DB, ctx.accept(STORAGE).get(config.backend))
    yield


@dataclass(frozen=True)
class Message:
    text: str


MESSAGE_RECEIVED = EventKey[Message]("message.received")
MESSAGE_RECORDED = EventKey[Message]("message.recorded")


async def record(ctx: Context, value: Message) -> None:
    await ctx.accept(DB).insert(ctx.name, value.text)
    await ctx.announce(MESSAGE_RECORDED, value)


storage = Contrib(offers=[STORAGE], lifespan=storage_lifespan)
database = Contrib(
    config=DatabaseConfig,
    accepts=database_accepts,
    offers=[DB],
    lifespan=database_lifespan,
)
recorder = Contrib(
    accepts=[DB],
    listen={MESSAGE_RECEIVED: record},
)


@asynccontextmanager
async def line_server():
    connected = asyncio.Event()
    release = asyncio.Event()
    replies: list[asyncio.Task[None]] = []

    async def reply(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        del reader
        try:
            connected.set()
            await release.wait()
            writer.write(b"resource ready\n")
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async with asyncio.TaskGroup() as group:
        listener = await asyncio.start_server(
            lambda reader, writer: replies.append(
                group.create_task(reply(reader, writer))
            ),
            "127.0.0.1",
            0,
        )
        async with listener:
            try:
                yield listener.sockets[0].getsockname()[1], connected, release
            finally:
                for task in replies:
                    task.cancel()


def observe[T](
    key: ServiceKey[T],
    services: dict[str, T],
) -> ContribEntry:
    @asynccontextmanager
    async def lifespan(ctx: Context):
        services[ctx.name] = ctx.accept(key)
        yield

    return ContribEntry(
        name=f"observe-{key.name}",
        definition=Contrib(accepts=[key], lifespan=lifespan),
    )


def loader(
    load: Callable[[Context], Awaitable[Composition | Candidate]],
    accepts: Sequence[ServiceKey[Any]] = (),
) -> ContribEntry:
    @asynccontextmanager
    async def lifespan(ctx: Context):
        ctx.offer(COMPOSITION, await load(ctx))
        yield

    return ContribEntry(
        name="loader",
        definition=Contrib(accepts=accepts, offers=[COMPOSITION], lifespan=lifespan),
    )
