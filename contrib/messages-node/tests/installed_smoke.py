"""Use bcc and public RPC against a separate node and TCP TestChannel peers."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import tomllib
from contextlib import asynccontextmanager, closing
from importlib.metadata import distribution
from importlib.resources import files
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from typing import Any, cast
from uuid import uuid7

import tomli_w
from bazaar_compute_node.boot import build
from bazaar_compute_node.boot import node as application
from bazaar_contrib_agent.tool import Tool
from bazaar_contrib_config import CONFIGURATION, ConfigDocument, Configuration
from bazaar_contrib_sidecar import CLIENT, Client
from bazaar_contrib_timer import TIMER, Timer
from bazaar_core import (
    Composition,
    Context,
    Contrib,
    ContribEntry,
)


class Peer:
    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        tick_ms: int,
    ):
        self.reader = reader
        self.writer = writer
        self.tick_ms = tick_ms
        self.deliveries: list[dict[str, Any]] = []
        self.retries: dict[str, float] = {}

    async def write(self, packet: dict[str, Any]):
        self.writer.write(json.dumps(packet).encode() + b"\n")
        await self.writer.drain()

    async def inbound(
        self,
        body: str,
        *,
        sender: str = "hanchin",
        sender_id: str | None = None,
        kind: str = "dm",
        target: str = "peer",
        reply_to: str | None = None,
        attachments: tuple[Path, ...] = (),
    ):
        id = str(uuid7())
        await self.write(
            {
                "inbound": {
                    "id": id,
                    "target": {
                        "kind": kind,
                        "thread_id": target,
                        "reply_to_message_id": reply_to,
                    },
                    "sender": {"id": sender_id or sender, "name": sender},
                    "body": body,
                    "attachments": [
                        {"name": path.name, "path": str(path)} for path in attachments
                    ],
                }
            }
        )
        return id

    async def delivered(
        self, *, status: str = "confirmed", backpressure: bool = False
    ) -> dict[str, Any]:
        outbound: dict[str, Any] | None = None
        while True:
            packet = json.loads(await self.reader.readline())
            id = packet["operation_id"]
            if backpressure and packet["part"]["index"] > 0 and id not in self.retries:
                self.retries[id] = monotonic()
                await self.write(
                    {
                        "id": packet["id"],
                        "error": {
                            "kind": "channel_rate_limit",
                            "message": "The recipient is busy; retry this part.",
                            "phase": "send",
                            "delivery": "not_sent",
                            "retryable": True,
                            "retry_after": 0.05,
                            "code": "recipient_busy",
                        },
                    }
                )
                continue
            if id in self.retries:
                assert monotonic() - self.retries[id] + self.tick_ms / 1000 >= 0.05
            await self.acknowledge(packet, status=status)
            if outbound is None:
                outbound = {
                    **packet["outbound"],
                    "attachments": list(packet["outbound"]["attachments"]),
                }
            else:
                outbound["body"] += packet["outbound"]["body"]
                outbound["attachments"].extend(packet["outbound"]["attachments"])
            if (
                status == "partial"
                or packet["part"]["index"] + 1 == packet["part"]["count"]
            ):
                assert outbound is not None
                return outbound

    async def acknowledge(self, packet: dict[str, Any], *, status: str = "confirmed"):
        receipt = {
            "status": status,
            "provider_message_id": packet["id"],
            "provider_receipt_ref": "receipt:" + packet["id"],
            "provider_thread_id": packet["outbound"]["target"]["thread_id"],
            "receipt": {"provider": "tcp-test-channel", "accepted": True},
        }
        if status == "partial":
            receipt["error"] = (
                "The peer accepted the text; its attachment is still pending."
            )
        await self.write({"id": packet["id"], "receipt": receipt})
        self.deliveries.append(packet)

    async def aclose(self):
        self.writer.close()
        await self.writer.wait_closed()


def stored_messages(directory: Path) -> dict[str, dict[str, Any]]:
    with closing(
        sqlite3.connect(
            (directory / ".bcn" / "bcn.sqlite3").as_uri() + "?mode=ro", uri=True
        )
    ) as connection:
        connection.row_factory = sqlite3.Row
        return {
            row["provider_message_id"]: dict(row)
            for row in connection.execute(
                "SELECT * FROM messages WHERE agent_id = ? AND direction = ?",
                ("Cloud", "outbound"),
            )
        }


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


async def bcc(
    directory: Path, agent: str, *args: str, body: str = "", success: bool = True
) -> str:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "bazaar_contrib_agent.bcc",
        *args,
        env=dict(os.environ)
        | {
            "BCN_AGENT_ID": agent,
            "BCN_AGENT_MODE": "dangerous_individual",
            "BCN_ACTOR_ID": agent,
            "BCN_RUNTIME_SESSION_ID": agent + "-session",
            "BCN_COMMAND_CAPABILITY": agent + "-capability",
        },
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    output, errors = await process.communicate(body.encode())
    if success and process.returncode:
        raise RuntimeError(errors.decode() + output.decode())
    return output.decode() + errors.decode()


async def exercise(directory: Path):
    directory = await asyncio.to_thread(directory.resolve)
    installed = distribution("bazaar-contrib-messages-node")
    assert any(".dist-info/licenses/" in str(file) for file in installed.files or ())
    os.environ["BCN_HOME"] = str(directory)
    config = directory / "config.toml"
    payload = tomllib.loads(
        await asyncio.to_thread(
            files("bazaar_compute_node").joinpath("defaults.toml").read_text
        )
    )
    payload["contribs"].append(
        {
            "enabled": [
                {"entry": "messages-gateway", "config": {"directory": str(directory)}}
            ]
        }
    )
    await asyncio.to_thread(config.write_text, tomli_w.dumps(payload))

    clients: list[Client] = []
    configurations: list[Configuration] = []
    timers: list[Timer] = []

    @asynccontextmanager
    async def consumer(ctx: Context):
        clients.append(ctx.accept(CLIENT))
        configurations.append(ctx.accept(CONFIGURATION))
        timers.append(ctx.accept(TIMER))
        yield

    def select(document: ConfigDocument) -> Composition:
        composition = build(document)
        entry = ContribEntry(
            name="rpc-consumer",
            definition=Contrib(
                accepts=[CLIENT, CONFIGURATION, TIMER], lifespan=consumer
            ),
        )
        return composition.model_copy(
            update={
                "entries": (*composition.entries, entry),
                "roots": (entry.name,),
            }
        )

    async with (
        node(config),
        application(configer_options=(str(config),), start=select),
        Tool(configurations[0]) as tool,
    ):
        os.environ["BCN_TOOL_CONFIG"] = str(tool.path)
        tick_ms = timers[0].tick_ms
        peers: dict[str, Peer] = {}
        for agent in ("Cloud", "Tifa"):
            address = json.loads(
                await asyncio.to_thread((directory / agent / "channel.json").read_text)
            )
            peers[agent] = Peer(
                *await asyncio.open_connection("127.0.0.1", address["port"]),
                tick_ms=tick_ms,
            )

        async def request(agent: str, resource: str, command: str, **payload):
            return cast(
                dict[str, Any],
                await clients[0].call(
                    "messages",
                    "request",
                    {
                        "kind": "command",
                        "agent_id": agent,
                        "actor_id": agent,
                        "resource": resource,
                        "command": command,
                        **payload,
                    },
                ),
            )

        async def received(agent: str, id: str):
            while True:
                result = await request(agent, "inbox", "check")
                if any(
                    target["latest_message_id"] == id
                    for target in result.get("result", {}).get("targets", ())
                ):
                    return result["result"]
                await asyncio.sleep(0.01)

        try:
            source = directory / "Cloud" / "source-notes.txt"
            await asyncio.to_thread(
                source.write_text, "海报发布：周五下午，沿用确认版式。"
            )
            first = await peers["Cloud"].inbound(
                "海报的上线日期改到周五，版式继续用昨天确认的那版。",
                sender_id="provider-user-42",
                attachments=(source,),
            )
            await received("Cloud", first)
            await peers["Tifa"].inbound(
                "客户邮件里的预算调整请保存在我的项目里。", sender="aerith"
            )
            inbox = await bcc(directory, "Cloud", "inbox", "check")
            assert "hanchin" in inbox
            history = await bcc(
                directory, "Cloud", "message", "read", "--target", "dm:@hanchin"
            )
            assert "上线日期" in history and "source-notes.txt" in history
            response = await request("Cloud", "message", "read", target="dm:@hanchin")
            saved = (
                directory
                / "Cloud"
                / response["result"]["messages"][0]["attachments"][0]["relative_path"]
            )
            assert "确认版式" in await asyncio.to_thread(saved.read_text)
            second = await peers["Cloud"].inbound(
                "刚确认周五下午三点发布，海报上请补上时间。", reply_to=first
            )
            await received("Cloud", second)
            attachment = directory / "Cloud" / "poster.txt"
            await asyncio.to_thread(attachment.write_text, "上线时间：周五 15:00\n")
            held = await bcc(
                directory,
                "Cloud",
                "message",
                "send",
                "--target",
                "dm:@hanchin",
                "--attachment",
                str(attachment),
                "--reply-to",
                first,
                body="已更新海报日期和时间，附件供确认。",
                success=False,
            )
            assert "draft" in held.lower() and "周五下午三点" in held, held
            checked = await bcc(directory, "Cloud", "message", "check")
            assert "上线日期" in checked and "下午三点" in checked
            sending = asyncio.create_task(
                bcc(
                    directory,
                    "Cloud",
                    "message",
                    "send",
                    "--send-draft",
                    "--target",
                    "dm:@hanchin",
                )
            )
            outbound = await peers["Cloud"].delivered()
            assert "已更新海报" in outbound["body"]
            assert "15:00" in await asyncio.to_thread(
                Path(outbound["attachments"][0]["path"]).read_text
            )
            assert outbound["target"]["reply_to_message_id"] == first
            assert "sent" in (await sending).lower()
            report = "海报发布说明：" + "周五下午三点上线，印刷前核对时间与报价。" * 480
            sending_report = asyncio.create_task(
                bcc(
                    directory,
                    "Cloud",
                    "message",
                    "send",
                    "--target",
                    "dm:@hanchin",
                    "--attachment",
                    str(attachment),
                    body=report,
                )
            )
            deliveries = len(peers["Cloud"].deliveries)
            peer = peers["Cloud"]
            first_part = json.loads(await peer.reader.readline())
            await peer.acknowledge(first_part)
            second_part = json.loads(await peer.reader.readline())
            # The next operation starts only after the first visible part is durable.
            rows = await asyncio.to_thread(stored_messages, directory)
            saved_part = rows[first_part["id"]]
            quoted = await peer.inbound(
                "第一段的发布时间已核对，后面的印刷说明请继续发。",
                reply_to=first_part["id"],
            )
            await received("Cloud", quoted)
            quoted_history = await request(
                "Cloud", "message", "read", target="dm:@hanchin"
            )
            quoted_message = next(
                message
                for message in quoted_history["result"]["messages"]
                if message["message_id"] == quoted
            )
            assert quoted_message["reply_to_message_id"] == saved_part["message_id"]
            await peer.acknowledge(second_part)
            delivered_report = await peer.delivered(backpressure=True)
            report_result = await sending_report
            assert "sent" in report_result.lower()
            assert (
                first_part["outbound"]["body"]
                + second_part["outbound"]["body"]
                + delivered_report["body"]
                == report
            )
            rows = await asyncio.to_thread(stored_messages, directory)
            report_parts = peer.deliveries[deliveries:]
            for packet in report_parts:
                saved_part = rows[packet["id"]]
                assert saved_part["body"] == packet["outbound"]["body"]
                assert [
                    item["name"] for item in json.loads(saved_part["attachments_json"])
                ] == [item["name"] for item in packet["outbound"]["attachments"]]
            send_ids = {
                json.loads(rows[packet["id"]]["metadata_json"])["outbound_send_id"]
                for packet in report_parts
            }
            assert len(send_ids) == 1
            assert send_ids.pop() in report_result
            # Observe the reply that arrived while this report was still sending.
            await bcc(directory, "Cloud", "message", "check")
            assert "15:00" in await asyncio.to_thread(
                Path(delivered_report["attachments"][0]["path"]).read_text
            )
            delivered_ids = [
                packet["operation_id"]
                for packet in peers["Cloud"].deliveries[deliveries:]
            ]
            assert len(set(delivered_ids)) == len(delivered_ids)
            for sender in ("hanchin", "@hanchin"):
                search = await bcc(
                    directory,
                    "Cloud",
                    "message",
                    "search",
                    "--query",
                    "海报",
                    "--sender",
                    sender,
                    "--target",
                    "dm:@hanchin",
                    "--sort",
                    "relevance",
                    "--limit",
                    "2",
                )
                assert "海报" in search
            budgeted_search = await request(
                "Cloud",
                "message",
                "search",
                query="上线日期 昨天确认",
                target="dm:@hanchin",
                sort="relevance",
                query_budget=5.0,
            )
            assert budgeted_search["ok"]
            assert any(
                "上线日期" in message["snippet"]
                for message in budgeted_search["result"]["messages"]
            )
            around = await bcc(
                directory,
                "Cloud",
                "message",
                "read",
                "--target",
                "dm:@hanchin",
                "--around",
                first,
                "--limit",
                "2",
            )
            assert "上线日期" in around
            catalog = await request("Cloud", "inbox", "check")
            assert catalog["ok"]
            group_message = await peers["Cloud"].inbound(
                "@Cloud 设计群已经确认海报，接下来交给印刷。",
                kind="group",
                target="design",
            )
            group_catalog = await received("Cloud", group_message)
            group_target = next(
                target["target"]
                for target in group_catalog["targets"]
                if target["latest_message_id"] == group_message
            )
            left = await bcc(
                directory, "Cloud", "thread", "unfollow", "--target", group_target
            )
            assert "Design room" in left
            tifa = await bcc(directory, "Tifa", "message", "check")
            assert "预算调整" in tifa
            dm_id = await peers["Cloud"].inbound(
                "@Cloud 印刷报价下来了，我也需要一份确认结果。",
                kind="group",
                target="design",
                sender="barret",
            )
            await received("Cloud", dm_id)
            opening = asyncio.create_task(
                bcc(
                    directory,
                    "Cloud",
                    "message",
                    "send",
                    "--target",
                    "dm:@barret",
                    body="报价已经收到，确认后我会在这里回复你。",
                )
            )
            opened = await peers["Cloud"].delivered()
            assert opened["target"]["thread_id"] == "barret"
            await opening
            read_opened = await bcc(
                directory, "Cloud", "message", "read", "--target", "dm:@barret"
            )
            assert "报价已经收到" in read_opened
            # A partial receipt keeps the draft available for reconciliation.
            partial = asyncio.create_task(
                bcc(
                    directory,
                    "Cloud",
                    "message",
                    "send",
                    "--target",
                    "dm:@hanchin",
                    body=(
                        "这版请先确认文字，附件稍后补齐。" + "印刷说明供核对。" * 600
                    ),
                    success=False,
                )
            )
            accepted_part = json.loads(await peers["Cloud"].reader.readline())
            await peers["Cloud"].acknowledge(accepted_part)
            await peers["Cloud"].delivered(status="partial")
            assert "partial" in (await partial).lower()
            rows = await asyncio.to_thread(stored_messages, directory)
            accepted_message = rows[accepted_part["id"]]
            assert accepted_message["delivery_state"] == "sent"
            assert (
                json.loads(accepted_message["metadata_json"])["outbound_delivery"][
                    "state"
                ]
                == "partial"
            )
            persisted = await bcc(
                directory, "Cloud", "message", "read", "--target", "dm:@hanchin"
            )
            assert "已更新海报日期和时间" in persisted
        finally:
            for peer in peers.values():
                await peer.aclose()
    # A new node reads the same durable history, with a fresh process-local draft set.
    clients.clear()
    configurations.clear()
    async with (
        node(config),
        application(configer_options=(str(config),), start=select),
        Tool(configurations[0]) as tool,
    ):
        os.environ["BCN_TOOL_CONFIG"] = str(tool.path)
        history = await bcc(
            directory, "Cloud", "message", "read", "--target", "dm:@hanchin"
        )
        assert "已更新海报日期和时间" in history
        rows = await asyncio.to_thread(stored_messages, directory)
        for packet in report_parts:
            around = await bcc(
                directory,
                "Cloud",
                "message",
                "read",
                "--target",
                "dm:@hanchin",
                "--around",
                rows[packet["id"]]["message_id"],
            )
            assert (
                packet["outbound"]["body"] in around
                if packet["outbound"]["body"]
                else packet["outbound"]["attachments"][0]["name"] in around
            )
    print(
        "Installed messages: TCP ingress, bcc/RPC, snapshots, drafts, attachments, receipts, search and restart passed"
    )


if __name__ == "__main__":
    with TemporaryDirectory(prefix="bm-") as path:
        asyncio.run(exercise(Path(path)))
