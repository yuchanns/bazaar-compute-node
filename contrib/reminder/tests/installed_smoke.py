"""Exercise installed Reminder persistence, real timers and local commands."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import sys
from contextlib import asynccontextmanager, closing
from importlib.metadata import distribution
from pathlib import Path
from tempfile import TemporaryDirectory
from time import time_ns
from typing import Any
from uuid import uuid7

import tomli_w
from bazaar_contrib_sidecar import CLIENT, Client, Construct, Remote, Symbol
from bazaar_core import Context, Contrib, ContribEntry, ContribHub, ContribSpec, load

from bazaar_contrib_reminder.reminder import format_utc_timestamp


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
            if stderr:
                print(stderr.decode(), file=sys.stderr)
            if process.returncode:
                raise RuntimeError(stderr.decode())


def rows(directory: Path, table: str) -> list[dict[str, Any]]:
    with closing(
        sqlite3.connect((directory / "node.sqlite3").as_uri() + "?mode=ro", uri=True)
    ) as database:
        database.row_factory = sqlite3.Row
        return [dict(row) for row in database.execute(f"SELECT * FROM {table}")]


async def received(directory: Path, id: str) -> dict[str, Any]:
    async with asyncio.timeout(15):
        while True:
            for row in await asyncio.to_thread(rows, directory, "messages"):
                if row["message_id"] == id:
                    return row
            await asyncio.sleep(0.01)


async def events(directory: Path, name: str, *, count: int = 1) -> list[dict[str, Any]]:
    async with asyncio.timeout(15):
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


def agent(id: str) -> Construct:
    return Construct("bazaar_contrib_messages:Agent", {"id": id})


def thread(id: str) -> Construct:
    return Construct("bazaar_contrib_messages:Thread", {"id": id})


async def bcc(owner: str, actor_id: str, *args: str) -> str:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "bazaar_contrib_agent.bcc",
        "reminder",
        *args,
        env=dict(os.environ)
        | {
            "BCN_AGENT_ID": owner,
            "BCN_ACTOR_ID": actor_id,
            "BCN_RUNTIME_SESSION_ID": owner + "-session",
            "BCN_COMMAND_CAPABILITY": owner + "-capability",
        },
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    output, errors = await process.communicate()
    if process.returncode:
        raise RuntimeError(errors.decode() + output.decode())
    return output.decode()


async def wake_for(
    directory: Path, reminder_id: str, *, count: int = 1
) -> list[dict[str, Any]]:
    async with asyncio.timeout(15):
        while True:
            content = await asyncio.to_thread((directory / "wakes.jsonl").read_text)
            wakes = [json.loads(line) for line in content.splitlines()]
            found = [
                wake
                for wake in wakes
                if wake["metadata"].get("reminder_id") == reminder_id
            ]
            if len(found) >= count:
                return found
            await asyncio.sleep(0.01)


async def exercise(directory: Path):
    directory = await asyncio.to_thread(directory.resolve)
    installed = distribution("bazaar-contrib-reminder")
    assert any(".dist-info/licenses/" in str(file) for file in installed.files or ())
    endpoint = (
        str(directory / "s") if os.name != "nt" else rf"\\.\pipe\bcn-reminder-{uuid7()}"
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
    await asyncio.to_thread(
        config.write_text,
        tomli_w.dumps(
            {
                "version": "5",
                "contribs": [
                    {"enabled": [{"entry": entry, "config": value}]}
                    for entry, value in (
                        ("commands", {}),
                        ("storage", {}),
                        ("sqlite", {"path": str(directory / "node.sqlite3")}),
                        ("database", {"backend": "sqlite"}),
                        ("schema", {}),
                        ("node-migrations", {}),
                        ("audit", {}),
                        (native, {"endpoint": endpoint}),
                        ("sidecar", {}),
                        ("timer-wheel", {}),
                        ("messages-node", {}),
                        ("reminder", {}),
                        ("reminder-gateway", {"directory": str(directory)}),
                    )
                ],
            }
        ),
    )

    @asynccontextmanager
    async def control():
        clients: list[Client] = []

        @asynccontextmanager
        async def consumer(ctx: Context):
            clients.append(ctx.accept(CLIENT))
            yield

        composition = await asyncio.to_thread(
            load, (ContribSpec(contrib=native, config={"endpoint": endpoint}),)
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
            reminders: Remote = await clients[0].context.call(
                "accept", Symbol("bazaar_contrib_reminder:REMINDER")
            )
            messages: Remote = await clients[0].context.call(
                "accept", Symbol("bazaar_contrib_messages:MESSAGES")
            )
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
                yield reminders, messages, peers
            finally:
                for peer in peers.values():
                    await peer.aclose()

    async def command(
        reminders: Remote, owner: str, actor_id: str, command: str, **values: Any
    ) -> dict[str, Any]:
        response = await reminders.call(
            "request",
            {
                "kind": "command",
                "resource": "reminder",
                "command": command,
                "agent_id": owner,
                "actor_id": actor_id,
                **values,
            },
        )
        assert response["ok"], response
        return response["result"]

    async with node(config), control() as (reminders, messages, peers):
        cloud_anchor = await peers["Cloud"].inbound(
            "活动材料周一就要交了，请到时提醒我确认讲师介绍。"
        )
        cloud_row = await received(directory, cloud_anchor)
        cloud_actor = cloud_row["thread_id"]
        tifa_anchor = await peers["Tifa"].inbound(
            "下周的会议记录还没整理完，需要记得继续跟进。"
        )
        tifa_row = await received(directory, tifa_anchor)
        tifa_group_anchor = await peers["Tifa"].inbound(
            "另一个会场的会议纪要也需要持续跟进。", target="planning", kind="group"
        )
        tifa_group_row = await received(directory, tifa_group_anchor)
        separate_anchor = await peers["Cloud"].inbound(
            "展台物料明天到货，稍后记得确认。", target="logistics"
        )
        separate_row = await received(directory, separate_anchor)
        first = await command(
            reminders,
            "Cloud",
            cloud_actor,
            "schedule",
            title="确认讲师介绍",
            message_id=cloud_anchor,
            delay_seconds=1,
        )
        first_id = first["reminder"]["reminder_id"]
        wakes = await wake_for(directory, first_id)
        assert wakes[-1]["anchor_message_id"] == cloud_anchor
        assert (
            wakes[-1]["thread_id"] == cloud_actor and wakes[-1]["agent_id"] == "Cloud"
        )
        assert "确认讲师介绍" in wakes[-1]["body"]
        state = (await command(reminders, "Cloud", cloud_actor, "list"))["reminders"]
        assert any(
            reminder["reminder_id"] == first_id and reminder["state"] == "fired"
            for reminder in state
        )
        storage = await messages.call("storage", agent("Cloud"))
        system = await storage.call(
            "get_owned_message",
            agent("Cloud"),
            thread(cloud_actor),
            wakes[-1]["message_id"],
        )
        assert await system.get("provider_message_id") is None
        assert await system.get("notifies_runtime")
        assert await system.get("seq") > cloud_row["seq"]
        unread = await messages.call("list_unread_message_owners")
        assert any(
            [
                await (await owner.get("trigger_message")).get("message_id")
                == wakes[-1]["message_id"]
                for owner in unread
            ]
        )
        assert "snoozed" in await bcc(
            "Cloud", cloud_actor, "snooze", "--id", first_id, "--by", "1m"
        )
        await command(
            reminders,
            "Cloud",
            cloud_actor,
            "update",
            reminder_id=first_id,
            fire_at=format_utc_timestamp(time_ns() // 1_000_000 + 1000),
        )
        wakes = await wake_for(directory, first_id, count=2)
        assert wakes[-1]["seq"] > wakes[0]["seq"]
        assert wakes[-1]["anchor_message_id"] == cloud_anchor
        output = await bcc(
            "Cloud",
            cloud_actor,
            "schedule",
            "--title",
            "核对展台清单",
            "--message-id",
            cloud_anchor,
            "--delay-seconds",
            "60",
        )
        match = re.search(r"#([0-9a-f-]{36})", output)
        assert match, output
        canceled_id = match.group(1)
        await bcc(
            "Cloud",
            cloud_actor,
            "update",
            "--id",
            canceled_id,
            "--title",
            "核对最终展台清单",
        )
        await bcc("Cloud", cloud_actor, "update", "--id", canceled_id, "--in", "2m")
        await bcc(
            "Cloud",
            cloud_actor,
            "update",
            "--id",
            canceled_id,
            "--cadence",
            "daily@09:00",
        )
        assert "canceled" in await bcc(
            "Cloud", cloud_actor, "cancel", "--id", canceled_id
        )
        assert "核对最终展台清单" in await bcc("Cloud", cloud_actor, "list", "--all")
        recurring = await command(
            reminders,
            "Tifa",
            "Tifa",
            "schedule",
            title="继续跟进会议记录",
            message_id=tifa_group_anchor,
            repeat_rule="every:1m",
            fire_at=format_utc_timestamp(time_ns() // 1_000_000 + 1000),
            timezone="Asia/Shanghai",
        )
        recurring_id = recurring["reminder"]["reminder_id"]
        recurring_wakes = await wake_for(directory, recurring_id)
        assert recurring_wakes[-1]["thread_id"] == tifa_group_row["thread_id"]
        assert recurring_wakes[-1]["anchor_message_id"] == tifa_group_anchor
        listed = (await command(reminders, "Tifa", "Tifa", "list"))["reminders"]
        repeated = next(
            reminder for reminder in listed if reminder["reminder_id"] == recurring_id
        )
        assert repeated["state"] == "scheduled" and repeated["last_occurrence_no"] > 0
        assert repeated["next_fire_at_ms"] > repeated["last_fired_at_ms"]
        await command(reminders, "Tifa", "Tifa", "cancel", reminder_id=recurring_id)
        own = await command(
            reminders,
            "Cloud",
            separate_row["thread_id"],
            "schedule",
            title="确认到货",
            message_id=separate_anchor,
            delay_seconds=60,
        )
        separate = (
            await command(reminders, "Cloud", separate_row["thread_id"], "list")
        )["reminders"]
        assert all(
            reminder["owner_thread_id"] == separate_row["thread_id"]
            for reminder in separate
        )
        await command(
            reminders,
            "Cloud",
            separate_row["thread_id"],
            "cancel",
            reminder_id=own["reminder"]["reminder_id"],
        )
        offline = await command(
            reminders,
            "Tifa",
            "Tifa",
            "schedule",
            title="恢复后继续整理会议记录",
            message_id=tifa_anchor,
            delay_seconds=4,
        )
        offline_id = offline["reminder"]["reminder_id"]
        before = len(await wake_for(directory, first_id, count=2))
        await events(directory, "reminder.fired", count=3)
    await asyncio.sleep(4.1)
    async with node(config), control() as (reminders, messages, peers):
        recovered = await wake_for(directory, offline_id)
        assert recovered[-1]["anchor_message_id"] == tifa_anchor
        assert recovered[-1]["thread_id"] == tifa_row["thread_id"]
        await wake_for(directory, first_id, count=before + 1)
        listed = (await command(reminders, "Tifa", "Tifa", "list"))["reminders"]
        assert any(
            reminder["reminder_id"] == offline_id and reminder["state"] == "fired"
            for reminder in listed
        )
        reminders_rows = await asyncio.to_thread(rows, directory, "reminders")
        assert any(
            reminder["reminder_id"] == canceled_id and reminder["state"] == "canceled"
            for reminder in reminders_rows
        )
        assert any(
            reminder["reminder_id"] == recurring_id
            and reminder["last_occurrence_no"] > 0
            for reminder in reminders_rows
        )
    print("Reminder workflow passed")


async def main():
    with TemporaryDirectory(
        prefix="bre-", dir="/tmp" if os.name != "nt" else None
    ) as directory:
        async with asyncio.timeout(120):
            await exercise(Path(directory))


if __name__ == "__main__":
    asyncio.run(main())
