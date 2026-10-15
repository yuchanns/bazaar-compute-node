"""Run the installed configuration workflow against real files, TCP, and SQLite."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
lazy import json
lazy import signal
lazy import tomllib
lazy from contextlib import asynccontextmanager
lazy from importlib.resources import files
lazy from uuid import uuid7

from bazaar_contrib_config import (
    CONFIGURATION,
    ConfigConflict,
    ConfigDocument,
    Configuration,
)
from bazaar_contrib_status import CONTRIB_STATUS, IContribStatus
from bazaar_core import (
    Blocked,
    Composition,
    Context,
    Contrib,
    ContribEntry,
    Failed,
    Running,
    discover,
)
from bcn_example_contribs import RemoteConfig, RemoteStore
from bcn_example_contribs.center import ConfigCenter
from bcn_example_contribs.databases import RECORDS, Records
from blockbuster import blockbuster_ctx
lazy import aiosqlite
lazy import tomli_w

from bazaar_compute_node.boot import build, node


async def exercise_server(path: Path) -> None:
    config = path / "mixed.toml"
    port_file = path / "server.port"
    directory = path / "mixed-journals"
    entries = [
        {
            "contrib": "example-server",
            "config": {"database": str(path / "server.sqlite"), "port": str(port_file)},
        },
        {"contrib": "example-node-records", "config": {"name": "node"}},
        {
            "contrib": "example-node-database",
            "config": {"path": str(path / "node.sqlite")},
        },
        {"contrib": "example-directory", "config": {"path": str(directory)}},
        {"contrib": "example-journal"},
    ]
    payload = {
        "version": "5",
        "contribs": entries,
        "agent": [{"id": str(uuid7()), "name": "server"}],
    }
    await asyncio.to_thread(config.write_text, tomli_w.dumps(payload))
    configurations: list[Configuration] = []
    records: list[Records] = []

    @asynccontextmanager
    async def observe(ctx: Context):
        configurations.append(ctx.accept(CONFIGURATION))
        records.append(ctx.accept(RECORDS))
        yield

    async def read_server():
        port = int(await asyncio.to_thread(port_file.read_text))
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            writer.write(b"records\n")
            await writer.drain()
            return json.loads(await reader.readline())
        finally:
            writer.close()
            await writer.wait_closed()

    consumer = ContribEntry(
        name="mixed-observer",
        definition=Contrib(accepts=[CONFIGURATION, RECORDS], lifespan=observe),
    )
    async with (
        node(configer_options=(str(config),)) as hub,
        hub.enable(Composition(entries=[consumer])),
    ):
        assert ["server", "started"] in await read_server()
        assert ("node", "started") in await records[0].read()
        assert all(name == "server" for name, _ in await read_server())
        assert all(name == "node" for name, _ in await records[0].read())
        service = configurations[0]
        assert service.snapshot is not None
        saved = await service.commit(
            payload
            | {
                "contribs": [
                    entry for entry in entries if entry["contrib"] != "example-server"
                ]
            },
            expected_revision=service.snapshot.revision,
        )
        applied = await service.apply(saved)
        assert applied.applied, applied.error
        await records[0].write("node", "continues after server stops")
        assert ("node", "continues after server stops") in await records[0].read()
        assert "started" in await asyncio.to_thread(
            (directory / "server.txt").read_text
        )
    assert "released" in await asyncio.to_thread((directory / "server.txt").read_text)
    for name in ("node", "server"):
        async with (
            aiosqlite.connect(path / f"{name}.sqlite") as database,
            database.execute("SELECT name, state FROM records") as cursor,
        ):
            assert any(
                row[0] == name and row[1] == "released"
                for row in await cursor.fetchall()
            )
    await asyncio.to_thread(
        config.write_text,
        tomli_w.dumps(
            {
                "version": "5",
                "contribs": [
                    entry for entry in entries if entry["contrib"] == "example-server"
                ],
            }
        ),
    )
    async with node(configer_options=(str(config),)):
        assert ["server", "started"] in await read_server()


async def exercise(path: Path) -> None:
    await exercise_server(path)
    config = path / "config.toml"
    directory = path / "journals"
    log = path / "info" / "node.jsonl"
    entries = [
        {
            "contrib": "logging",
            "config": {
                "path": str(path / "node.jsonl"),
                "paths": {
                    "info": str(log),
                    "error": str(path / "errors" / "node.jsonl"),
                },
                "retention_days": 7,
                "max_size": "32 KiB",
            },
        },
        {"contrib": "example-journal"},
        {"contrib": "status"},
        {"contrib": "example-directory", "config": {"path": str(directory)}},
    ]
    payload = {
        "version": "5",
        "contribs": entries,
        "agent": [{"id": str(uuid7()), "name": name} for name in ("Cloud", "Tifa")],
    }
    await asyncio.to_thread(config.write_text, tomli_w.dumps(payload))
    observed: dict[str, Configuration | IContribStatus] = {}

    @asynccontextmanager
    async def observe(ctx: Context):
        observed.update(
            configuration=ctx.accept(CONFIGURATION), status=ctx.accept(CONTRIB_STATUS)
        )
        yield

    consumer = ContribEntry(
        name="configuration-consumer",
        definition=Contrib(accepts=[CONFIGURATION, CONTRIB_STATUS], lifespan=observe),
    )
    async with (
        node(configer_options=(str(config),)) as hub,
        hub.enable(Composition(entries=[consumer])),
    ):
        assert any(
            record.name == "example-journal" and isinstance(record.state, Running)
            for record in hub.snapshots
        )
        service = observed["configuration"]
        assert isinstance(service, Configuration)
        assert service.snapshot is not None
        updated = dict(payload) | {
            "contrib_indexes": ["https://example.com/contribs.json"]
        }
        saved = await service.commit(
            updated, expected_revision=service.snapshot.revision
        )
        assert saved.payload["contrib_indexes"]
        document = ConfigDocument.model_validate(
            dict(payload)
            | {
                "contribs": [{"contrib": "example-journal"}],
                "agent": [{"id": str(uuid7()), "name": "Aerith"}],
            }
        )
        async with hub.enable(await asyncio.to_thread(build, document)):
            assert "started" in await asyncio.to_thread(
                (directory / "Aerith.txt").read_text
            )
        for name in ("Cloud", "Tifa"):
            assert "started" in await asyncio.to_thread(
                (directory / f"{name}.txt").read_text
            )
    for name in ("Cloud", "Tifa", "Aerith"):
        assert "released" in await asyncio.to_thread(
            (directory / f"{name}.txt").read_text
        )
    records = []
    for file in await asyncio.to_thread(lambda: list(log.parent.glob("node.jsonl*"))):
        records.extend(
            json.loads(line)
            for line in (await asyncio.to_thread(file.read_text)).splitlines()
        )
    for event in ("node.started", "node.stopping"):
        assert any(
            record["event_name"] == event
            and record["source"]["module"] == "bazaar_compute_node"
            for record in records
        )
    assert any(
        record["event_name"] == "contrib.running"
        and "Aerith" in record["metadata"].get("location", "")
        and record["metadata"].get("contrib") == "example-journal"
        and record["source"]["contrib"] == "node-audit"
        and record["source"]["module"] == "bazaar_compute_node.audit"
        for record in records
    )
    async with node(configer_options=(str(config),)) as hub:
        assert any(record.name == "Cloud" for record in hub.snapshots)
    await asyncio.to_thread(
        config.write_text, tomli_w.dumps(dict(payload) | {"agent": []})
    )
    async with node(configer_options=(str(config),)) as hub:
        assert not any(record.name == "example-journal" for record in hub.snapshots)
        configurations: list[Configuration] = []

        @asynccontextmanager
        async def observe_late(ctx: Context):
            configurations.append(ctx.accept(CONFIGURATION))
            yield

        async with hub.enable(
            Composition(
                entries=[
                    ContribEntry(
                        name="late-configuration",
                        definition=Contrib(
                            accepts=[CONFIGURATION], lifespan=observe_late
                        ),
                    )
                ]
            )
        ):
            service = configurations[0]
            assert service.snapshot is not None
            late = await service.commit(
                dict(payload) | {"agent": [{"id": str(uuid7()), "name": "late"}]},
                expected_revision=service.snapshot.revision,
            )
            result = await service.apply(late)
            assert result.applied, result.error
            assert "started" in await asyncio.to_thread(
                (directory / "late.txt").read_text
            )
    assert "released" in await asyncio.to_thread((directory / "late.txt").read_text)
    writer = path / "independent.txt"
    broken = dict(payload) | {
        "contribs": [
            {"contrib": "example-broken"},
            {"contrib": "example-directory", "config": {"path": []}},
            {"contrib": "example-journal"},
            {"contrib": "example-dependent"},
            {"contrib": "example-writer", "config": {"path": str(writer)}},
            {"contrib": "status"},
        ]
    }
    await asyncio.to_thread(config.write_text, tomli_w.dumps(broken))
    async with (
        node(configer_options=(str(config),)) as hub,
        hub.enable(Composition(entries=[consumer])),
    ):
        service = observed["status"]
        assert not isinstance(service, Configuration)
        records = service.snapshot()
        assert any(
            record.name == "example-broken" and isinstance(record.state, Failed)
            for record in records
        )
        assert any(
            record.name == "example-directory" and isinstance(record.state, Failed)
            for record in records
        )
        assert any(
            record.name == "example-journal" and isinstance(record.state, Blocked)
            for record in records
        )
        assert any(
            record.name == "example-dependent" and isinstance(record.state, Blocked)
            for record in records
        )
        assert any(
            record.name == "example-writer" and isinstance(record.state, Running)
            for record in records
        )
    assert "released" in await asyncio.to_thread(writer.read_text)
    await asyncio.to_thread(config.unlink)

    @asynccontextmanager
    async def observe_configuration(ctx: Context):
        observed["configuration"] = ctx.accept(CONFIGURATION)
        yield

    consumer = ContribEntry(
        name="configuration-consumer",
        definition=Contrib(accepts=[CONFIGURATION], lifespan=observe_configuration),
    )
    center = ConfigCenter(path / "center.sqlite3")
    port = await center.start()
    options = (f"port={port}", "key=node-a")
    peer = RemoteStore(RemoteConfig(port=port, key="node-a"))
    try:
        center.drop_response = True
        async with (
            node(configer="example-config", configer_options=options) as hub,
            hub.enable(Composition(entries=[consumer])),
        ):
            service = observed["configuration"]
            assert isinstance(service, Configuration)
            assert service.snapshot is not None
            revision = service.snapshot.revision
            updates = [
                {
                    "version": "5",
                    "contribs": [{"contrib": "status"}],
                    "contrib_indexes": [f"https://example.com/{name}.json"],
                }
                for name in ("one", "two")
            ]
            results = await asyncio.gather(
                service.commit(updates[0], expected_revision=revision),
                peer.write(updates[1], expected_revision=revision),
                return_exceptions=True,
            )
            assert any(isinstance(result, ConfigConflict) for result in results)
            current = await peer.read()
            assert current is not None
            center.drop_response = True
            saved = await service.commit(updates[0], expected_revision=current.revision)
            assert saved.payload["contrib_indexes"] == updates[0]["contrib_indexes"]
            resource = await asyncio.to_thread(
                files("bcn_example_contribs").joinpath("defaults.toml").read_text
            )
            resource = tomllib.loads(resource)
            user = dict(updates[0]) | {
                "contribs": [
                    {"contrib": "commands"},
                    {
                        "contrib": "example-writer",
                        "config": {"path": str(path / "user-writer.txt")},
                    },
                    {
                        "contrib": "example-writer",
                        "config": {"path": str(path / "second-writer.txt")},
                    },
                ]
            }
            saved = await service.commit(user, expected_revision=saved.revision)
            # A concurrent editor changes the source while this caller imports.
            center.pause_reads = asyncio.Event()
            importing = asyncio.create_task(service.import_defaults(resource))
            await asyncio.wait_for(center.read_requests.get(), 10)
            saved = await peer.write(
                user | {"contrib_indexes": ["https://example.com/editor.json"]},
                expected_revision=saved.revision,
            )
            center.pause_reads.set()
            try:
                await importing
            except ConfigConflict:
                pass
            else:
                raise AssertionError("the caller must see the concurrent edit")
            center.pause_reads = None
            # The caller explicitly reads a new snapshot through a new import.
            imported = await service.import_defaults(resource)
            assert (
                imported.payload["contrib_indexes"] == saved.payload["contrib_indexes"]
            )
            selected = {
                entry["contrib"]: entry.get("config", {})
                for entry in ConfigDocument.model_validate(imported.payload).model_dump(
                    mode="json"
                )["contribs"]
            }
            writers = [
                entry.config["path"]
                for entry in ConfigDocument.model_validate(imported.payload).contribs
                if entry.contrib == "example-writer"
            ]
            assert str(path / "user-writer.txt") in writers
            assert str(path / "second-writer.txt") in writers
            assert "example-added" in selected
            assert "status" not in selected
            saved = await service.commit(
                dict(imported.payload)
                | {
                    "contribs": [
                        {"contrib": "status"},
                        *ConfigDocument.model_validate(imported.payload).model_dump(
                            mode="json"
                        )["contribs"],
                    ]
                },
                expected_revision=imported.revision,
            )
        await center.close()
        center = ConfigCenter(path / "center.sqlite3")
        options = (f"port={await center.start()}", "key=node-a")
        async with node(configer="example-config", configer_options=options) as hub:
            assert any(
                record.name == "status" and isinstance(record.state, Running)
                for record in hub.snapshots
            )
        for name in ("user-writer.txt", "second-writer.txt"):
            assert "released" in await asyncio.to_thread((path / name).read_text)
        assert not await asyncio.to_thread(
            (path / "remote" / ".bcn" / "config.toml").exists
        )
        center.pause_reads = asyncio.Event()
        ready: asyncio.Queue[object] = asyncio.Queue()
        stopped = asyncio.Event()

        async def competing_node():
            try:
                async with node(
                    configer="example-config",
                    configer_options=(*options, "key=new-shared-node"),
                ) as hub:
                    ready.put_nowait(hub)
                    await stopped.wait()
            except* ConfigConflict as error:
                ready.put_nowait(error)

        async with asyncio.TaskGroup() as group:
            for _ in range(2):
                group.create_task(competing_node())
            for _ in range(2):
                await asyncio.wait_for(center.read_requests.get(), 10)
            center.pause_reads.set()
            results = [await asyncio.wait_for(ready.get(), 10) for _ in range(2)]
            assert any(isinstance(result, BaseExceptionGroup) for result in results)
            assert any(not isinstance(result, BaseExceptionGroup) for result in results)
            stopped.set()
        center.pause_reads = None
        # A new caller can start using the document already committed by its peer.
        async with node(
            configer="example-config",
            configer_options=(*options, "key=new-shared-node"),
        ):
            pass
        center.reading.clear()
        if sys.platform != "win32":
            writer = path / "user-writer.txt"
            await asyncio.to_thread(writer.unlink)
            process = await asyncio.create_subprocess_exec(
                "bcn",
                "--configer",
                "example-config",
                "--configer-options",
                options[0],
                "--config",
                "key=new-shared-node",
                "--configer-options",
                options[1],
                "run",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                assert process.stdout is not None
                assert await asyncio.wait_for(process.stdout.readline(), 15)
                process.send_signal(signal.SIGTERM)
                _, errors = await asyncio.wait_for(process.communicate(), 15)
                assert process.returncode == 0, errors.decode()
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
            assert "released" in await asyncio.to_thread(writer.read_text)
        center.pause_reads = asyncio.Event()

        async def startup():
            async with node(configer="example-config", configer_options=options):
                await asyncio.Future()

        task = asyncio.create_task(startup())
        try:
            await asyncio.wait_for(center.reading.wait(), 10)
            task.cancel()
            result = await asyncio.gather(task, return_exceptions=True)
            assert isinstance(result[0], asyncio.CancelledError)
        finally:
            center.pause_reads.set()
    finally:
        await center.close()
        assert not center.connections
        assert not center.tasks
    installed = await asyncio.to_thread(discover)
    assert any(
        source.package == "bcn-example-contribs" and source.name == "example-config"
        for source in installed
    )
    assert not await asyncio.to_thread(config.exists)


def main() -> None:
    path = Path(sys.argv[1])
    os.environ["BCN_HOME"] = str(path / "remote")
    logging.getLogger("bazaar_core").addHandler(logging.NullHandler())
    with blockbuster_ctx():
        asyncio.run(exercise(path))
    print("Installed configuration workflow passed")


if __name__ == "__main__":
    main()
