"""Control installed collaboration services through a real sidecar and TCP peers."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
from contextlib import asynccontextmanager, closing
from importlib.metadata import distribution
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast
from uuid import uuid7

import tomli_w
from bazaar_contrib_sidecar import CLIENT, Client
from bazaar_core import Context, Contrib, ContribEntry, ContribHub, ContribSpec, load


class Peer:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader = reader
        self.writer = writer
        self.deliveries: list[dict[str, Any]] = []
        self.status = "confirmed"
        self.task = asyncio.create_task(self.receive())

    async def receive(self):
        while data := await self.reader.readline():
            packet = json.loads(data)
            self.deliveries.append(packet)
            self.writer.write(
                json.dumps(
                    {
                        "id": packet["id"],
                        "receipt": {
                            "status": self.status,
                            "provider_message_id": packet["id"],
                            "error": "The channel declined this reply."
                            if self.status == "failed"
                            else None,
                        },
                    }
                ).encode()
                + b"\n"
            )
            await self.writer.drain()

    async def inbound(
        self,
        body: str,
        *,
        id: str | None = None,
        target: str = "peer",
        kind: str = "dm",
        sender: str = "hanchin",
        reply_to: str | None = None,
        notifies_runtime: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        id = id or str(uuid7())
        self.writer.write(
            json.dumps(
                {
                    "inbound": {
                        "id": id,
                        "target": {
                            "kind": kind,
                            "thread_id": target,
                            "reply_to_message_id": reply_to,
                        },
                        "sender": {
                            "id": sender,
                            "name": sender,
                            "display_name": "Hanchin",
                        },
                        "body": body,
                        "notifies_runtime": notifies_runtime,
                        "metadata": metadata or {},
                    }
                }
            ).encode()
            + b"\n"
        )
        await self.writer.drain()
        return id

    async def aclose(self):
        self.writer.close()
        await self.writer.wait_closed()
        await self.task


@asynccontextmanager
async def node(config: Path):
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(Path(__file__).with_name("node.py")),
        str(config),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdin and process.stdout and process.stderr
    try:
        line = await process.stdout.readline()
        if not line:
            raise RuntimeError((await process.stderr.read()).decode())
        yield
    finally:
        if process.returncode is None:
            process.stdin.write(b"stop\n")
            await process.stdin.drain()
            _, stderr = await process.communicate()
            if process.returncode:
                raise RuntimeError(stderr.decode())


def rows(directory: Path, table: str) -> list[dict[str, Any]]:
    with closing(
        sqlite3.connect(
            (directory / ".bcn" / "node.sqlite3").as_uri() + "?mode=ro", uri=True
        )
    ) as database:
        database.row_factory = sqlite3.Row
        return [dict(row) for row in database.execute(f"SELECT * FROM {table}")]


async def received(directory: Path, id: str) -> dict[str, Any]:
    while True:
        for row in await asyncio.to_thread(rows, directory, "messages"):
            if row["message_id"] == id:
                return row
        await asyncio.sleep(0.01)


async def events(directory: Path, name: str, *, count: int = 1) -> list[dict[str, Any]]:
    while True:
        text = await asyncio.to_thread((directory / "audit.jsonl").read_text)
        found = [
            event
            for line in text.splitlines()
            if (event := json.loads(line))["event_name"] == name
        ]
        if len(found) >= count:
            return found
        await asyncio.sleep(0.01)


async def contacts(
    directory: Path, owner: str, state: str | None = None
) -> list[dict[str, Any]]:
    sessions = {
        session["id"]: session
        for session in await asyncio.to_thread(rows, directory, "channel_sessions")
        if session["agent_id"] == owner
    }
    messages = await asyncio.to_thread(rows, directory, "messages")
    found = []
    for thread in await asyncio.to_thread(rows, directory, "threads"):
        session = sessions.get(thread["channel_session_id"])
        if session is None or (state is not None and session["review"] != state):
            continue
        latest = max(
            (message for message in messages if message["thread_id"] == thread["id"]),
            key=lambda message: message["seq"],
        )
        found.append(
            {
                "thread_id": thread["id"],
                "canonical_target": latest["target"],
                "review": session["review"],
                "latest_message_id": latest["message_id"],
                "seq": latest["seq"],
            }
        )
    return sorted(found, key=lambda contact: contact["seq"], reverse=True)


async def command(client: Client, command: str, **values: object) -> dict[str, Any]:
    response = cast(
        dict[str, Any],
        await client.call(
            "messages",
            "request",
            {
                "kind": "command",
                "agent_id": "Cloud",
                "actor_id": "Cloud",
                "resource": "message",
                "command": command,
                **values,
            },
        ),
    )
    assert response["ok"], response
    return response["result"]


async def review(client: Client, thread_id: str, decision: str) -> None:
    await client.call("collaboration", "review_contact", thread_id, decision)


async def exercise(directory: Path):
    directory = await asyncio.to_thread(directory.resolve)
    installed = distribution("bazaar-contrib-collaboration")
    assert any(".dist-info/licenses/" in str(file) for file in installed.files or ())
    endpoint = (
        str(directory / "s")
        if os.name != "nt"
        else rf"\\.\pipe\bcn-collaboration-{uuid7()}"
    )
    os.environ.update(
        BCN_HOME=str(directory),
        BCN_ENDPOINT=endpoint,
        UV_TOOL_DIR=str(directory / "tools"),
        UV_TOOL_BIN_DIR=str(directory / "bin"),
        UV_CACHE_DIR=str(directory / "cache"),
    )
    native = "sidecar-windows" if os.name == "nt" else "sidecar-unix"
    config = directory / "config.toml"
    payload = {
        "version": "5",
        "contribs": [
            {"enabled": [{"entry": entry, "config": value}]}
            for entry, value in (
                ("commands", {}),
                ("storage", {}),
                ("sqlite", {"name": "node.sqlite3"}),
                ("database", {"backend": "sqlite"}),
                ("schema", {}),
                ("legacy-node-migrations", {}),
                ("audit", {}),
                (native, {"endpoint": endpoint}),
                ("sidecar", {}),
                ("timer-wheel", {}),
                ("messages-node", {}),
                ("collaboration", {}),
                ("collaboration-gateway", {"directory": str(directory)}),
            )
        ],
    }
    await asyncio.to_thread(config.write_text, tomli_w.dumps(payload))

    @asynccontextmanager
    async def control():
        clients: list[Client] = []

        @asynccontextmanager
        async def consumer(ctx: Context):
            clients.append(ctx.accept(CLIENT))
            yield

        composition = await asyncio.to_thread(
            load,
            (
                ContribSpec(contrib="timer-wheel"),
                ContribSpec(contrib=native, config={"endpoint": endpoint}),
            ),
        )
        composition = composition.model_copy(
            update={
                "entries": (
                    *composition.entries,
                    ContribEntry(
                        name="control",
                        definition=Contrib(accepts=[CLIENT], lifespan=consumer),
                    ),
                )
            }
        )
        async with ContribHub() as hub, hub.enable(composition):
            peers: dict[str, Peer] = {}
            try:
                for owner in ("Cloud", "Tifa"):
                    address = json.loads(
                        await asyncio.to_thread(
                            (directory / owner / "channel.json").read_text
                        )
                    )
                    peers[owner] = Peer(
                        *await asyncio.open_connection("127.0.0.1", address["port"])
                    )
                yield clients[0], peers
            finally:
                for peer in peers.values():
                    await peer.aclose()

    async with node(config), control() as (client, peers):
        for owner, reply in (
            ("Cloud", "消息已收到，审核通过后继续。"),
            ("Cloud", "已收到申请，审核通过后继续。"),
            ("Tifa", "请稍候，我们会尽快审核。"),
        ):
            await client.call(
                "collaboration", "set_setting", owner, "review.reply", reply
            )
        pending = await peers["Cloud"].inbound("你好，我想请你一起整理下周的活动材料。")
        pending_row = await received(directory, pending)
        assert not pending_row["notifies_runtime"]
        replies = await events(directory, "channel.review.replied")
        assert replies[-1]["payload"]["state"] == "completed"
        assert any(
            "已收到申请" in delivery["outbound"]["body"]
            for delivery in peers["Cloud"].deliveries
        )
        pending_contacts = await contacts(directory, "Cloud", "pending")
        owned = next(
            contact
            for contact in pending_contacts
            if contact["thread_id"] == pending_row["thread_id"]
        )
        other = await peers["Tifa"].inbound("你好，能帮我整理会议记录吗？")
        other_row = await received(directory, other)
        assert not other_row["notifies_runtime"]
        assert other_row["thread_id"] != pending_row["thread_id"]
        await review(client, other_row["thread_id"], "approved")
        other = await peers["Tifa"].inbound("上周会议主要讨论了场地和报名安排。")
        assert (await received(directory, other))["notifies_runtime"]
        group_request = await peers["Cloud"].inbound(
            "@Cloud 下周活动的分工已经整理好了。", target="team", kind="group"
        )
        group_row = await received(directory, group_request)
        assert not group_row["notifies_runtime"]
        await review(client, group_row["thread_id"], "approved")
        allowed = await peers["Cloud"].inbound(
            "@Cloud 可以先看看讲师的时间安排。", target="team", kind="group"
        )
        assert (await received(directory, allowed))["notifies_runtime"]
        trusted = await peers["Cloud"].inbound(
            "沿用已确认的项目联系通道。",
            target="trusted",
            metadata={"review": "approved"},
        )
        assert (await received(directory, trusted))["notifies_runtime"]
        await review(client, owned["thread_id"], "approved")
        followup = await peers["Cloud"].inbound(
            "报名表里还需要补上讲师介绍。", reply_to=pending
        )
        row = await received(directory, followup)
        assert row["notifies_runtime"] and row["reply_to_message_id"] == pending
        assert row["thread_id"] == owned["thread_id"]
        await command(client, "check")
        await command(
            client,
            "send",
            target=owned["canonical_target"],
            body="报名表已补好，我会继续整理讲师介绍。",
            reply_to_message_id=followup,
        )
        sent = max(
            (
                message
                for message in await asyncio.to_thread(rows, directory, "messages")
                if message["direction"] == "outbound"
            ),
            key=lambda message: message["seq"],
        )["message_id"]
        outbound = peers["Cloud"].deliveries[-1]
        quote = await peers["Cloud"].inbound(
            "收到，这版可以发给会务同事了。", reply_to=outbound["id"]
        )
        quoted = await received(directory, quote)
        assert quoted["reply_to_message_id"] == sent
        history = await command(
            client,
            "read",
            target=owned["canonical_target"],
            around_message_id=quote,
            limit=1,
        )
        assert any(
            message["message_id"] == sent for message in history["referenced_messages"]
        )
        await peers["Cloud"].inbound(
            outbound["outbound"]["body"], id=outbound["id"], notifies_runtime=False
        )
        quiet = await peers["Cloud"].inbound(
            "补存早些时候的频道上下文。",
            notifies_runtime=False,
            reply_to=outbound["id"],
        )
        assert (await received(directory, quiet))["reply_to_message_id"] == quoted[
            "reply_to_message_id"
        ]
        history = await asyncio.to_thread(rows, directory, "messages")
        assert (
            len(
                [
                    message
                    for message in history
                    if message["provider_message_id"] == outbound["id"]
                ]
            )
            == 1
        )
        await review(client, owned["thread_id"], "denied")
        peers["Cloud"].status = "failed"
        returned = await peers["Cloud"].inbound("我补齐了活动背景，能再看一下吗？")
        assert not (await received(directory, returned))["notifies_runtime"]
        replies = await events(directory, "channel.review.replied", count=4)
        assert replies[-1]["payload"]["state"] == "failed"
        assert (await contacts(directory, "Cloud", "pending"))[0]["thread_id"] == owned[
            "thread_id"
        ]
        await review(client, owned["thread_id"], "approved")
        group = next(
            contact
            for contact in await contacts(directory, "Cloud", "approved")
            if contact["latest_message_id"] == allowed
        )
        await review(client, group["thread_id"], "denied")
        settings = await events(directory, "setting.changed", count=3)
        assert all(
            event["payload"]["metadata"] == {"key": "review.reply"}
            for event in settings
        )
        reviews = await events(directory, "channel.session.reviewed", count=6)
        assert {event["context"]["agent_id"] for event in reviews} == {"Cloud", "Tifa"}
        notifications = [
            json.loads(line)
            for line in (
                await asyncio.to_thread((directory / "review-events.jsonl").read_text)
            ).splitlines()
        ]
        assert all(
            {
                "agent": event["context"]["agent_id"],
                "thread": event["context"]["thread_id"],
                "review": event["payload"]["metadata"]["review"],
            }
            in notifications
            for event in reviews
        )
        assert (await contacts(directory, "Tifa"))[0]["thread_id"] == other_row[
            "thread_id"
        ]

    async with node(config), control() as (client, peers):
        for owner, text in (("Cloud", "审核通过"), ("Tifa", "请稍候")):
            assert text in cast(
                str,
                await client.call("collaboration", "setting", owner, "review.reply"),
            )
        assert any(
            contact["thread_id"] == owned["thread_id"]
            for contact in await contacts(directory, "Cloud", "approved")
        )
        assert any(
            contact["thread_id"] == group["thread_id"]
            for contact in await contacts(directory, "Cloud", "denied")
        )
        assert any(
            contact["thread_id"] == other_row["thread_id"]
            for contact in await contacts(directory, "Tifa", "approved")
        )
        resumed = await peers["Cloud"].inbound(
            "继续之前的活动材料，今天有了新的议程。", reply_to=quote
        )
        assert (await received(directory, resumed))["reply_to_message_id"] == quote
        newcomer = await peers["Tifa"].inbound(
            "你好，我是刚加入项目的同事。", target="new-peer", sender="barret"
        )
        assert not (await received(directory, newcomer))["notifies_runtime"]
        await events(directory, "channel.review.replied", count=5)
        assert any(
            contact["latest_message_id"] == newcomer
            for contact in await contacts(directory, "Tifa", "pending")
        )
        await peers["Cloud"].inbound(
            "继续之前的活动材料，今天有了新的议程。",
            id=resumed,
            reply_to=quote,
            notifies_runtime=False,
            metadata={"quoted_backfill": True},
        )
        marker = await peers["Cloud"].inbound("活动安排稍后补充。")
        await received(directory, marker)
        history = await asyncio.to_thread(rows, directory, "messages")
        assert (
            len([message for message in history if message["message_id"] == resumed])
            == 1
        )
    print("collaboration workflow passed")


async def main():
    with TemporaryDirectory(
        prefix="bco-", dir="/tmp" if os.name != "nt" else None
    ) as directory:
        await exercise(Path(directory))


if __name__ == "__main__":
    asyncio.run(main())
