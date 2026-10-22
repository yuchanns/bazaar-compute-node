"""Proactor named pipes carry the same streams and handlers as Unix sockets."""

from __future__ import annotations

from contextlib import asynccontextmanager
lazy import _winapi
lazy import asyncio
lazy from asyncio.windows_utils import BUFSIZE, PipeHandle
lazy from collections.abc import Callable
lazy from typing import Any

from bazaar_contrib_sidecar import Sessions
from bazaar_contrib_sidecar.streams import serve

PIPE_REJECT_REMOTE_CLIENTS = 0x00000008


async def connect(endpoint: str):
    loop: Any = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    transport, _ = await loop.create_pipe_connection(lambda: protocol, endpoint)
    return reader, asyncio.StreamWriter(transport, protocol, reader, loop)


@asynccontextmanager
async def listen(
    endpoint: str,
    connected: Callable[[asyncio.StreamReader, asyncio.StreamWriter], None],
):
    loop: Any = asyncio.get_running_loop()

    def create(first: bool):
        flags = _winapi.PIPE_ACCESS_DUPLEX | _winapi.FILE_FLAG_OVERLAPPED
        if first:
            flags |= _winapi.FILE_FLAG_FIRST_PIPE_INSTANCE
        return PipeHandle(
            _winapi.CreateNamedPipe(
                endpoint,
                flags,
                _winapi.PIPE_TYPE_MESSAGE
                | _winapi.PIPE_READMODE_MESSAGE
                | _winapi.PIPE_WAIT
                | PIPE_REJECT_REMOTE_CLIENTS,
                _winapi.PIPE_UNLIMITED_INSTANCES,
                BUFSIZE,
                BUFSIZE,
                _winapi.NMPWAIT_WAIT_FOREVER,
                _winapi.NULL,
            )
        )

    pipe = create(True)

    async def accept():
        nonlocal pipe
        while True:
            # Keep a spare instance so the address remains owned between clients.
            current = pipe
            pipe = create(False)
            try:
                await loop._proactor.accept_pipe(current)
                protocol = asyncio.StreamReaderProtocol(
                    asyncio.StreamReader(), connected
                )
                loop._make_duplex_pipe_transport(
                    current, protocol, extra={"addr": endpoint}
                )
            except BrokenPipeError:
                current.close()
            except BaseException:
                current.close()
                raise

    try:
        async with asyncio.TaskGroup() as tasks:
            task = tasks.create_task(accept(), eager_start=True)
            try:
                yield
            finally:
                task.cancel()
    finally:
        pipe.close()


class Listener:
    """Hold native connections only while the handler owner is serving."""

    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint

    @asynccontextmanager
    async def serve(self, sessions: Sessions):
        connections: set[asyncio.Task[None]] = set()
        closing = False

        async with asyncio.TaskGroup() as tasks:

            def connected(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
                if closing:
                    writer.close()
                    return
                task = tasks.create_task(
                    serve(reader, writer, sessions), eager_start=True
                )
                connections.add(task)
                task.add_done_callback(connections.discard)

            async with listen(self.endpoint, connected):
                try:
                    yield
                finally:
                    closing = True
                    for task in connections:
                        task.cancel()
