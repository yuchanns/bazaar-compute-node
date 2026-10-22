"""Multiplex complete JSONL frames over an implementation's byte streams."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID, uuid7
lazy import asyncio
lazy import logging
lazy from collections.abc import Awaitable, Callable

from pydantic import Field, TypeAdapter

from .contracts import Response, Sessions
from .wire import Failure, Message, Request

_READ_CHUNK_BYTES = 64 * 1024
logger = logging.getLogger(__name__)


class Call(Message):
    kind: Literal["call"] = "call"
    id: UUID = Field(default_factory=uuid7)
    request: Request


class Cancel(Message):
    kind: Literal["cancel"] = "cancel"
    id: UUID


FRAME = TypeAdapter(Annotated[Call | Cancel, Field(discriminator="kind")])


class Reply(Message):
    id: UUID
    response: Response = Field(discriminator="kind")


class UnlimitedLineReader:
    """Retain trailing bytes while reading complete lines in fixed-size chunks."""

    def __init__(self, reader: asyncio.StreamReader) -> None:
        self._reader = reader
        self._buffer = bytearray()
        self._eof = False

    async def readline(self) -> bytes:
        while True:
            boundary = self._buffer.find(b"\n")
            if boundary >= 0:
                boundary += 1
                line = bytes(self._buffer[:boundary])
                del self._buffer[:boundary]
                return line
            if self._eof:
                line = bytes(self._buffer)
                self._buffer.clear()
                return line
            chunk = await self._reader.read(_READ_CHUNK_BYTES)
            if chunk:
                self._buffer.extend(chunk)
            else:
                self._eof = True


class Connection:
    """Pair concurrent calls with their responses on one lazy connection."""

    def __init__(
        self,
        connect: Callable[
            [], Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]
        ],
    ) -> None:
        self.connect = connect
        self.lock = asyncio.Lock()
        self.writer: asyncio.StreamWriter | None = None
        self.reader: asyncio.Task[None] | None = None
        self.pending: dict[UUID, asyncio.Future[Response]] = {}
        self.error: Exception | None = None

    async def send(self, request: Request) -> Response:
        call = Call(request=request)
        future: asyncio.Future[Response] = asyncio.get_running_loop().create_future()
        try:
            async with self.lock:
                if self.error is not None:
                    raise ConnectionError(
                        "the sidecar connection has closed"
                    ) from self.error
                if self.writer is None:
                    reader, self.writer = await self.connect()
                    self.reader = asyncio.create_task(
                        self.receive(UnlimitedLineReader(reader))
                    )
                self.pending[call.id] = future
                self.writer.write(call.model_dump_json().encode() + b"\n")
                await self.writer.drain()
            return await future
        except asyncio.CancelledError:
            if call.id in self.pending:
                try:
                    async with self.lock:
                        if self.writer is not None and not self.writer.is_closing():
                            self.writer.write(
                                Cancel(id=call.id).model_dump_json().encode() + b"\n"
                            )
                            await self.writer.drain()
                except OSError:
                    logger.exception("could not cancel sidecar call %s", call.id)
            raise
        finally:
            self.pending.pop(call.id, None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()

    async def receive(self, reader: UnlimitedLineReader) -> None:
        try:
            while line := await reader.readline():
                reply = Reply.model_validate_json(line)
                future = self.pending.get(reply.id)
                if future is not None and not future.done():
                    future.set_result(reply.response)
        except Exception as error:  # noqa: BLE001 - Fail all calls owned by this broken connection.
            self.error = error
        finally:
            self.error = self.error or ConnectionError(
                "sidecar closed before returning a result"
            )
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(self.error)

    async def aclose(self) -> None:
        async with self.lock:
            self.error = self.error or ConnectionError(
                "the sidecar connection has closed"
            )
            if self.writer is not None:
                self.writer.close()
            if self.reader is not None:
                self.reader.cancel()
        if self.reader is not None:
            await asyncio.gather(self.reader, return_exceptions=True)
        if self.writer is not None:
            try:
                await self.writer.wait_closed()
            except OSError:
                pass


async def serve(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, sessions: Sessions
) -> None:
    """Own concurrent handlers and remote resources for one connection."""
    try:
        lines = UnlimitedLineReader(reader)
        async with sessions() as dispatch, asyncio.TaskGroup() as group:
            tasks: dict[UUID, asyncio.Task[None]] = {}

            async def respond(call: Call) -> None:
                try:
                    response = await dispatch(call.request)
                except asyncio.CancelledError:
                    response = Failure(
                        type="CancelledError", message="the remote call was cancelled"
                    )
                writer.write(
                    Reply(id=call.id, response=response).model_dump_json().encode()
                    + b"\n"
                )
                await writer.drain()

            try:
                while line := await lines.readline():
                    frame = FRAME.validate_json(line)
                    match frame:
                        case Call():
                            task = group.create_task(respond(frame))
                            tasks[frame.id] = task
                            task.add_done_callback(
                                lambda _, id=frame.id: tasks.pop(id, None)
                            )
                        case Cancel():
                            if task := tasks.get(frame.id):
                                task.cancel()
            finally:
                for task in tasks.values():
                    task.cancel()
    except* OSError, ValueError:
        pass
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
