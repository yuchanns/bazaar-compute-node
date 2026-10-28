"""Exercise installed configuration commands and real pending conversations."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import signal
import sqlite3
import sys
import tomllib
from contextlib import AsyncExitStack, asynccontextmanager, closing
from pathlib import Path
from typing import Any

from bazaar_contrib_config_filesystem import FileConfigStore
from bazaar_contrib_sidecar import CLIENT, Client
from bazaar_core import (
    Context,
    Contrib,
    ContribEntry,
    ContribHub,
    ContribSpec,
    load,
)
from blockbuster import blockbuster_ctx


async def exercise(path: Path, source: str, tests: Path):
    config = path / "config.toml"
    os.environ["EDITOR_SOURCE"] = "Weekend editor"

    async def cli(*arguments: str):
        print("bcn --config", config, *arguments, flush=True)
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
        try:
            output, errors = await process.communicate()
            print((output + errors).decode(), end="", flush=True)
            assert process.returncode == 0, (output + errors).decode()
            if "--help" not in arguments and (
                arguments[:2]
                in (
                    ("agent", "add"),
                    ("agent", "edit"),
                    ("agent", "remove"),
                )
                or arguments[:3]
                in (
                    ("agent", "review", "approve"),
                    ("agent", "review", "deny"),
                )
            ):
                assert not output, output.decode()
                assert errors.strip()
            return output.decode()
        finally:
            if process.returncode is None:
                process.terminate()
                await process.wait()

    async def document():
        return tomllib.loads(await asyncio.to_thread(config.read_text))

    async def agents():
        payload = await document()
        return next(
            entry["config"]["agents"]
            for package in payload["contribs"]
            for entry in package["enabled"]
            if entry["entry"] == "agent"
        )

    await cli("contrib", "configured")
    payload = await document()
    index = next(
        index
        for index, entry in enumerate(
            entry for package in payload["contribs"] for entry in package["enabled"]
        )
        if entry["entry"] == "contrib-manager"
    )
    await cli("contrib", "edit", str(index), "--option", f"host_source={source}")
    # The default composition already offers the agent commands.
    assert "No agents" in await cli("agent", "list")
    await cli(
        "contrib",
        "add",
        "bcn-agent-workbench",
        "--source",
        source,
        "--enable",
        "test-channel-builder",
    )
    await cli("contrib", "enable", "bcn-agent-workbench", "--entry", "document-builder")
    await cli(
        "contrib",
        "enable",
        "bcn-agent-workbench",
        "--entry",
        "secondary-channel-builder",
    )
    await cli("contrib", "add", "bcn-agent-journal", "--source", source)
    for command in (
        ("agent",),
        ("agent", "list"),
        ("agent", "add"),
        ("agent", "edit"),
        ("agent", "remove"),
        ("agent", "review"),
        ("agent", "review", "list"),
        ("agent", "review", "approve"),
        ("agent", "review", "deny"),
    ):
        output = await cli(*command, "--help")
        await asyncio.to_thread(
            (path / f"{'-'.join(command)}-help.txt").write_text, output
        )
    await asyncio.to_thread(print, "Installed and enabled real providers", flush=True)

    for name, mode in (("Cloud", "session"), ("Tifa", "dangerous_individual")):
        providers = (
            (
                "--channel",
                "test-channel-secondary",
                "--set",
                "channel.1.endpoint_file=secondary.json",
            )
            if name == "Cloud"
            else ()
        )
        await cli(
            "agent",
            "add",
            "--name",
            name,
            "--channel",
            "test-channel",
            *providers,
            "--harness",
            "document",
            "--set",
            f"agent.workspace={path / name}",
            "--set",
            f"agent.mode={mode}",
            "--set",
            f"harness.title={name} editorial report",
            "--set",
            "harness.env=EDITOR_NAME=EDITOR_SOURCE",
        )
    initial = await agents()
    cloud, tifa = initial
    await cli(
        "agent",
        "edit",
        cloud["id"],
        "--name",
        "Cloud desk",
        "--channel",
        "test-channel-secondary",
        "--channel",
        "test-channel",
        "--set",
        "agent.idle_timeout=30",
        "--set",
        "agent.session.config.sandbox.policy=workspace-write",
        "--set",
        "harness.publication_delay=0.01",
    )
    updated = await agents()
    assert (
        updated[0]["id"] == cloud["id"]
        and updated[0]["harness"][0]["title"] == cloud["harness"][0]["title"]
    )
    assert updated[0]["channel"] == cloud["channel"][::-1] and updated[1] == tifa
    assert updated[0]["harness"][0]["publication_delay"] == 0.01
    assert updated[0]["harness"][0]["env"] == {"EDITOR_NAME": "EDITOR_SOURCE"}
    listing = await cli("agent", "list")
    assert "Cloud desk" in listing and "Tifa" in listing
    assert "offline" in listing
    assert not await asyncio.to_thread((path / "Cloud").exists)
    assert not await asyncio.to_thread((path / "Tifa").exists)
    await asyncio.to_thread(
        print,
        "Offline add/list/edit preserved identity and provider settings",
        flush=True,
    )

    from bazaar_contrib_channel import ChannelTarget, SenderIdentity

    sys.path.insert(0, str(tests))
    provider = await asyncio.to_thread(importlib.import_module, "provider_smoke")
    Peer, finish, ChannelMessage = (
        provider.Peer,
        provider.finish,
        provider.InboundMessage,
    )

    endpoint = str(path / ".bcn" / "sidecar.sock")
    if os.name == "nt":
        payload = await document()
        endpoint = rf"\\.\pipe\bcn-task17-{cloud['id']}"
        async with FileConfigStore(config) as store:
            snapshot = await store.read()
            assert snapshot is not None
            for package in payload["contribs"]:
                for entry in package["enabled"]:
                    if entry["entry"] == "sidecar-windows":
                        entry["config"] = {"endpoint": endpoint}
            await store.write(payload, expected_revision=snapshot.revision)

    os.environ["BCN_ENDPOINT"] = endpoint

    @asynccontextmanager
    async def control():
        clients: list[Client] = []

        @asynccontextmanager
        async def consumer(ctx: Context):
            clients.append(ctx.accept(CLIENT))
            yield

        composition = await asyncio.to_thread(
            load,
            [
                ContribSpec(contrib="timer-wheel"),
                ContribSpec(
                    contrib="sidecar-windows" if os.name == "nt" else "sidecar-unix",
                    config={"endpoint": endpoint},
                ),
            ],
        )
        composition = composition.model_copy(
            update={
                "entries": (
                    *composition.entries,
                    ContribEntry(
                        name="review-control",
                        definition=Contrib(accepts=[CLIENT], lifespan=consumer),
                    ),
                )
            }
        )
        async with ContribHub() as hub, hub.enable(composition):
            yield clients[0]

    def select(query: str, *args: object) -> list[dict[str, Any]]:
        database = path / ".bcn" / "bcn.sqlite3"
        with closing(
            sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
        ) as connection:
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute(query, args)]

    async def contacts(owner: str, state: str | None = None) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            select,
            "SELECT thread.id AS thread_id, session.review AS review "
            "FROM threads AS thread JOIN channel_sessions AS session "
            "ON session.id = thread.channel_session_id "
            "WHERE session.agent_id = ? AND (? IS NULL OR session.review = ?) "
            "ORDER BY thread.created_at_ms",
            owner,
            state,
            state,
        )

    async def notifies_runtime(message_id: str) -> int | None:
        found = await asyncio.to_thread(
            select,
            "SELECT notifies_runtime FROM messages WHERE provider_message_id = ?",
            message_id,
        )
        return found[0]["notifies_runtime"] if found else None

    async def restarted(name: str, before: int) -> None:
        endpoint = path / name / "channel.json"
        while True:
            if (await asyncio.to_thread(endpoint.stat)).st_mtime_ns != before:
                return
            await asyncio.sleep(0.01)

    async def pending(owner: str):
        while True:
            rows = await contacts(owner, "pending")
            if rows:
                return rows[-1]
            await asyncio.sleep(0.01)

    @asynccontextmanager
    async def running():
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(Path(__file__).with_name("management_node.py")),
            str(config),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            assert process.stdout is not None
            if (await process.stdout.readline()).rstrip(b"\r\n") != b"ready":
                _, errors = await process.communicate()
                raise RuntimeError(errors.decode())
            yield process
        finally:
            if process.returncode is None:
                assert process.stdin is not None
                process.stdin.write(b"stop\n")
                await process.stdin.drain()
            _, errors = await process.communicate()
            assert process.returncode == 0, errors.decode()

    async def connect(name: str):
        address = json.loads(
            await asyncio.to_thread((path / name / "channel.json").read_text)
        )
        reader, writer = await asyncio.open_connection("127.0.0.1", address["port"])
        return Peer(reader, writer)

    request = ChannelMessage(
        id="weekend-original",
        target=ChannelTarget(kind="group", thread_id="weekend-edition"),
        sender=SenderIdentity(id="editor", name="Editor", kind="human"),
        body="The weekend edition is nearly ready. Could you prepare a short word-count report for the editor?",
        mentions_agent=True,
    )
    async with running(), AsyncExitStack() as stack:
        peer = await connect("Cloud")
        stack.push_async_callback(peer.aclose)
        assert [item["id"] for item in await agents()] == [cloud["id"], tifa["id"]]
        assert "running" in await cli("agent", "list")
        await peer.write({"inbound": request.model_dump(mode="json")})
        row = await pending(cloud["id"])
        assert row["thread_id"] in await cli("agent", "review", "list", cloud["id"])
        assert not await contacts(tifa["id"], "pending")
    await asyncio.to_thread(
        print, "Pending request persisted before node restart", flush=True
    )

    async with running(), AsyncExitStack() as stack:
        cloud_peer = await connect("Cloud")
        tifa_peer = await connect("Tifa")
        tifa_peer.automatic = True
        stack.push_async_callback(cloud_peer.aclose)
        stack.push_async_callback(tifa_peer.aclose)
        assert row["thread_id"] in await cli("agent", "review", "list", cloud["id"])
        await cli("agent", "review", "approve", row["thread_id"])
        report = await finish(cloud_peer)
        data = json.loads(await asyncio.to_thread(report.read_text))
        assert request.body in data["inbox"] and data["words"] > 0
        assert data["editor"] == "Weekend editor"
        assert data["context"]["actor_id"] == row["thread_id"]
        assert (await contacts(cloud["id"]))[0]["review"] == "approved"
        await asyncio.to_thread(
            print, "CLI approval after restart resumes the original request", flush=True
        )

        shared = request.model_copy(
            update={
                "id": "shared-original",
                "body": "I need a short summary of this draft before the shared editorial desk closes.",
            }
        )
        await tifa_peer.write({"inbound": shared.model_dump(mode="json")})
        shared_row = await pending(tifa["id"])
        async with control() as client:
            await client.call(
                "collaboration", "review_contact", shared_row["thread_id"], "approved"
            )
        shared_report = await finish(tifa_peer)
        data = json.loads(await asyncio.to_thread(shared_report.read_text))
        assert (
            shared.body in data["inbox"] and data["context"]["actor_id"] == tifa["id"]
        )
        await asyncio.to_thread(
            print,
            "Public approval resumes the original request in shared Agent mode",
            flush=True,
        )

        denied = request.model_copy(
            update={
                "id": "denied-original",
                "target": ChannelTarget(kind="group", thread_id="guest-edition"),
                "body": "Could you review the guest edition before I hand it to the editor?",
            }
        )
        await cloud_peer.write({"inbound": denied.model_dump(mode="json")})
        denied_row = await pending(cloud["id"])
        await cli("agent", "review", "deny", denied_row["thread_id"])
        assert any(
            item["thread_id"] == denied_row["thread_id"]
            for item in await contacts(cloud["id"], "denied")
        )
        again = denied.model_copy(
            update={
                "id": "guest-followup",
                "body": "The guest edition has been revised. Could you prepare the editorial report now?",
            }
        )
        await cloud_peer.write({"inbound": again.model_dump(mode="json")})
        await pending(cloud["id"])
        quiet = again.model_copy(
            update={
                "id": "guest-quoted-context",
                "body": "The editor's earlier note is included here as background for the revised edition.",
                "notifies_runtime": False,
            }
        )
        await cloud_peer.write({"inbound": quiet.model_dump(mode="json")})
        while True:
            if await notifies_runtime(quiet.id) is not None:
                break
            await asyncio.sleep(0.01)
        await cli("agent", "review", "approve", denied_row["thread_id"])
        assert not await notifies_runtime(quiet.id)
        renewed = await finish(cloud_peer)
        assert (
            again.body
            in json.loads(await asyncio.to_thread(renewed.read_text))["inbox"]
        )
        for saved in (report, renewed):
            while True:
                if await asyncio.to_thread(saved.with_suffix(".published.json").exists):
                    break
                await asyncio.sleep(0.01)
    async with running(), AsyncExitStack() as stack:
        assert all(item["review"] == "approved" for item in await contacts(cloud["id"]))
        await cli(
            "agent",
            "add",
            "--name",
            "Live desk",
            "--channel",
            "test-channel",
            "--harness",
            "document",
            "--set",
            f"agent.workspace={path / 'Live'}",
        )
        live = (await agents())[-1]
        endpoint = path / "Live" / "channel.json"
        while True:
            if await asyncio.to_thread(endpoint.exists):
                break
            await asyncio.sleep(0.01)
        before = (await asyncio.to_thread(endpoint.stat)).st_mtime_ns
        await cli(
            "agent",
            "edit",
            live["id"],
            "--name",
            "Updated live desk",
            "--set",
            "harness.title=Updated live report",
            "--set",
            "agent.session.config.sandbox.policy=danger-full-access",
        )
        await restarted("Live", before)
        peer = await connect("Live")
        peer.automatic = True
        stack.push_async_callback(peer.aclose)
        message = request.model_copy(
            update={
                "id": "live-edition",
                "body": "The live desk has the revised draft. Could you prepare its editorial report?",
            }
        )
        await peer.write({"inbound": message.model_dump(mode="json")})
        live_row = await pending(live["id"])
        await cli("agent", "review", "approve", live_row["thread_id"])
        saved = await finish(peer)
        report = json.loads(await asyncio.to_thread(saved.read_text))
        assert report["title"] == "Updated live report"
        assert message.body in report["inbox"]
        await cli("agent", "remove", live["id"])
        assert all(item["id"] != live["id"] for item in await agents())
        assert await asyncio.to_thread((path / "Live").is_dir)
        await asyncio.to_thread(
            print,
            "Live CLI add/edit/remove apply without restarting the node",
            flush=True,
        )
    await asyncio.to_thread(
        print,
        "Denied conversations follow the existing admission and persistence rules",
        flush=True,
    )

    await cli("agent", "remove", cloud["id"])
    assert await asyncio.to_thread((path / "Cloud").is_dir)
    assert [item["id"] for item in await agents()] == [tifa["id"]]
    await cli("agent", "remove", "Tifa")
    assert not await agents()

    if os.name != "nt":
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "bazaar_compute_node",
            "--config",
            str(config),
            "run",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            assert process.stdout is not None
            while (line := await process.stdout.readline()) != b"bcn ready\n":
                assert line, "bcn run ended before it was ready"
        finally:
            if process.returncode is None:
                process.send_signal(signal.SIGTERM)
            _, errors = await process.communicate()
            assert process.returncode == 0, errors.decode()
    await asyncio.to_thread(print, "Agent management workflow passed", flush=True)


if __name__ == "__main__":
    with blockbuster_ctx():
        asyncio.run(exercise(Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])))
