"""Exercise installed management through real indexes, uv, files, and lifespans."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import time
import tomllib
from contextlib import asynccontextmanager
from importlib.resources import files
from pathlib import Path
lazy from hashlib import sha256
lazy from typing import Any
lazy from uuid import uuid4

from bazaar_compute_node import run
from bazaar_compute_node.boot import bootstrap, build, node
from bazaar_contrib_config import ConfigDocument, ContribPackage, EnabledContrib
from bazaar_contrib_config_filesystem import FileConfigStore
from bazaar_contrib_invoke import COMMANDS
from bazaar_core import (
    Blocked,
    Composition,
    Context,
    Contrib,
    ContribEntry,
    Failed,
    Running,
)
from blockbuster import blockbuster_ctx
from packaging.requirements import Requirement
from packaging.version import Version

from bazaar_contrib_manager import CONTRIB_MANAGER, ContribManager, Operation, Package


async def invoke(options: dict[str, Any], action) -> Any:
    started = time.monotonic()
    selected: list[ContribManager] = []

    @asynccontextmanager
    async def lifespan(ctx: Context):
        selected.append(ctx.accept(CONTRIB_MANAGER))
        yield

    async with (
        node(
            start=lambda document: build(document).model_copy(
                update={"roots": ("contrib-manager",)}
            ),
            **options,
        ) as hub,
        hub.enable(
            Composition(
                entries=[
                    ContribEntry(
                        name="management-observer",
                        definition=Contrib(
                            accepts=[CONTRIB_MANAGER], lifespan=lifespan
                        ),
                    )
                ]
            )
        ),
    ):
        ready = time.monotonic()
        result = await action(selected[0])
        completed = time.monotonic()
    name = result.kind if isinstance(result, Operation) else "query"
    print(
        f"Manager {name}: startup {ready - started:.2f}s, "
        f"action {completed - ready:.2f}s, shutdown {time.monotonic() - completed:.2f}s",
        flush=True,
    )
    return result


async def exercise(path: Path, source: str, other_source: str, releases: Path) -> None:
    config = path / "config.toml"
    options: dict[str, Any] = {"configer_options": (str(config),)}
    payload = tomllib.loads(
        await asyncio.to_thread(
            files("bazaar_compute_node").joinpath("defaults.toml").read_text
        )
    )
    manager = next(
        entry
        for package in payload["contribs"]
        for entry in package["enabled"]
        if entry["entry"] == "contrib-manager"
    )
    manager["config"] = {"host_source": source}
    async with FileConfigStore(config) as store:
        await store.write(payload, expected_revision=None)
    results: list[Operation] = []

    async def checked(action) -> Any:
        result = await invoke(options, action)
        if isinstance(result, Operation):
            results.append(result)
            assert result.phase == "complete", result.model_dump_json(indent=2)
        return result

    async def cli(*arguments: str, config: Path = config):
        print("bcn --config", config, "contrib", *arguments, flush=True)
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "bazaar_compute_node",
            "--config",
            str(config),
            "contrib",
            *arguments,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        output, errors = await process.communicate()
        print((output + errors).decode(), end="", flush=True)
        assert process.returncode == 0, (output + errors).decode()
        return output.decode(), errors.decode()

    print("Read installed manager catalogs", flush=True)
    initial = await checked(lambda manager: manager.catalog.indexes())
    assert any(index.entries for index in initial)
    current = tomllib.loads(await asyncio.to_thread(config.read_text))
    index = next(
        i
        for i, entry in enumerate(
            entry for package in current["contribs"] for entry in package["enabled"]
        )
        if entry["entry"] == "contrib-manager"
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
                            "version": ">=0.1.0",
                        }
                    ]
                }
            ),
            "utf-8",
        )
        await checked(lambda manager, location=location: manager.index(str(location)))
    location = source.replace("/simple/", "/catalog.json")
    _, added_index = await cli("index", "add", location)
    assert location in added_index
    candidate = tomllib.loads(await asyncio.to_thread(config.read_text))
    assert location in candidate["contrib_indexes"]
    entries = await checked(lambda manager: manager.catalog.entries())
    assert any(
        entry.package == "bazaar-contrib-example"
        and entry.version
        and len(entry.indexes) > 1
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

    await cli(
        "add",
        "aiofiles",
        "--source",
        "https://pypi.org/simple",
        "--version",
        "==25.1.0",
    )
    output, _ = await cli("show", "aiofiles")
    assert "aiofiles" in output
    await cli("remove", "aiofiles")

    async def install(manager: ContribManager):
        package = await manager.catalog.select("example", source=source)
        return await manager.add(package)

    print("Install example from its local package index", flush=True)
    added = await checked(install)
    assert added.installed and added.config_saved
    detail = await checked(lambda manager: manager.detail("example"))
    assert any(entry.config_schema for entry in detail.entries)
    candidate = tomllib.loads(await asyncio.to_thread(config.read_text))
    document = ConfigDocument.model_validate(candidate)
    assert any(
        package.distribution == detail.package
        and Requirement(package.package).specifier.contains(detail.version)
        for package in document.contribs
    )

    # A private index publishes a newer release after the original lock is warm.
    version = Version(detail.version)
    wheel = next((releases / "registry" / "two" / "files").glob("*.whl"))
    await asyncio.to_thread(
        shutil.copyfile, wheel, path / "registry" / "one" / "files" / wheel.name
    )
    index = path / "registry" / "one" / "simple" / detail.package / "index.html"
    digest = sha256(await asyncio.to_thread(wheel.read_bytes)).hexdigest()
    await asyncio.to_thread(
        index.write_text,
        await asyncio.to_thread(index.read_text)
        + f'\n<a href="../../files/{wheel.name}#sha256={digest}">{wheel.name}</a>',
    )
    async with FileConfigStore(config) as store:
        snapshot = await store.read()
        assert snapshot is not None
        payload = ConfigDocument.model_validate(snapshot.payload).model_dump(
            mode="json"
        )
        for package in payload["contribs"]:
            if ContribPackage.model_validate(package).distribution == detail.package:
                package["package"] = f"{detail.package}>={version}"
        await store.write(payload, expected_revision=snapshot.revision)
    await cli("sync")
    current = await checked(lambda manager: manager.detail("example"))
    print(
        f"Normal sync selected {current.version}; previous version {version}",
        flush=True,
    )
    await cli("sync", "--upgrade")
    detail = await checked(lambda manager: manager.detail("example"))
    assert Version(detail.version) > version
    print(f"Explicit upgrade selected {detail.version}", flush=True)
    document = ConfigDocument.model_validate(
        tomllib.loads(await asyncio.to_thread(config.read_text))
    )
    # An operator can run healthy resources and repair an unavailable optional package.
    recovery = path / "recovery.toml"
    journal = path / "recovery.txt"
    packages = [
        package.model_copy(
            update={
                "enabled": (
                    EnabledContrib(
                        entry="example-file",
                        config={"path": str(journal), "label": "recovery"},
                    ),
                    EnabledContrib(entry="example-reader"),
                )
            }
        )
        if package.distribution == detail.package
        else package
        for package in document.contribs
    ]
    packages.append(
        ContribPackage(
            package="bazaar-contrib-unavailable==1",
            source=source,
            enabled=(EnabledContrib(entry="unavailable"),),
        )
    )
    async with FileConfigStore(recovery) as store:
        await store.write(
            document.model_copy(update={"contribs": tuple(packages)}).model_dump(
                mode="json"
            ),
            expected_revision=None,
        )
    output, _ = await cli("configured", config=recovery)
    assert "unavailable" in output and "example-file" in output

    async def activate(manager: ContribManager):
        await manager.configuration.activate()
        return manager.ctx.snapshots

    records = await invoke({"configer_options": (str(recovery),)}, activate)
    assert any(
        record.name == "example-file" and isinstance(record.state, Running)
        for record in records
    )
    assert any(
        record.name == "unavailable" and isinstance(record.state, Failed)
        for record in records
    )
    assert any(
        record.name == "example-reader" and isinstance(record.state, Blocked)
        for record in records
    )
    assert "released:recovery" in await asyncio.to_thread(journal.read_text)
    position = sum(len(package.enabled) for package in packages[:-1])
    await cli("disable", str(position), config=recovery)
    async with FileConfigStore(recovery) as store:
        snapshot = await store.read()
        assert snapshot is not None
        document = ConfigDocument.model_validate(snapshot.payload)
        await store.write(
            document.model_copy(
                update={
                    "contribs": tuple(
                        package
                        for package in document.contribs
                        if package.distribution != "bazaar-contrib-unavailable"
                    )
                }
            ).model_dump(mode="json"),
            expected_revision=snapshot.revision,
        )
    await cli("enable", "example", "--entry", "example-consumer", config=recovery)
    await cli("sync", config=recovery)
    records = await invoke({"configer_options": (str(recovery),)}, activate)
    assert any(
        record.name == "example-reader" and isinstance(record.state, Running)
        for record in records
    )
    assert "reader started" in await asyncio.to_thread(journal.read_text)

    # A legacy document remains readable until its full candidate can be saved.
    legacy = path / "legacy-recovery.toml"
    async with FileConfigStore(legacy) as store:
        await store.write(
            {"version": "4", "node": {"logging": {"retention_days": 0}}},
            expected_revision=None,
        )
    converted, _ = await invoke(
        {"configer_options": (str(legacy),)},
        lambda manager: manager.configuration.read(),
    )
    assert converted.changes
    position = next(
        index
        for index, entry in enumerate(
            entry
            for package in ConfigDocument.model_validate(converted.payload).contribs
            for entry in package.enabled
        )
        if entry.entry == "logging"
    )
    await cli("edit", str(position), "--option", "retention_days=7", config=legacy)
    async with FileConfigStore(legacy) as store:
        snapshot = await store.read()
        assert snapshot is not None
        ConfigDocument.model_validate(snapshot.payload)
    # Handwritten platform declarations can leave the installed-only enabled field out.
    async with FileConfigStore(config) as store:
        snapshot = await store.read()
        assert snapshot is not None
        payload = ConfigDocument.model_validate(snapshot.payload).model_dump(
            mode="json"
        )
        declarations = []
        for package in payload["contribs"]:
            if ContribPackage.model_validate(package).distribution == detail.package:
                package = dict(package)
                package.pop("enabled", None)
                declarations.extend(
                    (
                        {
                            **package,
                            "package": f"{detail.package}=={detail.version}; sys_platform != '{sys.platform}'",
                            "enabled": [],
                        },
                        {
                            **package,
                            "package": f"{detail.package}=={detail.version}; sys_platform == '{sys.platform}'",
                        },
                    )
                )
            else:
                declarations.append(package)
        await store.write(
            {**payload, "contribs": declarations},
            expected_revision=snapshot.revision,
        )
    for arguments, value in (
        (("list",), detail.package),
        (("show", "example"), detail.version),
        (("entries",), source),
        (("configured",), "contrib-manager"),
        (("operation", str(added.id)), str(added.id)),
    ):
        output, _ = await cli(*arguments)
        assert value in output
    enabled = await checked(
        lambda manager: manager.enable(
            "example",
            contrib="example-file",
            config={"path": str(path / "example.txt")},
        )
    )
    assert enabled.config_saved

    async def conflict(manager: ContribManager):
        package = await manager.catalog.select("example", source=source, version=">=9")
        return await manager.add(package)

    rejected = await invoke(options, conflict)
    assert rejected.error and rejected.phase == "failed"
    assert (await checked(lambda manager: manager.detail("example"))).entries
    writer = path / "user.txt"
    _, output = await cli(
        "add",
        "example",
        "--source",
        source,
        "--enable",
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
    assert "example-file" in output
    candidate = tomllib.loads(await asyncio.to_thread(config.read_text))
    index = next(
        i
        for i, entry in enumerate(
            entry for package in candidate["contribs"] for entry in package["enabled"]
        )
        if entry["entry"] == "example-file"
    )
    await cli(
        "edit",
        str(index),
        "--option",
        "metadata.project=studio",
        "--option",
        "tags.0=local",
        "--option",
        "labels.1.value=urgent",
    )
    candidate = tomllib.loads(await asyncio.to_thread(config.read_text))
    entries = [
        entry for package in candidate["contribs"] for entry in package["enabled"]
    ]
    assert entries[index]["config"]["metadata"]["project"] == "studio"
    await cli("add", "example", "--source", source, "--enable")
    candidate = tomllib.loads(await asyncio.to_thread(config.read_text))
    declaration = next(
        package
        for package in candidate["contribs"]
        if ContribPackage.model_validate(package).distribution == detail.package
        and ContribPackage.model_validate(package).applies
    )
    assert {entry.name for entry in detail.entries} <= {
        entry["entry"] for entry in declaration["enabled"]
    }
    for arguments, command in (
        (["--help"], "example"),
        (["example", "--help"], "write"),
    ):
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "bazaar_compute_node",
            "--config",
            str(config),
            *arguments,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        result, errors = await process.communicate()
        assert process.returncode == 0, (result + errors).decode()
        assert command in result.decode()
    output = path / "command note.txt"
    text, tag = uuid4().hex, uuid4().hex
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "bazaar_compute_node",
        "--config",
        str(config),
        "example",
        "write",
        text,
        "--output",
        str(output),
        "--tag",
        tag,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    result, errors = await process.communicate()
    assert process.returncode == 0, (result + errors).decode()
    note = json.loads(await asyncio.to_thread(output.read_text))
    assert text in note["text"] and tag in note["tags"]
    assert text in json.loads(result)["text"] and tag in json.loads(result)["tags"]
    candidate = tomllib.loads(await asyncio.to_thread(config.read_text))
    assert any(
        entry["config"].get("path") == str(writer)
        for package in candidate["contribs"]
        for entry in package["enabled"]
    )
    assert (await checked(lambda manager: manager.operation(added.id))).installed

    selected: dict[str, ContribManager] = {}
    groups = []

    @asynccontextmanager
    async def observer(ctx: Context):
        selected["manager"] = ctx.accept(CONTRIB_MANAGER)
        groups.append(ctx.accept(COMMANDS))
        yield

    consumer = ContribEntry(
        name="management-observer",
        definition=Contrib(
            accepts=[CONTRIB_MANAGER, COMMANDS],
            lifespan=observer,
        ),
    )
    entries, loader = await asyncio.to_thread(
        bootstrap,
        start=lambda document: build(document).model_copy(
            update={"roots": ("contrib-manager",)}
        ),
        **options,
    )
    async with run(entries, loader) as hub, hub.enable(Composition(entries=[consumer])):
        manager = selected["manager"]
        group = groups[-1]
        previous = {record.name: record.id for record in hub.snapshots}
        await manager.configuration.activate()
        current = {record.name: record.id for record in hub.snapshots}
        for name in (
            "contrib-manager",
            "commands",
            "node-configuration",
            "config-filesystem",
        ):
            assert current[name] == previous[name]
        assert groups[-1] is group
        assert "started:kept" in await asyncio.to_thread(writer.read_text)
    assert "released:kept" in await asyncio.to_thread(writer.read_text)
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
            for i, entry in enumerate(
                entry
                for package in candidate.payload["contribs"]
                for entry in package["enabled"]
            )
            if entry["entry"] == "example-file"
        )
        edited = await manager.edit(index, {"label": "edited"})
        assert edited.applied and edited.config_saved, edited.model_dump_json()
        assert "started:edited" in await asyncio.to_thread(writer.read_text)
        assert "released:kept" in await asyncio.to_thread(writer.read_text)
        candidate, _ = await manager.configuration.read()
        index = next(
            i
            for i, entry in enumerate(
                entry
                for package in candidate.payload["contribs"]
                for entry in package["enabled"]
            )
            if entry["entry"] == "contrib-manager"
        )
        own = await manager.edit(index, {"path": str(path / "changed-manager")})
        assert own.config_saved and own.pending_restart and own.error
        # Restore the saved manager parameters before continuing the package workflow.
        assert manager.configuration.snapshot is not None
        await manager.configuration.commit(
            candidate.payload, expected_revision=manager.configuration.snapshot.revision
        )
        versions = json.loads(
            await asyncio.to_thread((releases / "versions.json").read_text)
        )
        wheel = next(
            (path / "registry" / "one" / "files").glob(
                f"bazaar_compute_node-{versions['target']}-*.whl"
            )
        )
        index = (
            path / "registry" / "one" / "simple" / "bazaar-compute-node" / "index.html"
        )
        digest = sha256(await asyncio.to_thread(wheel.read_bytes)).hexdigest()
        print(f"Publish host {versions['target']} into the cached index", flush=True)
        await asyncio.to_thread(
            index.write_text,
            await asyncio.to_thread(index.read_text)
            + f'\n<a href="../../files/{wheel.name}#sha256={digest}">{wheel.name}</a>',
        )
        upgraded = await manager.upgrade(versions["target"])
        assert upgraded.installed and upgraded.pending_restart, (
            upgraded.model_dump_json(indent=2)
        )
        host = await manager.detail("bazaar-compute-node")
        assert Version(host.version) > Version(versions["current"])
        print(f"Host upgrade installed {host.version}", flush=True)
        candidate, _ = await manager.configuration.read()
        assert any(
            entry["config"].get("label") == "edited"
            and entry["config"].get("path") == str(writer)
            for package in candidate.payload["contribs"]
            for entry in package["enabled"]
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

    print("Change installed example package source", flush=True)
    replaced = await checked(change_source)
    assert replaced.installed
    detail = await checked(lambda manager: manager.detail("example"))
    assert detail.version.startswith("0.2")
    candidate = tomllib.loads(await asyncio.to_thread(config.read_text))
    assert any(
        entry["config"].get("label") == "edited"
        for package in candidate["contribs"]
        for entry in package["enabled"]
    )
    # Edit the standard dependency syntax just as an operator edits the portable document.
    async with FileConfigStore(config) as store:
        snapshot = await store.read()
        assert snapshot is not None
        payload = ConfigDocument.model_validate(snapshot.payload).model_dump(
            mode="json"
        )
        for entry in payload["contribs"]:
            if (
                ContribPackage.model_validate(entry).distribution == detail.package
                and ContribPackage.model_validate(entry).applies
            ):
                entry["package"] = (
                    "bazaar-contrib-example[text]>=0.2,<0.3; python_version >= '3.15'"
                )
        await store.write(payload, expected_revision=snapshot.revision)
    # A second node receives only the configuration, after installing the minimal host.
    migrated = path.parent / f"m-{uuid4().hex[:8]}"
    await asyncio.to_thread(migrated.mkdir)
    copied = migrated / "config.toml"
    await asyncio.to_thread(
        copied.write_bytes, await asyncio.to_thread(config.read_bytes)
    )
    environment = dict(os.environ) | {
        "BCN_HOME": str(migrated),
        "UV_TOOL_DIR": str(migrated / "tools"),
        "UV_TOOL_BIN_DIR": str(migrated / "bin"),
    }
    tool = Path(os.environ["UV_TOOL_DIR"]) / "bazaar-compute-node" == Path(sys.prefix)
    prefix = migrated / ("tools/bazaar-compute-node" if tool else "environment")

    async def launch(*arguments: str):
        print("Run", *arguments, flush=True)
        process = await asyncio.create_subprocess_exec(
            *arguments,
            env=environment,
            cwd=migrated,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        output, errors = await process.communicate()
        print((output + errors).decode(), end="", flush=True)
        assert process.returncode == 0, (output + errors).decode()
        return output.decode()

    if tool:
        await launch(
            "uv",
            "tool",
            "install",
            "--python",
            "3.15",
            "--index",
            source,
            "bazaar-compute-node",
        )
    else:
        await launch("uv", "venv", "--python", "3.15", str(prefix))
        await launch(
            "uv",
            "pip",
            "install",
            "--python",
            str(prefix),
            "--index",
            source,
            "bazaar-compute-node",
        )
    python = (await launch("uv", "python", "find", "--no-project", str(prefix))).strip()
    before = json.loads(
        await launch(str(python), "-m", "bazaar_contrib_manager.environment")
    )
    assert {entry["package"] for entry in before["packages"]}.isdisjoint(
        {
            "bazaar-contrib-example",
            "bazaar-contrib-logging",
            "bazaar-contrib-sidecar-unix",
            "bazaar-contrib-sidecar-windows",
        }
    )
    note = migrated / "copied-command.json"
    await launch(
        str(python),
        "-m",
        "bazaar_compute_node",
        "--config",
        str(copied),
        "example",
        "write",
        "migrated node",
        "--output",
        str(note),
    )
    assert (
        "migrated node" in json.loads(await asyncio.to_thread(note.read_text))["text"]
    )
    after = json.loads(
        await launch(str(python), "-m", "bazaar_contrib_manager.environment")
    )
    example = next(
        entry
        for entry in after["packages"]
        if entry["package"] == "bazaar-contrib-example"
    )
    assert example["version"].startswith("0.2") and example["entries"]
    copied_payload = tomllib.loads(await asyncio.to_thread(copied.read_text))
    package = next(
        package
        for package in copied_payload["contribs"]
        if ContribPackage.model_validate(package).distribution == detail.package
        and ContribPackage.model_validate(package).applies
    )
    entry = next(
        entry for entry in package["enabled"] if entry["entry"] == "example-file"
    )
    assert package["source"] == other_source and ">=0.2" in package["package"]
    assert entry["config"]["metadata"]["project"] == "studio"
    await launch(
        str(python), str(Path(__file__).with_name("migration_smoke.py")), str(copied)
    )
    assert "released:edited" in await asyncio.to_thread(writer.read_text)

    async with FileConfigStore(copied) as store:
        snapshot = await store.read()
        assert snapshot is not None
        payload = ConfigDocument.model_validate(snapshot.payload).model_dump(
            mode="json"
        )
        url = other_source.replace(
            "/simple/", "/files/bazaar_contrib_example-0.2.0-py3-none-any.whl"
        )
        for entry in payload["contribs"]:
            if (
                ContribPackage.model_validate(entry).distribution == detail.package
                and ContribPackage.model_validate(entry).applies
            ):
                entry["package"] = f"bazaar-contrib-example[text] @ {url}"
        await store.write(payload, expected_revision=snapshot.revision)
    await launch(
        str(python),
        "-m",
        "bazaar_compute_node",
        "--config",
        str(copied),
        "example",
        "write",
        "remote package",
        "--output",
        str(note),
    )
    after = json.loads(
        await launch(str(python), "-m", "bazaar_contrib_manager.environment")
    )
    assert (
        next(
            entry
            for entry in after["packages"]
            if entry["package"] == "bazaar-contrib-example"
        )["url"]
        == url
    )
    assert json.loads(await asyncio.to_thread(note.read_text))["text"]

    local = path / "local packages"
    await asyncio.to_thread(local.mkdir)
    wheel = local / "bazaar_contrib_example-0.2.0-py3-none-any.whl"
    await asyncio.to_thread(
        shutil.copyfile, releases / "registry" / "two" / "files" / wheel.name, wheel
    )
    directory = local / "example"
    await asyncio.to_thread(shutil.copytree, releases / "example-two", directory)
    for artifact in (wheel, directory):
        async with FileConfigStore(copied) as store:
            snapshot = await store.read()
            assert snapshot is not None
            payload = ConfigDocument.model_validate(snapshot.payload).model_dump(
                mode="json"
            )
            for entry in payload["contribs"]:
                if (
                    ContribPackage.model_validate(entry).distribution == detail.package
                    and ContribPackage.model_validate(entry).applies
                ):
                    entry["package"] = (
                        f"bazaar-contrib-example[text] @ {artifact.as_uri()}"
                    )
            await store.write(payload, expected_revision=snapshot.revision)
        await launch(
            str(python),
            "-m",
            "bazaar_compute_node",
            "--config",
            str(copied),
            "example",
            "write",
            "local package",
            "--output",
            str(note),
        )
        assert (
            "local package"
            in json.loads(await asyncio.to_thread(note.read_text))["text"]
        )

    blocked = await invoke(
        options, lambda manager: manager.remove("bazaar-contrib-example")
    )
    assert blocked.phase == "failed" and blocked.error
    assert (
        await checked(lambda manager: manager.detail("example"))
    ).version.startswith("0.2")
    for name in (
        "example-commands",
        "example-reader",
        "example-consumer",
        "example-file",
    ):
        candidate = tomllib.loads(await asyncio.to_thread(config.read_text))
        index = next(
            i
            for i, entry in enumerate(
                entry
                for package in candidate["contribs"]
                for entry in package["enabled"]
            )
            if entry["entry"] == name
        )
        await checked(lambda manager, index=index: manager.disable(index))

    await checked(
        lambda manager: manager.add(
            Package(package="bazaar-contrib-example-carrier", source=source)
        )
    )
    carrier = {"configer": "example-config", **options}
    for platform in (None, "win32" if sys.platform != "win32" else "linux"):
        async with FileConfigStore(config) as store:
            snapshot = await store.read()
            assert snapshot is not None
            original = snapshot.payload
            payload = ConfigDocument.model_validate(original).model_dump(mode="json")
            packages = []
            for package in payload["contribs"]:
                if package["package"].startswith("bazaar-contrib-example-carrier"):
                    if platform is None:
                        continue
                    package["package"] += f'; sys_platform == "{platform}"'
                packages.append(package)
            payload["contribs"] = packages
            await store.write(payload, expected_revision=snapshot.revision)
        synced = await invoke(carrier, lambda manager: manager.sync())
        assert synced.installed, synced.model_dump_json(indent=2)
        async with FileConfigStore(config) as store:
            snapshot = await store.read()
            assert snapshot is not None
            await store.write(original, expected_revision=snapshot.revision)
    protected = await invoke(
        carrier, lambda manager: manager.remove("bazaar-contrib-example-carrier")
    )
    assert protected.error and protected.phase == "failed"
    assert (await checked(lambda manager: manager.detail("example-carrier"))).entries
    print("Remove installed example packages", flush=True)
    removed = await checked(lambda manager: manager.remove("bazaar-contrib-example"))
    assert removed.installed
    await checked(lambda manager: manager.remove("bazaar-contrib-example-carrier"))
    await checked(lambda manager: manager.index(str(second), remove=True))
    await checked(lambda manager: manager.index(str(duplicate), remove=True))
    candidate = tomllib.loads(await asyncio.to_thread(config.read_text))
    index = next(
        i
        for i, entry in enumerate(
            entry for package in candidate["contribs"] for entry in package["enabled"]
        )
        if entry["entry"] == "contrib-manager"
    )
    output, _ = await cli("index", "list")
    assert location in output
    await cli("disable", str(index))
    async with node(**options) as hub:
        assert hub.snapshots
    await asyncio.to_thread(
        (path / "results.json").write_text,
        json.dumps([result.model_dump(mode="json") for result in results], indent=2),
        "utf-8",
    )


if __name__ == "__main__":
    with blockbuster_ctx():
        asyncio.run(
            exercise(Path(sys.argv[1]), sys.argv[2], sys.argv[3], Path(sys.argv[4]))
        )
    print("Installed manager workflow passed")
