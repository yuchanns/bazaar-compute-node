"""Drive installed agents through a real TCP channel and document subprocess."""

from __future__ import annotations

import asyncio
import json
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any
from uuid import uuid7

import aiofiles
from bazaar_contrib_audit import AUDIT_EVENT, AuditEvent
from bazaar_contrib_channel import (
    ChannelApprovalRequest,
    ChannelTarget,
    InboundAttachment,
    SenderIdentity,
)
from bazaar_contrib_harness import ApprovalResult, HarnessOutput
from bazaar_core import (
    CONTRIB_CHANGED,
    Context,
    Contrib,
    ContribChange,
    ContribEntry,
    ContribHub,
    ContribSpec,
    Failed,
    load,
)
from bcn_agent_workbench import ChannelMessage as InboundMessage


class Peer:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader = reader
        self.writer = writer
        self.events: list[dict[str, Any]] = []

    async def write(self, packet: dict[str, Any]):
        self.writer.write(json.dumps(packet).encode() + b"\n")
        await self.writer.drain()

    async def until(self, field: str) -> dict[str, Any]:
        while data := await self.reader.readline():
            packet = json.loads(data)
            if field in packet:
                return packet
            self.events.append(packet)
        raise RuntimeError(f"channel closed before {field}")

    async def aclose(self):
        self.writer.close()
        await self.writer.wait_closed()


async def connect(endpoint: Path, ready: asyncio.Event, hub: ContribHub) -> Peer:
    while True:
        ready.clear()
        if await asyncio.to_thread(endpoint.exists):
            break
        failures = [
            record.state.error
            for record in hub.snapshots
            if isinstance(record.state, Failed)
        ]
        if failures:
            raise RuntimeError("; ".join(failures))
        await ready.wait()
    data = json.loads(await asyncio.to_thread(endpoint.read_text))
    reader, writer = await asyncio.open_connection("127.0.0.1", data["port"])
    return Peer(reader, writer)


async def report(peer: Peer, draft: Path, target: ChannelTarget | None = None) -> Path:
    message = InboundMessage(
        id=str(uuid7()),
        target=target or ChannelTarget(kind="group", thread_id="editorial-team"),
        sender=SenderIdentity(id="editor", name="Editor"),
        body="I’m preparing this draft for the editor. Could you save a short word-count report for the attached draft?",
        attachments=(
            InboundAttachment(name=draft.name, path=draft, media_type="text/plain"),
        ),
    )
    await peer.write(
        {
            "inbound": message.model_dump(mode="json"),
        }
    )
    packet = await peer.until("approval")
    approval = ChannelApprovalRequest.model_validate(packet["approval"])
    assert approval.sender_id == message.sender.id
    assert approval.target == message.target.model_copy(
        update={"reply_to_message_id": message.id}
    )
    await peer.write(
        {
            "id": packet["id"],
            "decision": ApprovalResult(
                request_id=approval.approval.request_id, decision="approved"
            ).model_dump(mode="json"),
        }
    )
    path = Path(str(approval.approval.details["path"]))
    while True:
        packet = await peer.until("event")
        peer.events.append(packet)
        event = HarnessOutput.model_validate(packet["event"])
        assert event.session_id == approval.approval.session_id
        assert event.turn_id == approval.approval.turn_id
        assert packet["target"]["kind"] == message.target.kind
        assert packet["target"]["thread_id"] == message.target.thread_id
        if event.kind == "text":
            assert path.name in event.text
        elif event.kind == "completed":
            break
    return path


async def session_routes(peer: Peer, draft: Path, workspace: Path) -> Path:
    """Verify approval routing, turn events and session isolation."""
    first = await report(peer, draft)
    second = await report(peer, draft)
    private = await report(
        peer, draft, ChannelTarget(kind="dm", thread_id="private-editor")
    )
    assert first.parent == second.parent
    assert first.parent != private.parent
    workspace = await asyncio.to_thread(workspace.resolve)
    for saved in (first, second, private):
        content = await asyncio.to_thread(saved.read_text)
        assert workspace.name in content
        assert await asyncio.to_thread(saved.parents[2].samefile, workspace)
        assert await asyncio.to_thread(saved.with_suffix(".closed").read_text)
        data = json.loads(content)
        request = json.loads(
            await asyncio.to_thread((saved.parent / "session.json").read_text)
        )
        assert data["context"]["session_id"] == data["session_id"] == request["id"]
        assert data["context"]["agent_id"] == data["agent_id"]
        assert data["words"] > 0 and data["lines"] > 0
        instruction = "\n".join(request["instructions"])
        for value in (
            request["id"],
            "document",
            str(workspace),
        ):
            assert value in instruction
        assert (
            "Credential handling" in instruction and "bcc message send" in instruction
        )
        assert data["context"]["actor_id"] in {
            "editorial-team",
            "private-editor",
            data["agent_id"],
        }
    assert {"started", "text", "completed"} <= {
        packet["event"]["kind"] for packet in peer.events if "event" in packet
    }
    return first.parent


async def turn_failure(peer: Peer, draft: Path, session: Path):
    """Verify failed turns finish before the next input is handled."""
    removed = draft.with_name("withdrawn-draft.txt")
    await asyncio.to_thread(removed.write_text, "An earlier draft for the editor")
    await asyncio.to_thread(removed.unlink)
    message = InboundMessage(
        id=str(uuid7()),
        target=ChannelTarget(kind="group", thread_id="editorial-team"),
        sender=SenderIdentity(id="editor", name="Editor"),
        body="Could you prepare the word-count report for this earlier draft too?",
        attachments=(InboundAttachment(name=removed.name, path=removed),),
    )
    await peer.write({"inbound": message.model_dump(mode="json")})
    while True:
        packet = await peer.until("event")
        peer.events.append(packet)
        event = HarnessOutput.model_validate(packet["event"])
        if event.kind == "failed":
            assert event.session_id == session.name
            assert packet["target"]["thread_id"] == message.target.thread_id
            break
    saved = await report(peer, draft)
    assert saved.parent == session
    assert await asyncio.to_thread(saved.read_text)


async def exercise(path: Path):
    await asyncio.to_thread(path.mkdir)
    workspaces = {name: path / name for name in ("Cloud", "Tifa")}
    drafts = {}
    for name, workspace in workspaces.items():
        await asyncio.to_thread(workspace.mkdir)
        draft = workspace / "draft.txt"
        await asyncio.to_thread(
            draft.write_text,
            f"{name} owns the editorial draft. The report is due Friday.\nA chart is ready for review.",
        )
        drafts[name] = draft
    composition = await asyncio.to_thread(
        load,
        [
            ContribSpec(contrib="audit"),
            ContribSpec(contrib="storage"),
            ContribSpec(
                contrib="sqlite", config={"path": str(path / "providers.sqlite3")}
            ),
            ContribSpec(contrib="database", config={"backend": "sqlite"}),
            ContribSpec(contrib="schema"),
            ContribSpec(contrib="legacy-node-migrations"),
            ContribSpec(
                contrib="agent",
                config={
                    "agents": [
                        {
                            "id": name,
                            "name": name,
                            "workspace": str(workspace),
                            "mode": "session"
                            if name == "Cloud"
                            else "dangerous_individual",
                            "channel": [{"kind": "test-channel"}],
                            "harness": [
                                {
                                    "kind": "document",
                                    "title": f"{name} editorial report",
                                }
                            ],
                        }
                        for name, workspace in workspaces.items()
                    ]
                },
            ),
            ContribSpec(contrib="channel-builders"),
            ContribSpec(contrib="harness-builders"),
            ContribSpec(contrib="test-channel-builder"),
            ContribSpec(contrib="document-builder"),
        ],
    )
    ready = asyncio.Event()

    def changed(ctx: Context, change: ContribChange):
        ready.set()

    lock = asyncio.Lock()

    async def record(ctx: Context, event: AuditEvent):
        async with lock, aiofiles.open(path / "audit.jsonl", "a") as stream:
            await stream.write(event.model_dump_json() + "\n")

    composition = composition.model_copy(
        update={
            "entries": (
                *composition.entries,
                ContribEntry(
                    name="ready", definition=Contrib(listen={CONTRIB_CHANGED: changed})
                ),
                ContribEntry(
                    name="audit-file", definition=Contrib(listen={AUDIT_EVENT: record})
                ),
            )
        }
    )
    async with asyncio.timeout(30), AsyncExitStack() as peers:
        async with ContribHub() as hub:
            candidate = hub.validate(composition)
            async with hub.enable(candidate):
                connected = {}
                for name, workspace in workspaces.items():
                    peer = await connect(workspace / "channel.json", ready, hub)
                    peers.push_async_callback(peer.aclose)
                    connected[name] = peer
                for name, peer in connected.items():
                    session = await session_routes(peer, drafts[name], workspaces[name])
                    await turn_failure(peer, drafts[name], session)
                # Leave one genuine subprocess waiting on channel approval during shutdown.
                await connected["Cloud"].write(
                    {
                        "inbound": InboundMessage(
                            id=str(uuid7()),
                            target=ChannelTarget(
                                kind="group", thread_id="editorial-team"
                            ),
                            sender=SenderIdentity(id="editor"),
                            body="The revised draft is ready too. Could you save another short summary before I send it?",
                        ).model_dump(mode="json")
                    }
                )
                pending = await connected["Cloud"].until("approval")
                approval = ChannelApprovalRequest.model_validate(pending["approval"])
                pending_path = Path(str(approval.approval.details["path"]))
            for peer in connected.values():
                remaining = await peer.reader.read()
                peer.events.extend(json.loads(line) for line in remaining.splitlines())
            assert any(
                packet["event"]["kind"] == "cancelled"
                for packet in connected["Cloud"].events
                if "event" in packet
            )
        assert await asyncio.to_thread(pending_path.with_suffix(".closed").read_text)
        for workspace in workspaces.values():
            assert await asyncio.to_thread((workspace / "channel.closed").read_text)
            sessions = await asyncio.to_thread(list, (workspace / "reports").iterdir())
            for session in sessions:
                assert await asyncio.to_thread((session / "session.closed").read_text)
    async with aiofiles.open(path / "audit.jsonl") as stream:
        records = [AuditEvent.model_validate_json(line) async for line in stream]
    assert {"Cloud", "Tifa"} <= {event.context.agent_id for event in records}
    requests = {
        event.context.turn_id: event
        for event in records
        if event.event_name == "harness.turn.requested"
    }
    for name, draft in drafts.items():
        content = await asyncio.to_thread(draft.read_text)
        reads = []
        for event in records:
            if (
                event.context.agent_id == name
                and event.event_name == "harness.tool.completed"
            ):
                output = HarnessOutput.model_validate(event.payload)
                if output.metadata.get("name") == "file.read":
                    reads.append((event, output))
        assert reads
        for event, output in reads:
            assert output.metadata["result"] == content
            raw = HarnessOutput.model_validate(output.raw)
            assert raw.metadata["result"] == content
            assert raw.occurred_at_ms == event.occurred_at_ms
            assert output.session_id == event.context.session_id
            assert output.turn_id == event.context.turn_id
    for event in records:
        if event.event_name == "harness.text":
            request = requests[event.context.turn_id]
            assert event.run_id == request.run_id
            assert event.sequence > request.sequence
            assert event.monotonic_ns >= request.monotonic_ns
            assert event.context.thread_id == request.context.thread_id
    assert any(event.event_name == "approval.decided" for event in records)
