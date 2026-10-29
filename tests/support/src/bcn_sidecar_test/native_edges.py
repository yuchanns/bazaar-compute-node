"""Check installed native transports with real endpoints and isolated processes."""

from __future__ import annotations

import asyncio
import os
import socket
import stat
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import tomli_w
from bazaar_contrib_sidecar.streams import Call, Reply, UnlimitedLineReader
from bazaar_contrib_sidecar.wire import Request, Result
from installed_smoke import command, node

if sys.platform == "win32":
    from bazaar_contrib_sidecar_windows.transport import connect
else:
    from bazaar_contrib_sidecar_unix.transport import connect, listen


async def exercise(directory: Path):
    os.environ["BCN_HOME"] = str(directory)
    native = "sidecar-windows" if sys.platform == "win32" else "sidecar-unix"
    endpoint = (
        rf"\\.\pipe\bcn-edges-{directory.name}"
        if sys.platform == "win32"
        else str(directory / "endpoint")
    )
    config = directory / "config.toml"
    journal = directory / "journal.txt"
    await asyncio.to_thread(
        config.write_text,
        tomli_w.dumps(
            {
                "version": "5",
                "contribs": [
                    {"enabled": [{"entry": "commands"}]},
                    {"enabled": [{"entry": "timer-wheel"}]},
                    {"enabled": [{"entry": native, "config": {"endpoint": endpoint}}]},
                    {"enabled": [{"entry": "sidecar"}]},
                    {"enabled": [{"entry": "sidecar-controls"}]},
                    {
                        "enabled": [
                            {
                                "entry": "sidecar-journal",
                                "config": {"path": str(journal)},
                            }
                        ]
                    },
                ],
            }
        ),
    )

    if sys.platform != "win32":
        path = Path(endpoint)
        for leftover in ("socket", "broken-symlink", "file"):
            if leftover == "socket":
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                    await asyncio.to_thread(sock.bind, endpoint)
            elif leftover == "broken-symlink":
                await asyncio.to_thread(path.symlink_to, directory / "missing")
            else:
                await asyncio.to_thread(path.write_text, "left by an earlier node")
            async with node(config):
                await command(config, "remote", "append", f"recovered {leftover}")

        def connected(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
            writer.write(b"original listener still owns this address\n")
            writer.close()

        # This listener has no BCN lock or application protocol.
        async with await asyncio.start_unix_server(connected, path=endpoint):
            try:
                async with node(config):
                    raise AssertionError("an occupied address was taken over")
            except RuntimeError:
                pass
            reader, writer = await connect(endpoint)
            try:
                assert b"original listener" in await reader.readline()
            finally:
                writer.close()
                await writer.wait_closed()

        # Shutdown must preserve a file installed at the address after a rename.
        async with listen(endpoint, connected):
            await asyncio.to_thread(path.rename, directory / "previous-socket")
            await asyncio.to_thread(path.write_text, "replacement belongs to its owner")
        assert "replacement" in await asyncio.to_thread(path.read_text)
        await asyncio.to_thread(path.unlink)

    async with node(config):
        if sys.platform != "win32":
            mode = (await asyncio.to_thread(Path(endpoint).stat)).st_mode
            assert stat.S_ISSOCK(mode) and not mode & 0o077
        try:
            async with node(config):
                raise AssertionError("a second node acquired the same address")
        except RuntimeError:
            pass
        await command(
            config, "remote", "append", "original node after duplicate startup"
        )

        reader, writer = await connect(endpoint)
        try:
            requests = [
                Call(request=Request(service="journal", method="tag"))
                .model_dump_json()
                .encode()
                for _ in range(2)
            ]
            writer.write(requests[0][:5])
            await writer.drain()
            writer.write(requests[0][5:] + b"\n" + requests[1] + b"\n")
            await writer.drain()
            lines = UnlimitedLineReader(reader)
            for _ in range(2):
                response = Reply.model_validate_json(await lines.readline()).response
                assert isinstance(response, Result) and response.value == "journal"
        finally:
            writer.close()
            await writer.wait_closed()

        for payload in (b"invalid JSON\n", b"x" * 2_000_000 + b"\n"):
            reader, writer = await connect(endpoint)
            try:
                writer.write(payload)
                try:
                    await writer.drain()
                    assert not await reader.read(1)
                except OSError:
                    pass
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except OSError:
                    pass
            await command(config, "remote", "append", "healthy after malformed client")

        # Fast disconnects can reach Windows before its pending accept completes.
        for _ in range(8):
            _, writer = await connect(endpoint)
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
        await command(config, "remote", "append", "healthy after early disconnects")
        reader, writer = await connect(endpoint)

    try:
        assert not await reader.read(1)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass

    # Kill only the isolated test node; the next process must reclaim its address.
    async with node(config) as process:
        await command(config, "remote", "append", "before forced termination")
        process.kill()
        await process.wait()
    async with node(config):
        await command(config, "remote", "append", "recovered after forced termination")
    text = await asyncio.to_thread(journal.read_text)
    assert all(
        result in text
        for result in (
            "original node after duplicate startup",
            "healthy after malformed client",
            "healthy after early disconnects",
            "recovered after forced termination",
        )
    )
    print(
        f"Installed {native}: endpoint ownership, bad clients, cleanup and crash recovery passed"
    )


if __name__ == "__main__":
    with TemporaryDirectory(
        prefix="bcn-edges-", dir="/tmp" if os.name == "posix" else None
    ) as directory:
        asyncio.run(exercise(Path(directory)))
