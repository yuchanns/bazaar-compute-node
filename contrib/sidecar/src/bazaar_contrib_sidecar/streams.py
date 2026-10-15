"""Read complete JSONL frames independently of asyncio's stream buffer limit."""

from __future__ import annotations

lazy import asyncio

_READ_CHUNK_BYTES = 64 * 1024


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
