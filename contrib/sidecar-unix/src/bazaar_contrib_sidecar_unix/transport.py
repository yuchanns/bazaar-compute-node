"""Unix streams provide transport for the shared sidecar protocol."""

from __future__ import annotations

from contextlib import asynccontextmanager
lazy import asyncio
lazy import socket
lazy import stat
lazy from collections.abc import Callable
lazy from pathlib import Path

from bazaar_contrib_sidecar import Sessions
from bazaar_contrib_sidecar.streams import UnlimitedLineReader
from bazaar_contrib_sidecar.wire import RESPONSE, Request


async def connect(endpoint: str):
    return await asyncio.open_unix_connection(endpoint)


@asynccontextmanager
async def listen(
    endpoint: str,
    connected: Callable[[asyncio.StreamReader, asyncio.StreamWriter], None],
):
    path = Path(endpoint)
    await asyncio.to_thread(path.parent.mkdir, mode=0o700, parents=True, exist_ok=True)
    try:
        mode = (await asyncio.to_thread(path.lstat)).st_mode
    except FileNotFoundError:
        pass
    else:
        if not (stat.S_ISSOCK(mode) or stat.S_ISLNK(mode)):
            raise FileExistsError(f"sidecar endpoint is not a socket: {path}")
        try:
            _, writer = await connect(endpoint)
        except ConnectionRefusedError, FileNotFoundError:
            pass
        else:
            writer.close()
            await writer.wait_closed()
            raise FileExistsError(f"sidecar endpoint already exists: {path}")
        await asyncio.to_thread(path.unlink, missing_ok=True)
    # Binding explicitly keeps asyncio from unlinking a newly occupied address.
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        await asyncio.to_thread(sock.bind, endpoint)
        server = await asyncio.start_unix_server(
            connected,
            sock=sock,
            start_serving=False,
            cleanup_socket=True,
        )
    except BaseException:
        sock.close()
        raise
    try:
        await asyncio.to_thread(path.chmod, 0o600)
        await server.start_serving()
        async with server:
            yield
    finally:
        server.close()
        await server.wait_closed()


class Channel:
    """Own a lazy native connection for the provider's client service."""

    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint
        self.streams: tuple[UnlimitedLineReader, asyncio.StreamWriter] | None = None

    async def send(self, request: Request):
        if self.streams is None:
            reader, writer = await connect(self.endpoint)
            self.streams = UnlimitedLineReader(reader), writer
        reader, writer = self.streams
        writer.write(request.model_dump_json().encode() + b"\n")
        await writer.drain()
        line = await reader.readline()
        if not line:
            raise ConnectionError("sidecar closed before returning a result")
        return RESPONSE.validate_json(line)

    async def aclose(self) -> None:
        if self.streams is not None:
            _, writer = self.streams
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                # A pipe peer can disconnect after its final response was flushed.
                pass


class Listener:
    """Hold native connections only while the handler owner is serving."""

    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint

    @asynccontextmanager
    async def serve(self, sessions: Sessions):
        connections: set[asyncio.Task[None]] = set()
        closing = False

        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
            try:
                lines = UnlimitedLineReader(reader)
                async with sessions() as dispatch:
                    while line := await lines.readline():
                        response = await dispatch(Request.model_validate_json(line))
                        writer.write(response.model_dump_json().encode() + b"\n")
                        await writer.drain()
            except OSError, ValueError:
                pass
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except OSError:
                    pass

        async with asyncio.TaskGroup() as tasks:

            def connected(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
                if closing:
                    writer.close()
                    return
                task = tasks.create_task(handle(reader, writer), eager_start=True)
                connections.add(task)
                task.add_done_callback(connections.discard)

            async with listen(self.endpoint, connected):
                try:
                    yield
                finally:
                    closing = True
                    for task in connections:
                        task.cancel()
