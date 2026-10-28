"""Recover a persisted unread request in a second genuine node process."""

import asyncio
import json
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from uuid import uuid7

from bazaar_contrib_channel import ChannelTarget, SenderIdentity
from bcn_agent_workbench import ChannelMessage as InboundMessage
from provider_smoke import Peer, finish


def sessions(database: Path, agent_id: str, *, forget: bool = False):
    with closing(sqlite3.connect(database)) as connection:
        if forget:
            # Conversations written before bots were told apart carry no identity.
            connection.execute(
                "UPDATE channel_sessions SET channel_identity = NULL WHERE agent_id = ?",
                (agent_id,),
            )
            connection.commit()
        return connection.execute(
            "SELECT id, provider_thread_id, channel_identity FROM channel_sessions "
            "WHERE agent_id = ? ORDER BY id",
            (agent_id,),
        ).fetchall()


async def exercise(path: Path):
    await asyncio.to_thread(path.mkdir)
    endpoint = (
        str(path / "sidecar.sock")
        if os.name != "nt"
        else r"\\.\pipe\bcn-recovery-" + str(uuid7())
    )
    config = path / "node.json"
    agent = {
        "id": "Recovery",
        "name": "Recovery",
        "workspace": str(path),
        "channel": [{"kind": "test-channel"}],
        # The first node cannot start a session, so the request stays unread.
        "harness": [{"kind": "document", "env": {"MISSING": "BCN_RECOVERY_MISSING"}}],
    }
    contribs = [
        {"contrib": "audit"},
        {"contrib": "storage"},
        {"contrib": "sqlite", "config": {"path": str(path / "messages.sqlite3")}},
        {"contrib": "database", "config": {"backend": "sqlite"}},
        *(
            {"contrib": name}
            for name in (
                "schema",
                "legacy-node-migrations",
                "timer-wheel",
                "messages-node",
                "collaboration",
                "reminder",
                "channel-builders",
                "harness-builders",
                "test-channel-builder",
                "document-builder",
                "agent-execution",
            )
        ),
        {"contrib": "sidecar"},
        {
            "contrib": "sidecar-windows" if os.name == "nt" else "sidecar-unix",
            "config": {"endpoint": endpoint},
        },
        {"contrib": "agent", "config": {"agents": [agent]}},
    ]

    async def start():
        await asyncio.to_thread(config.write_text, json.dumps({"contribs": contribs}))
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(Path(__file__).with_name("recovery_node.py")),
            str(config),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert process.stdout is not None
        data = await process.stdout.readline()
        if not data:
            assert process.stderr is not None
            raise RuntimeError((await process.stderr.read()).decode())
        assert json.loads(data)["ready"]
        address = json.loads(await asyncio.to_thread((path / "channel.json").read_text))
        reader, writer = await asyncio.open_connection("127.0.0.1", address["port"])
        return process, Peer(reader, writer)

    async def stop(process, peer):
        await peer.aclose()
        if process.returncode is None:
            assert process.stdin is not None
            process.stdin.write(b"stop\n")
            await process.stdin.drain()
        _, error = await process.communicate()
        assert process.returncode == 0, error.decode()

    process, peer = await start()
    message = InboundMessage(
        id=str(uuid7()),
        target=ChannelTarget(kind="group", thread_id="weekend-edition"),
        sender=SenderIdentity(id="editor", name="Editor"),
        body="The weekend edition needs a short report before I send the draft for review.",
        mentions_agent=True,
        metadata={"review": "approved"},
    )
    try:
        await peer.write({"inbound": message.model_dump(mode="json")})
        assert process.stdout is not None
        assert json.loads(await process.stdout.readline())["persisted"]
    finally:
        await stop(process, peer)
    database = path / "messages.sqlite3"
    before = await asyncio.to_thread(sessions, database, agent["id"], forget=True)
    agent["harness"] = [{"kind": "document"}]
    process, peer = await start()
    try:
        pending = await peer.until("approval")
        saved = await finish(peer, pending)
        data = json.loads(await asyncio.to_thread(saved.read_text))
        assert message.body in data["inbox"] and data["words"] > 0
    finally:
        await stop(process, peer)
    after = await asyncio.to_thread(sessions, database, agent["id"])
    assert [row[:2] for row in after] == [row[:2] for row in before]
    assert all(row[2] for row in after)
    assert await asyncio.to_thread((saved.parent / "session.closed").read_text)
