"""Exercise installed management through real indexes, uv, files, and lifespans."""

from __future__ import annotations

import asyncio
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path
lazy from typing import Any

from bazaar_compute_node.boot import node
from bazaar_compute_node.contrib import invoke
from bazaar_core import Composition, Context, Contrib, ContribEntry
from blockbuster import blockbuster_ctx

from bazaar_contrib_manager import CONTRIB_MANAGER, ContribManager, Operation


async def exercise(path: Path, source: str, other_source: str) -> None:
    config = path / "config.toml"
    options: dict[str, Any] = {"configer_options": (str(config),)}
    results: list[Operation] = []

    async def checked(action) -> Any:
        result = await invoke(options, action)
        if isinstance(result, Operation):
            results.append(result)
            assert result.phase == "complete", result.model_dump_json(indent=2)
        return result

    async def cli(*arguments: str):
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "bazaar_compute_node",
            "contrib",
            "--config",
            str(config),
            *arguments,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        output, errors = await process.communicate()
        assert process.returncode == 0, (output + errors).decode()
        return json.loads(output)

    initial = await checked(lambda manager: manager.catalog.indexes())
    assert any(index.entries for index in initial)
    candidate, _ = await checked(lambda manager: manager.configuration.read())
    current = candidate.payload
    index = next(
        i
        for i, entry in enumerate(current["contribs"])
        if entry["contrib"] == "contrib-manager"
    )
    await checked(lambda manager: manager.edit(index, {"host_source": source}))
    local = path / "private.json"
    second = path / "second.json"
    duplicate = path / "duplicate.json"
    for location, package_source in (
        (local, source),
        (second, other_source),
        (duplicate, source),
    ):
        await asyncio.to_thread(
            location.write_text,
            json.dumps(
                {
                    "contribs": [
                        {
                            "package": "bazaar-contrib-example",
                            "source": package_source,
                        }
                    ]
                }
            ),
            "utf-8",
        )
        await checked(lambda manager, location=location: manager.index(str(location)))
    added_index = await cli("index", "add", source.replace("/simple/", "/catalog.json"))
    assert added_index["config_saved"]
    entries = await checked(lambda manager: manager.catalog.entries())
    assert any(
        entry.package == "bazaar-contrib-example" and len(entry.indexes) > 1
        for entry in entries
    )
    assert any(
        entry.package == "bazaar-contrib-example" and entry.source == other_source
        for entry in entries
    )
    await asyncio.to_thread(local.unlink)
    cached = await checked(lambda manager: manager.catalog.indexes())
    assert any(
        index.location == str(local) and index.cached and index.entries
        for index in cached
    )
    await checked(lambda manager: manager.index(str(local), remove=True))

    async def install(manager: ContribManager):
        package = await manager.catalog.select(
            "example", source=source, version=">=0.1,<0.2"
        )
        return await manager.add(package, enable=True)

    added = await checked(install)
    assert added.installed and added.config_saved and added.pending_restart
    detail = await checked(lambda manager: manager.detail("example"))
    assert any(entry.config_schema for entry in detail.entries)

    async def conflict(manager: ContribManager):
        package = await manager.catalog.select("example", source=source, version=">=9")
        return await manager.add(package)

    rejected = await invoke(options, conflict)
    assert rejected.error and rejected.phase == "failed"
    assert (await checked(lambda manager: manager.detail("example"))).entries
    writer = path / "user.txt"
    enabled = await cli(
        "enable",
        "example",
        "--entry",
        "example-file",
        "--option",
        f"path={writer}",
        "--option",
        "label=kept",
        "--option",
        "metadata.owner=alice",
        "--option",
        "metadata.project=bcn",
        "--option",
        "tags=team",
        "--option",
        "tags=remote",
        "--option",
        "labels.0.name=environment",
        "--option",
        "labels.0.value=development",
        "--option",
        "labels.1.name=priority",
        "--option",
        "labels.1.value=normal",
    )
    assert enabled["config_saved"]
    candidate, _ = await checked(lambda manager: manager.configuration.read())
    index = next(
        i
        for i, entry in enumerate(candidate.payload["contribs"])
        if entry["contrib"] == "example-file"
    )
    edited = await cli(
        "edit",
        str(index),
        "--option",
        "metadata.project=studio",
        "--option",
        "labels.1.value=urgent",
    )
    assert edited["config_saved"]
    await checked(lambda manager: manager.enable("example"))
    candidate, _ = await checked(lambda manager: manager.configuration.read())
    assert any(
        entry["config"].get("path") == str(writer)
        for entry in candidate.payload["contribs"]
    )
    assert (await checked(lambda manager: manager.operation(added.id))).installed

    selected: dict[str, ContribManager] = {}

    @asynccontextmanager
    async def observer(ctx: Context):
        selected["manager"] = ctx.accept(CONTRIB_MANAGER)
        yield

    consumer = ContribEntry(
        name="management-observer",
        definition=Contrib(
            accepts=[CONTRIB_MANAGER],
            lifespan=observer,
        ),
    )
    async with (
        node(**options) as hub,
        hub.enable(Composition(entries=[consumer])),
    ):
        manager = selected["manager"]
        assert "started:kept" in await asyncio.to_thread(writer.read_text)
        records = [
            json.loads(line)
            for line in (await asyncio.to_thread(writer.read_text)).splitlines()
            if line.startswith("{")
        ]
        assert any(
            record["metadata"]["owner"] == "alice"
            and record["metadata"]["project"] == "studio"
            and "remote" in record["tags"]
            and {"name": "environment", "value": "development"} in record["labels"]
            and {"name": "priority", "value": "urgent"} in record["labels"]
            for record in records
        )
        candidate, _ = await manager.configuration.read()
        index = next(
            i
            for i, entry in enumerate(candidate.payload["contribs"])
            if entry["contrib"] == "example-file"
        )
        edited = await manager.edit(index, {"label": "edited"})
        assert edited.applied and edited.config_saved, edited.model_dump_json()
        assert "started:edited" in await asyncio.to_thread(writer.read_text)
        assert "released:kept" in await asyncio.to_thread(writer.read_text)
        candidate, _ = await manager.configuration.read()
        index = next(
            i
            for i, entry in enumerate(candidate.payload["contribs"])
            if entry["contrib"] == "contrib-manager"
        )
        own = await asyncio.wait_for(
            manager.edit(index, {"path": str(path / "changed-manager")}), 10
        )
        assert own.config_saved and own.pending_restart and own.error
        # Restore the saved manager parameters before continuing the package workflow.
        assert manager.configuration.snapshot is not None
        await manager.configuration.commit(
            candidate.payload, expected_revision=manager.configuration.snapshot.revision
        )
        upgraded = await manager.upgrade("0.3.1")
        assert upgraded.installed and upgraded.pending_restart, (
            upgraded.model_dump_json(indent=2)
        )
        candidate, _ = await manager.configuration.read()
        assert any(
            entry["config"].get("label") == "edited"
            and entry["config"].get("path") == str(writer)
            for entry in candidate.payload["contribs"]
        )
    assert "released:edited" in await asyncio.to_thread(writer.read_text)
    async with (
        node(**options) as hub,
        hub.enable(Composition(entries=[consumer])),
    ):
        assert "started:edited" in await asyncio.to_thread(writer.read_text)

    async def change_source(manager: ContribManager):
        package = await manager.catalog.select(
            "example", source=other_source, version=">=0.2,<0.3"
        )
        return await manager.add(package)

    replaced = await checked(change_source)
    assert replaced.installed
    detail = await checked(lambda manager: manager.detail("example"))
    assert detail.version.startswith("0.2")
    candidate, _ = await checked(lambda manager: manager.configuration.read())
    assert any(
        entry["config"].get("label") == "edited"
        for entry in candidate.payload["contribs"]
    )
    blocked = await invoke(
        options, lambda manager: manager.remove("bazaar-contrib-example")
    )
    assert blocked.phase == "failed" and blocked.error
    assert (
        await checked(lambda manager: manager.detail("example"))
    ).version.startswith("0.2")
    for name in ("example-consumer", "example-file"):
        candidate, _ = await checked(lambda manager: manager.configuration.read())
        index = next(
            i
            for i, entry in enumerate(candidate.payload["contribs"])
            if entry["contrib"] == name
        )
        await checked(lambda manager, index=index: manager.disable(index))

    carrier = {"configer": "example-config", **options}
    protected = await invoke(
        carrier, lambda manager: manager.remove("bazaar-contrib-example")
    )
    assert protected.error and protected.phase == "failed"
    assert (await checked(lambda manager: manager.detail("example"))).entries
    removed = await checked(lambda manager: manager.remove("bazaar-contrib-example"))
    assert removed.installed
    await checked(lambda manager: manager.index(str(second), remove=True))
    await checked(lambda manager: manager.index(str(duplicate), remove=True))
    candidate, _ = await checked(lambda manager: manager.configuration.read())
    index = next(
        i
        for i, entry in enumerate(candidate.payload["contribs"])
        if entry["contrib"] == "contrib-manager"
    )
    await cli("disable", str(index))
    assert any(index["entries"] for index in await cli("index", "list"))
    async with node(**options) as hub:
        assert hub.snapshots
    await asyncio.to_thread(
        (path / "results.json").write_text,
        json.dumps([result.model_dump(mode="json") for result in results], indent=2),
        "utf-8",
    )


if __name__ == "__main__":
    with blockbuster_ctx():
        asyncio.run(exercise(Path(sys.argv[1]), sys.argv[2], sys.argv[3]))
    print("Installed manager workflow passed")
