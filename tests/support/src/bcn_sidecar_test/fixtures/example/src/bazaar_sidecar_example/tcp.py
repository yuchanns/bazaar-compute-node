"""A real TCP implementation using public sidecar and timer contracts."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
lazy import asyncio

from bazaar_contrib_sidecar import CLIENT, SERVER, Client, Sessions
from bazaar_contrib_sidecar.streams import Connection, serve
from bazaar_contrib_timer import TIMER
from bazaar_core import Context, Contrib
from pydantic import BaseModel


class Config(BaseModel):
    endpoint: Path


class Server:
    def __init__(self, endpoint: Path):
        self.endpoint = endpoint

    @asynccontextmanager
    async def serve(self, sessions: Sessions):
        tasks: set[asyncio.Task[None]] = set()

        async with asyncio.TaskGroup() as group:

            def connected(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
                task = group.create_task(
                    serve(reader, writer, sessions), eager_start=True
                )
                tasks.add(task)
                task.add_done_callback(tasks.discard)

            async with await asyncio.start_server(connected, "127.0.0.1", 0) as server:
                port = server.sockets[0].getsockname()[1]
                await asyncio.to_thread(self.endpoint.write_text, str(port))
                try:
                    yield
                finally:
                    for task in tasks:
                        task.cancel()


@asynccontextmanager
async def lifespan(ctx: Context, config: Config):
    async def connect():
        port = int(await asyncio.to_thread(config.endpoint.read_text))
        return await asyncio.open_connection("127.0.0.1", port)

    connection = Connection(connect)
    client = Client(connection.send, connection.aclose, ctx.accept(TIMER))
    try:
        ctx.offer(SERVER, Server(config.endpoint))
        ctx.offer(CLIENT, client)
        yield
    finally:
        await client.aclose()


transport = Contrib(
    config=Config, accepts=[TIMER], offers=[SERVER, CLIENT], lifespan=lifespan
)
