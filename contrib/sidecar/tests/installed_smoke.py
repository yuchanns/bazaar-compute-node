"""Exercise installed consumers against a separate real node process."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

import tomli_w
from bazaar_contrib_config_versions import Converter
from bazaar_core import (
    Context,
    Contrib,
    ContribEntry,
    ContribHub,
    ContribSpec,
    load,
)

from bazaar_contrib_sidecar import CLIENT, Client, Construct, Symbol
from bazaar_contrib_sidecar.wire import Request


async def command(config: Path, *args: str) -> str:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "bazaar_compute_node",
        "--config",
        str(config),
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    output, errors = await process.communicate()
    if process.returncode:
        raise RuntimeError(errors.decode() + output.decode())
    return output.decode()


@asynccontextmanager
async def node(config: Path):
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "bazaar_sidecar_example.node",
        str(config),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdin and process.stdout and process.stderr
    try:
        ready = await process.stdout.readline()
        if not ready:
            errors = await process.stderr.read()
            await process.wait()
            raise RuntimeError(errors.decode())
        yield process
    finally:
        if process.returncode is None:
            process.stdin.write(b"stop\n")
            await process.stdin.drain()
            try:
                _, errors = await asyncio.wait_for(process.communicate(), 15)
            except TimeoutError:
                process.kill()
                await process.wait()
                raise
            if process.returncode:
                raise RuntimeError(errors.decode())


async def exercise(directory: Path, transport: str, control: str):
    os.environ["BCN_HOME"] = str(directory)
    native = "sidecar-windows" if sys.platform == "win32" else "sidecar-unix"
    provider = native if transport == "native" else "sidecar-tcp"
    if transport == "native":
        converted = await asyncio.to_thread(
            Converter("5", Converter("4")).convert,
            {
                "version": "4",
                "node": {"endpoint": str(directory / "endpoint"), "control": control},
            },
        )
        entries = [
            entry
            for entry in converted.payload["contribs"]
            if entry["enabled"][0]["entry"] in {"sidecar", native}
        ]
        endpoint = next(
            entry["enabled"][0]["config"]["endpoint"]
            for entry in entries
            if entry["enabled"][0]["entry"] == native
        )
    else:
        endpoint = str(directory / "endpoint")
        entries = [
            {"enabled": [{"entry": provider, "config": {"endpoint": endpoint}}]},
            {"enabled": [{"entry": "sidecar"}]},
        ]
    journal = directory / "journal.txt"
    child = directory / "child.txt"
    abandoned = directory / "abandoned.txt"
    config = directory / "config.toml"
    payload = {
        "version": "5",
        "contribs": [
            {"enabled": [{"entry": "commands"}]},
            *entries,
            {"enabled": [{"entry": "sidecar-controls"}]},
            {
                "enabled": [
                    {
                        "entry": "sidecar-journal",
                        "config": {"path": str(journal), "tag": "before"},
                    }
                ]
            },
        ],
    }
    await asyncio.to_thread(config.write_text, tomli_w.dumps(payload))
    clients: list[Client] = []

    @asynccontextmanager
    async def consumer(ctx: Context):
        clients.append(ctx.accept(CLIENT))
        yield

    entries = await asyncio.to_thread(
        load, [ContribSpec(contrib=provider, config={"endpoint": endpoint})]
    )
    entries = entries.model_copy(
        update={
            "entries": (
                *entries.entries,
                ContribEntry(
                    name="client-consumer",
                    definition=Contrib(accepts=[CLIENT], lifespan=consumer),
                ),
            )
        }
    )
    async with node(config):
        # The consumer graph starts only the provider, while the other process serves handlers.
        async with ContribHub() as hub, hub.enable(entries):
            remote = clients[0].context
            for _ in range(16):
                assert "sidecar" in await remote.get("name")
            await command(config, "remote", "append", "cross-process note 🌙")
            await remote.call(
                "announce", Symbol("bazaar_sidecar_example:NOTE"), "x" * 300_000
            )
            text = await asyncio.to_thread(journal.read_text)
            assert "cross-process note 🌙" in text and "x" * 300_000 in text
            message = "x" * 3_000_000 + "完整的长消息 🌙"
            configuration = await remote.call(
                "accept", Symbol("bazaar_contrib_config:CONFIGURATION")
            )
            _, snapshot = await configuration.call("read")
            payload["contribs"][-1]["enabled"][0]["config"]["tag"] = message
            snapshot = await configuration.call(
                "commit", payload, expected_revision=await snapshot.get("revision")
            )
            _, snapshot = await configuration.call("read")
            saved = await snapshot.get("payload")
            assert message in saved["contribs"][-1]["enabled"][0]["config"]["tag"]
            payload["contribs"][-1]["enabled"][0]["config"]["tag"] = "before"
            snapshot = await configuration.call(
                "commit", payload, expected_revision=await snapshot.get("revision")
            )
            await snapshot.release()
            await configuration.release()

            for path in (child, abandoned):
                candidate = await remote.call(
                    "validate",
                    Construct(
                        "bazaar_core:Composition",
                        {
                            "entries": [
                                Construct(
                                    "bazaar_core:ContribEntry",
                                    {
                                        "name": path.stem,
                                        "definition": Symbol(
                                            "bazaar_sidecar_example:journal"
                                        ),
                                        "config": {"path": str(path), "tag": path.stem},
                                    },
                                )
                            ]
                        },
                    ),
                    local=[Symbol("bazaar_sidecar_example:JOURNAL")],
                )
                scope = await remote.call("enable", candidate)
                if path == child:
                    async with scope.enter():
                        await remote.call(
                            "announce",
                            Symbol("bazaar_sidecar_example:NOTE"),
                            "owned child",
                        )
                    assert "child stopped" in await asyncio.to_thread(child.read_text)
                    await scope.release()
                else:
                    # Intentionally leave a real entered child to connection-owned cleanup.
                    await clients[0].request(
                        Request(operation="enter", target=scope.id)
                    )
                await candidate.release()

            payload["contribs"][-1]["enabled"][0]["config"]["tag"] = "after"
            await asyncio.to_thread(config.write_text, tomli_w.dumps(payload))
            result = json.loads(await command(config, "remote", "refresh"))
            assert result["applied"], result
            snapshots = await remote.get("snapshots")
            names = {await record.get("name") for record in snapshots}
            assert "sidecar-journal" in names

        async with asyncio.timeout(10):
            while "abandoned stopped" not in await asyncio.to_thread(  # noqa: ASYNC110 - The signal is a file in another process.
                abandoned.read_text
            ):
                await asyncio.sleep(0.01)
        text = await asyncio.to_thread(journal.read_text)
        assert "before stopped" in text and "after started" in text

    # A graceful restart reuses the native address and the saved configuration.
    async with node(config):
        await command(config, "remote", "append", "after restart")
    assert "after restart" in await asyncio.to_thread(journal.read_text)

    # The journal still runs as an ordinary local plugin without either sidecar entry.
    payload["contribs"] = [payload["contribs"][-1]]
    await asyncio.to_thread(config.write_text, tomli_w.dumps(payload))
    async with node(config):
        pass
    assert "after stopped" in await asyncio.to_thread(journal.read_text)
    print(
        f"Installed {provider} with V4 control={control}: remote consumers, hot apply, child scopes and restart passed"
    )


if __name__ == "__main__":
    with TemporaryDirectory(
        prefix="bcn-sidecar-", dir="/tmp" if os.name == "posix" else None
    ) as directory:
        asyncio.run(exercise(Path(directory), sys.argv[1], sys.argv[2]))
