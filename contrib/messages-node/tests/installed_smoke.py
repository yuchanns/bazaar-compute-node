"""Use bcc and public RPC against a separate node and TCP TestChannel peers."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from importlib.metadata import distribution
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import uuid7

import tomli_w
from bazaar_contrib_sidecar import CLIENT, Client, Remote, Symbol
from bazaar_core import Context, Contrib, ContribEntry, ContribHub, ContribSpec, load


class Peer:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader = reader
        self.writer = writer

    async def write(self, packet: dict[str, Any]):
        self.writer.write(json.dumps(packet).encode() + b"\n")
        await self.writer.drain()

    async def inbound(
        self,
        body: str,
        *,
        sender: str = "hanchin",
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
                    "sender": {"id": sender, "name": sender},
                    "body": body,
                    "attachments": [
                        {"name": path.name, "path": str(path)} for path in attachments
                    ],
                }
            }
        )
        return id

    async def delivered(self, *, status: str = "confirmed") -> dict[str, Any]:
        packet = json.loads(await self.reader.readline())
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
        return packet["outbound"]

    async def aclose(self):
        self.writer.close()
        await self.writer.wait_closed()


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
    endpoint = (
        str(directory / "s") if os.name != "nt" else rf"\\.\pipe\bcn-messages-{uuid7()}"
    )
    os.environ["BCN_HOME"] = str(directory)
    os.environ["BCN_ENDPOINT"] = endpoint
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
                        ("sqlite", {"path": str(directory / "messages.sqlite3")}),
                        ("database", {"backend": "sqlite"}),
                        ("schema", {}),
                        ("node-migrations", {}),
                        ("audit", {}),
                        (native, {"endpoint": endpoint}),
                        ("sidecar", {}),
                        ("messages-node", {}),
                        ("messages-gateway", {"directory": str(directory)}),
                    )
                ],
            }
        ),
    )
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
                    name="rpc-consumer",
                    definition=Contrib(accepts=[CLIENT], lifespan=consumer),
                ),
            )
        }
    )

    async with node(config), ContribHub() as hub, hub.enable(composition):
        remote: Remote = await clients[0].context.call(
            "accept", Symbol("bazaar_contrib_messages:MESSAGES")
        )
        peers: dict[str, Peer] = {}
        for agent in ("Cloud", "Tifa"):
            address = json.loads(
                await asyncio.to_thread((directory / agent / "channel.json").read_text)
            )
            peers[agent] = Peer(
                *await asyncio.open_connection("127.0.0.1", address["port"])
            )

        async def request(agent: str, resource: str, command: str, **payload):
            return await remote.call(
                "request",
                {
                    "kind": "command",
                    "agent_id": agent,
                    "actor_id": agent,
                    "resource": resource,
                    "command": command,
                    **payload,
                },
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
            search = await bcc(
                directory,
                "Cloud",
                "message",
                "search",
                "--query",
                "海报",
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
                    body="这版请先确认文字，附件稍后补齐。",
                    success=False,
                )
            )
            await peers["Cloud"].delivered(status="partial")
            assert "partial" in (await partial).lower()
            persisted = await bcc(
                directory, "Cloud", "message", "read", "--target", "dm:@hanchin"
            )
            assert "已更新海报日期和时间" in persisted
        finally:
            for peer in peers.values():
                await peer.aclose()
    # A new node reads the same durable history, with a fresh process-local draft set.
    async with node(config):
        history = await bcc(
            directory, "Cloud", "message", "read", "--target", "dm:@hanchin"
        )
        assert "已更新海报日期和时间" in history
    print(
        "Installed messages: TCP ingress, bcc/RPC, snapshots, drafts, attachments, receipts, search and restart passed"
    )


if __name__ == "__main__":
    with TemporaryDirectory(prefix="bm-") as path:
        asyncio.run(asyncio.wait_for(exercise(Path(path)), 60))
