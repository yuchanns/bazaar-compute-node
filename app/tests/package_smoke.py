"""Exercise the installed node application and its process entry point."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
lazy import signal
lazy from contextlib import asynccontextmanager

from bazaar_contrib_status import status
from bazaar_core import (
    COMPOSITION,
    Composition,
    Context,
    Contrib,
    ContribEntry,
    Running,
    ServiceKey,
)

from bazaar_compute_node import run

DIRECTORY = ServiceKey[Path]("installed.directory")


async def exercise(path: Path) -> None:
    @asynccontextmanager
    async def directory(ctx: Context):
        ctx.offer(DIRECTORY, path)
        yield

    @asynccontextmanager
    async def writer(ctx: Context):
        path = ctx.accept(DIRECTORY) / f"{ctx.name}.txt"
        await asyncio.to_thread(path.write_text, "agent started\n")
        try:
            yield
        finally:
            text = await asyncio.to_thread(path.read_text)
            await asyncio.to_thread(path.write_text, text + "agent released\n")

    @asynccontextmanager
    async def load(ctx: Context):
        ctx.offer(
            COMPOSITION,
            Composition(
                entries=[
                    ContribEntry(name="status", definition=status),
                    ContribEntry(
                        name="installed",
                        definition=Contrib(accepts=[DIRECTORY], lifespan=writer),
                    ),
                ],
            ),
        )
        yield

    async with run(
        Composition(
            entries=[
                ContribEntry(
                    name="directory",
                    definition=Contrib(offers=[DIRECTORY], lifespan=directory),
                )
            ]
        ),
        ContribEntry(
            name="node-composition",
            definition=Contrib(offers=[COMPOSITION], lifespan=load),
        ),
    ) as hub:
        if not any(
            instance.name == "status" and isinstance(instance.state, Running)
            for instance in hub.snapshots
        ):
            raise RuntimeError("installed status service did not start")
    text = await asyncio.to_thread((path / "installed.txt").read_text)
    if "agent started" not in text or "agent released" not in text:
        raise RuntimeError("installed node did not complete the agent lifecycle")

    if sys.platform != "win32":
        for sig in (signal.SIGINT, signal.SIGTERM):
            process = await asyncio.create_subprocess_exec(
                "bcn",
                "run",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                assert process.stdout is not None
                if not await asyncio.wait_for(process.stdout.readline(), timeout=10):
                    raise RuntimeError("installed node exited before becoming ready")
                process.send_signal(sig)
                _, errors = await asyncio.wait_for(process.communicate(), timeout=10)
                if process.returncode:
                    raise RuntimeError(errors.decode())
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()


def main() -> None:
    version = sys.argv[1]
    result = subprocess.run(
        ["bcn", "--version"], check=True, capture_output=True, text=True
    )
    if version not in result.stdout:
        raise RuntimeError("installed application CLI reports another version")
    with TemporaryDirectory(prefix="bcn-app-installed-") as directory:
        asyncio.run(exercise(Path(directory)))


if __name__ == "__main__":
    main()
