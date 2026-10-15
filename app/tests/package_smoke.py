"""Exercise the installed node application and its process entry point."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
lazy import json
lazy import signal
lazy from contextlib import asynccontextmanager
lazy from importlib.resources import files
lazy from uuid import uuid7

from bazaar_contrib_config import ConfigDocument, PackageRequirement
from bazaar_contrib_config_filesystem import FileConfigStore
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
from bazaar_compute_node.boot import node

DIRECTORY = ServiceKey[Path]("installed.directory")


async def exercise(path: Path) -> None:
    configuration = path / ".bcn" / "config.toml"
    async with node() as hub:
        for name in ("node-configuration", "sidecar"):
            if not any(
                record.name == name and isinstance(record.state, Running)
                for record in hub.snapshots
            ):
                raise RuntimeError(f"installed {name} did not become ready")
    async with FileConfigStore(configuration) as store:
        snapshot = await store.read()
        assert snapshot is not None
        document = ConfigDocument.model_validate(snapshot.payload)
        catalog = Path(document.contrib_indexes[0])
        content = await asyncio.to_thread(catalog.read_bytes)
        resource = await asyncio.to_thread(files, "bazaar_compute_node")
        assert content == await asyncio.to_thread(
            resource.joinpath("catalog.json").read_bytes
        )
        entries = [
            PackageRequirement.model_validate(entry)
            for entry in json.loads(content)["contribs"]
        ]
        assert entries
        payload = dict(snapshot.payload) | {
            "agent": [{"id": str(uuid7()), "name": "configured"}]
        }
        await store.write(payload, expected_revision=snapshot.revision)
    async with node(configer_options=(str(configuration),)) as hub:
        if not any(record.name == "configured" for record in hub.snapshots):
            raise RuntimeError("installed node did not load its saved agent")

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
                "--configer-options",
                str(
                    path
                    / (
                        "ignored.toml"
                        if sig == signal.SIGTERM
                        else "options with spaces.toml"
                    )
                ),
                *(
                    ["--config", str(path / "selected.toml")]
                    if sig == signal.SIGTERM
                    else []
                ),
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
                configured = path / (
                    "selected.toml"
                    if sig == signal.SIGTERM
                    else "options with spaces.toml"
                )
                if not await asyncio.to_thread(configured.is_file):
                    raise RuntimeError(
                        "installed CLI did not use the selected configuration path"
                    )
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
    with TemporaryDirectory(
        prefix="bcn-app-installed-", dir="/tmp" if os.name == "posix" else None
    ) as directory:
        directory = Path(directory) / "node 本地 🌙"
        os.environ["BCN_HOME"] = str(directory)
        asyncio.run(exercise(directory))


if __name__ == "__main__":
    main()
