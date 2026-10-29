"""Run installed contrib commands against a separate node and real file resources."""

from __future__ import annotations

import asyncio
import json
import sys
import tomllib
from contextlib import asynccontextmanager
from importlib.resources import files
from pathlib import Path

from bazaar_compute_node.boot import node
from bazaar_contrib_config import ConfigDocument, Configuration
from bazaar_contrib_config_filesystem import FileConfigStore
from packaging.requirements import Requirement

from bazaar_contrib_manager import Operation


async def serve(config: Path):
    async with node(configer_options=(str(config),)) as hub:
        Configuration.check(hub, force=True)
        print("ready", flush=True)
        await asyncio.to_thread(sys.stdin.readline)


@asynccontextmanager
async def running(config: Path):
    print("Start managed node with", config, flush=True)
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        __file__,
        "--node",
        str(config),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )
    assert process.stdin and process.stdout
    try:
        if not await process.stdout.readline():
            raise RuntimeError("Managed node exited before readiness")
        yield process
    finally:
        if process.returncode is None:
            process.stdin.write(b"stop\n")
            await process.stdin.drain()
            await process.communicate()
            assert process.returncode == 0


async def exercise(path: Path, source: str):
    config = path / "live.toml"
    journal = path / "live.txt"
    payload = tomllib.loads(
        await asyncio.to_thread(
            files("bazaar_compute_node").joinpath("defaults.toml").read_text
        )
    )
    catalog = path / "live-catalog.json"
    await asyncio.to_thread(
        catalog.write_text,
        json.dumps(
            {"contribs": [{"package": "bazaar-contrib-example", "source": source}]}
        ),
    )
    payload["contrib_indexes"] = [str(catalog)]
    official = json.loads(
        await asyncio.to_thread(
            files("bazaar_contrib_manager").joinpath("catalog.json").read_text
        )
    )
    packages = {entry["package"] for entry in official["contribs"]}
    for package in payload["contribs"]:
        package["source"] = (
            source
            if Requirement(package["package"]).name in packages
            else "https://pypi.org/simple"
        )
    manager = next(
        entry
        for package in payload["contribs"]
        for entry in package["enabled"]
        if entry["entry"] == "contrib-manager"
    )
    manager["config"] = {"host_source": source}
    for package in payload["contribs"]:
        for entry in package["enabled"]:
            if entry["entry"] == "logging":
                entry["config"] = {"level": "WARNING"}
    async with FileConfigStore(config) as store:
        await store.write(payload, expected_revision=None)

    async def cli(*arguments: str, success: bool = True):
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
        text = (output + errors).decode()
        if success:
            assert process.returncode == 0, text
        else:
            assert process.returncode, text
        return text

    async def position(name: str) -> int:
        async with FileConfigStore(config) as store:
            snapshot = await store.read()
            assert snapshot is not None
            document = ConfigDocument.model_validate(snapshot.payload)
            return next(
                index
                for index, entry in enumerate(
                    entry for package in document.contribs for entry in package.enabled
                )
                if entry.entry == name
            )

    async with running(config) as process:
        text = await cli(
            "add",
            "example",
            "--source",
            source,
            "--enable",
            "example-file",
            "--option",
            f"path={journal}",
            "--option",
            "label=online",
        )
        assert "Applied configuration" in text, text
        assert "started:online" in await asyncio.to_thread(journal.read_text)
        await cli("enable", "example", "--entry", "example-consumer")
        await cli("enable", "example", "--entry", "example-reader")
        index = await position("example-file")
        await asyncio.gather(
            cli("edit", str(index), "--option", "metadata.owner=alice"),
            cli("edit", str(index), "--option", "metadata.project=studio"),
        )
        records = [
            json.loads(line)
            for line in (await asyncio.to_thread(journal.read_text)).splitlines()
            if line.startswith("{")
        ]
        assert any(
            record["metadata"]["owner"] == "alice"
            and record["metadata"]["project"] == "studio"
            for record in records
        )
        protected = await cli("disable", str(index), success=False)
        assert (
            "example-consumer" in protected
            and "example.file" in protected
            and "--force" in protected
        )
        text = await cli("disable", str(index), "--force")
        assert "Applied configuration" in text, text
        text = await asyncio.to_thread(journal.read_text)
        assert text.rindex("reader released") < text.rindex("consumer released")
        assert text.rindex("consumer released") < text.rindex("released:online")

    async with running(config) as process:
        await cli("index", "add", source.replace("/simple/", "/catalog.json"))
        await cli("sync")
        await cli(
            "enable",
            "example",
            "--entry",
            "example-file",
            "--option",
            f"path={journal}",
            "--option",
            "label=restored",
        )
        text = await asyncio.to_thread(journal.read_text)
        assert "started:restored" in text
        assert "reader started" in text[text.index("started:restored") :]
        await cli("disable", str(await position("example-reader")))
        await cli("disable", str(await position("example-consumer")))
        text = await cli("disable", str(await position("example-file")))
        assert "Applied configuration" in text
        assert "released:restored" in await asyncio.to_thread(journal.read_text)
        assert process.returncode is None

    # A missing endpoint saves offline; no file resource starts in the command process.
    offline = path / "offline.txt"
    await cli(
        "enable",
        "example",
        "--entry",
        "example-file",
        "--option",
        f"path={offline}",
        "--option",
        "label=offline",
    )
    async with running(config):
        assert "started:offline" in await asyncio.to_thread(offline.read_text)

    # A real slow lifespan exceeds the client's existing timeout after saving remotely.
    async with FileConfigStore(config) as store:
        snapshot = await store.read()
        assert snapshot is not None
        payload = ConfigDocument.model_validate(snapshot.payload).model_dump(
            mode="json"
        )
        for package in payload["contribs"]:
            for entry in package["enabled"]:
                if entry["entry"] in {"sidecar-unix", "sidecar-windows"}:
                    entry["config"] = {"timeout": 1}
        await store.write(payload, expected_revision=snapshot.revision)
    async with running(config):
        text = await cli(
            "edit",
            str(await position("example-file")),
            "--option",
            "delay=3",
            "--option",
            "label=delayed",
            success=False,
        )
        assert "Saved configuration" in text and "Operation:" in text, text
        operations = [
            Operation.model_validate_json(await asyncio.to_thread(file.read_bytes))
            for file in (path / ".bcn" / "contrib" / "environments").glob(
                "*/operations/*.json"
            )
        ]
        assert any(
            operation.kind == "edit"
            and operation.config_saved
            and operation.phase == "failed"
            and operation.error == "TimeoutError"
            for operation in operations
        )
        await asyncio.sleep(3)
        assert "started:delayed" in await asyncio.to_thread(offline.read_text)

    # A composition without a client provider uses local management directly.
    async with FileConfigStore(config) as store:
        snapshot = await store.read()
        assert snapshot is not None
        payload = ConfigDocument.model_validate(snapshot.payload).model_dump(
            mode="json"
        )
        payload["contribs"] = [
            package
            for package in payload["contribs"]
            if not any(
                entry["entry"].startswith("sidecar") for entry in package["enabled"]
            )
        ]
        await store.write(payload, expected_revision=snapshot.revision)
    await cli(
        "edit",
        str(await position("example-file")),
        "--option",
        "delay=0",
        "--option",
        "label=local",
    )
    await cli("enable", "example", "--entry", "example-consumer")
    protected = await cli("disable", str(await position("example-file")), success=False)
    assert (
        "example-consumer" in protected
        and "example.file" in protected
        and "--force" in protected
    )
    async with running(config):
        assert "started:local" in await asyncio.to_thread(offline.read_text)


if __name__ == "__main__":
    if sys.argv[1] == "--node":
        asyncio.run(serve(Path(sys.argv[2])))
    else:
        asyncio.run(exercise(Path(sys.argv[1]), sys.argv[2]))
        print("Live manager workflow passed")
