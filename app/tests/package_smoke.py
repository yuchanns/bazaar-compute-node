"""Exercise the installed node application and its process entry point."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
lazy from contextlib import asynccontextmanager
lazy from uuid import uuid7

from bazaar_contrib_config import (
    ConfigDocument,
    ContribPackage,
    EnabledContrib,
)
from bazaar_contrib_config_filesystem import FileConfigStore
from bazaar_contrib_manager import CONTRIB_CATALOG, ContribCatalog
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
    payload = ConfigDocument(
        contrib_indexes=("resource://bazaar_contrib_manager/catalog.json",),
        contribs=(
            ContribPackage(
                enabled=(
                    EnabledContrib(entry="commands"),
                    EnabledContrib(entry="contrib-manager"),
                )
            ),
        ),
    ).model_dump(mode="json")
    async with FileConfigStore(configuration) as store:
        await store.write(payload, expected_revision=None)
    catalogs: list[ContribCatalog] = []

    @asynccontextmanager
    async def observe(ctx: Context):
        catalogs.append(ctx.accept(CONTRIB_CATALOG))
        yield

    async with (
        node() as hub,
        hub.enable(
            Composition(
                entries=[
                    ContribEntry(
                        name="catalog-observer",
                        definition=Contrib(accepts=[CONTRIB_CATALOG], lifespan=observe),
                    )
                ]
            )
        ),
    ):
        for name in ("node-configuration", "contrib-manager"):
            if not any(
                record.name == name and isinstance(record.state, Running)
                for record in hub.snapshots
            ):
                raise RuntimeError(f"installed {name} did not become ready")
        indexes = await catalogs[0].indexes()
        assert any(index.entries and index.error is None for index in indexes)
    async with FileConfigStore(configuration) as store:
        snapshot = await store.read()
        assert snapshot is not None
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
            instance.name == "installed" and isinstance(instance.state, Running)
            for instance in hub.snapshots
        ):
            raise RuntimeError("installed resource did not start")
    text = await asyncio.to_thread((path / "installed.txt").read_text)
    if "agent started" not in text or "agent released" not in text:
        raise RuntimeError("installed node did not complete the agent lifecycle")


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
