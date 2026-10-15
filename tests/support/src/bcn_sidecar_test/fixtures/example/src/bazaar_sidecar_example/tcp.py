"""A real TCP implementation depending only on the public sidecar package."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
lazy import asyncio

from bazaar_contrib_sidecar import CLIENT, SERVER, Client, Sessions
from bazaar_contrib_sidecar.streams import UnlimitedLineReader
from bazaar_contrib_sidecar.wire import RESPONSE, Request
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

        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
            try:
                lines = UnlimitedLineReader(reader)
                async with sessions() as handler:
                    while line := await lines.readline():
                        response = await handler(Request.model_validate_json(line))
                        writer.write(response.model_dump_json().encode() + b"\n")
                        await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        async with asyncio.TaskGroup() as group:

            def connected(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
                task = group.create_task(handle(reader, writer), eager_start=True)
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
    streams: tuple[UnlimitedLineReader, asyncio.StreamWriter] | None = None

    async def send(request: Request):
        nonlocal streams
        if streams is None:
            port = int(await asyncio.to_thread(config.endpoint.read_text))
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            streams = UnlimitedLineReader(reader), writer
        reader, writer = streams
        writer.write(request.model_dump_json().encode() + b"\n")
        await writer.drain()
        return RESPONSE.validate_json(await reader.readline())

    async def close():
        if streams is not None:
            streams[1].close()
            await streams[1].wait_closed()

    client = Client(send, close)
    try:
        ctx.offer(SERVER, Server(config.endpoint))
        ctx.offer(CLIENT, client)
        yield
    finally:
        await client.aclose()


transport = Contrib(config=Config, offers=[SERVER, CLIENT], lifespan=lifespan)
