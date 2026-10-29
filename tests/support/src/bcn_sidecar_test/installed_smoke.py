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
from bazaar_contrib_sidecar import CLIENT, Client
from bazaar_core import (
    Context,
    Contrib,
    ContribEntry,
    ContribHub,
    ContribSpec,
    load,
)


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
                _, errors = await process.communicate()
            except BaseException:
                if process.returncode is None:
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
    config = directory / "config.toml"
    payload = {
        "version": "5",
        "contribs": [
            {"enabled": [{"entry": "commands"}]},
            {"enabled": [{"entry": "timer-wheel"}]},
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
        load,
        [
            ContribSpec(contrib="timer-wheel"),
            ContribSpec(contrib=provider, config={"endpoint": endpoint}),
        ],
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
            client = clients[0]
            for _ in range(16):
                assert await client.call("journal", "tag") == "before"
            await client.call("journal", "write", "direct cross-process service\n")
            await command(config, "remote", "append", "cross-process note 🌙")
            await client.call("journal", "append", "x" * 300_000)
            text = await asyncio.to_thread(journal.read_text)
            assert "direct cross-process service" in text
            assert "cross-process note 🌙" in text and "x" * 300_000 in text
            for timeout in (None, 0.2):
                waiting = asyncio.create_task(
                    client.call("journal", "wait", 60_000, timeout=timeout)
                )
                await client.call("journal", "wait", 50)
                if timeout is None:
                    waiting.cancel()
                result = await asyncio.gather(waiting, return_exceptions=True)
                assert isinstance(result[0], (asyncio.CancelledError, TimeoutError))
                while await asyncio.to_thread(journal.read_text) == text:  # noqa: ASYNC110 - The signal is a file in another process.
                    await asyncio.sleep(0.01)
                text = await asyncio.to_thread(journal.read_text)
                assert text.endswith("wait 60000 cancelled\n")
            message = "x" * 3_000_000 + "完整的长消息 🌙"
            _, snapshot = await client.call("configuration", "read")
            payload["contribs"][-1]["enabled"][0]["config"]["tag"] = message
            snapshot = await client.call(
                "configuration",
                "commit",
                payload,
                expected_revision=snapshot["revision"],
            )
            _, snapshot = await client.call("configuration", "read")
            saved = snapshot["payload"]
            assert message in saved["contribs"][-1]["enabled"][0]["config"]["tag"]
            payload["contribs"][-1]["enabled"][0]["config"]["tag"] = "before"
            await client.call(
                "configuration",
                "commit",
                payload,
                expected_revision=snapshot["revision"],
            )

            payload["contribs"][-1]["enabled"][0]["config"]["tag"] = "after"
            await asyncio.to_thread(config.write_text, tomli_w.dumps(payload))
            result = json.loads(await command(config, "remote", "refresh"))
            assert result["applied"], result
            assert await client.call("journal", "tag") == "after"
            await client.call("journal", "write", "service after refresh\n")

        text = await asyncio.to_thread(journal.read_text)
        assert "before stopped" in text and "after started" in text
        assert "service after refresh" in text

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
        f"Installed {provider} with V4 control={control}: remote entries, hot apply and restart passed"
    )


if __name__ == "__main__":
    with TemporaryDirectory(
        prefix="bcn-sidecar-", dir="/tmp" if os.name == "posix" else None
    ) as directory:
        asyncio.run(exercise(Path(directory), sys.argv[1], sys.argv[2]))
