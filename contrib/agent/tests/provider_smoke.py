"""Drive installed agents through a real TCP channel and document subprocess."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sqlite3
from collections.abc import Callable, Collection
from contextlib import AsyncExitStack, asynccontextmanager, closing
from copy import deepcopy
from importlib.metadata import version
from pathlib import Path
from time import time_ns
from typing import Any, cast
from uuid import UUID, uuid7

import aiofiles
from bazaar_compute_node.boot import node
from bazaar_contrib_audit import AUDIT_EVENT, AuditEvent
from bazaar_contrib_channel import (
    ChannelApprovalRequest,
    ChannelTarget,
    ChannelTargetPresentation,
    SenderIdentity,
)
from bazaar_contrib_harness import (
    ApprovalResult,
    CommandDetail,
    ContentDelta,
    FileDetail,
    HarnessOutput,
    ModelUpdated,
    ToolCallCompleted,
    ToolCallFailed,
    ToolCallStarted,
    TurnFailed,
)
from bazaar_core import (
    CONTRIB_CHANGED,
    Blocked,
    Composition,
    Context,
    Contrib,
    ContribChange,
    ContribEntry,
    ContribHub,
    ContribSpec,
    Failed,
    Running,
    load,
)
from bcn_agent_workbench import (
    Attachment,
    HarnessBuilderParameters,
    configured,
    named,
    read_context_cache,
    running,
)
from bcn_agent_workbench import ChannelMessage as InboundMessage
from pydantic import TypeAdapter

from bazaar_contrib_agent import AGENTS, AgentInstances
from bazaar_contrib_agent.activity import AGENT_ACTIVITY, Activity, AgentActivity


class Peer:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader = reader
        self.writer = writer
        self.events: list[dict[str, Any]] = []
        self.automatic = False

    async def write(self, packet: dict[str, Any]):
        self.writer.write(json.dumps(packet).encode() + b"\n")
        await self.writer.drain()

    async def until(self, field: str, *, acknowledge: bool = True) -> dict[str, Any]:
        while data := await self.reader.readline():
            packet = json.loads(data)
            if "outbound" in packet and acknowledge:
                await self.write(
                    {
                        "id": packet["id"],
                        "receipt": {
                            "status": "confirmed",
                            "provider_message_id": str(uuid7()),
                        },
                    }
                )
            if field == "approval" and packet.get("event", {}).get("payload", {}).get(
                "type"
            ) in {"failed", "cancelled"}:
                raise RuntimeError(f"Turn ended before approval: {packet}")
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


async def submit(
    peer: Peer,
    draft: Path,
    target: ChannelTarget | None = None,
    *,
    body: str = "I’m preparing this draft for the editor. Could you save a short word-count report for the attached draft?",
):
    message = InboundMessage(
        id=str(uuid7()),
        target=target or ChannelTarget(kind="group", thread_id="editorial-team"),
        sender=SenderIdentity(id="editor", name="Editor"),
        body=body,
        attachments=(Attachment(name=draft.name, path=draft, media_type="text/plain"),),
        mentions_agent=True,
        metadata={"review": "approved"},
    )
    await peer.write({"inbound": message.model_dump(mode="json")})
    if peer.automatic:
        while True:
            packet = await peer.until("event")
            peer.events.append(packet)
            event = HarnessOutput.model_validate(packet["event"])
            if event.payload.type == "completed":
                return message, packet
            if event.payload.type in {"failed", "cancelled"}:
                raise RuntimeError(f"Document task ended before its report: {packet}")
    packet = await peer.until("approval")
    approval = ChannelApprovalRequest.model_validate(packet["approval"])
    assert approval.sender_id == message.sender.id
    assert approval.target == message.target.model_copy(
        update={"reply_to_message_id": message.id}
    )
    return message, packet


async def finish(peer: Peer, packet: dict[str, Any] | None = None) -> Path:
    if packet is None:
        if peer.automatic:
            while True:
                packet = await peer.until("event")
                peer.events.append(packet)
                if packet["event"]["payload"]["type"] == "completed":
                    break
                if packet["event"]["payload"]["type"] in {"failed", "cancelled"}:
                    raise RuntimeError(
                        f"Document task ended before its report: {packet}"
                    )
        else:
            packet = await peer.until("approval")
    if "event" in packet:
        return Path(
            next(
                item["event"]["payload"]["call"]["detail"]["changes"][0]["path"]
                for item in reversed(peer.events)
                if item.get("event", {}).get("turn_id") == packet["event"]["turn_id"]
                and item["event"]["payload"].get("call", {}).get("name") == "file.write"
            )
        )
    approval = ChannelApprovalRequest.model_validate(packet["approval"])
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
        if isinstance(event.payload, ContentDelta):
            assert path.name in event.payload.text
        elif event.payload.type == "completed":
            break
    return path


async def report(peer: Peer, draft: Path, target: ChannelTarget | None = None) -> Path:
    _, packet = await submit(peer, draft, target)
    return await finish(peer, packet)


async def session_routes(peer: Peer, draft: Path, workspace: Path) -> Path:
    """Verify approval routing, turn events and session isolation."""
    first = await report(peer, draft)
    second = await report(peer, draft)
    private = await report(
        peer, draft, ChannelTarget(kind="dm", thread_id="private-editor")
    )
    assert first.parent == second.parent
    if workspace.name == "Tifa":
        assert first.parent == private.parent
    else:
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
        assert str(workspace) in instruction
        assert (
            "Credential handling" in instruction and "bcc message send" in instruction
        )
        if workspace.name == "Tifa":
            assert data["context"]["actor_id"] == data["agent_id"]
        else:
            assert data["context"]["actor_id"] != data["agent_id"]
    assert {"started", "content.delta", "completed"} <= {
        packet["event"]["payload"]["type"]
        for packet in peer.events
        if "event" in packet
    }
    return first.parent


async def turn_failure(peer: Peer, draft: Path, session: Path):
    """Verify failed turns finish before the next input is handled."""
    removed = draft.with_name("withdrawn-draft.txt")
    await asyncio.to_thread(removed.write_bytes, b"\xff\xfe\xfa")
    message = InboundMessage(
        id=str(uuid7()),
        target=ChannelTarget(kind="group", thread_id="editorial-team"),
        sender=SenderIdentity(id="editor", name="Editor"),
        body="Could you prepare the word-count report for this earlier draft too?",
        attachments=(Attachment(name=removed.name, path=removed),),
        mentions_agent=True,
        metadata={"review": "approved"},
    )
    await peer.write({"inbound": message.model_dump(mode="json")})
    while True:
        packet = await peer.until("event")
        peer.events.append(packet)
        event = HarnessOutput.model_validate(packet["event"])
        if event.payload.type == "failed":
            assert packet["target"]["thread_id"] == message.target.thread_id
            break
    saved = await report(peer, draft)
    assert await asyncio.to_thread(saved.read_text)


async def attachment_download(
    peer: Peer,
    draft: Path,
    session: Path,
    database: Path,
    persisted: asyncio.Queue[AuditEvent],
):
    """Finish independent sources while another waits, then replay the message."""
    message = InboundMessage(
        id=str(uuid7()),
        target=ChannelTarget(kind="group", thread_id="editorial-team"),
        sender=SenderIdentity(id="editor", name="Editor"),
        body="Could you prepare a word-count report for the updated draft and its companion note? The earlier appendix may no longer be available.",
        attachments=(
            Attachment(name=draft.name, path=draft, gate="updated-draft"),
            Attachment(name="companion-note.txt", path=draft, gate="companion-note"),
            Attachment(
                name="earlier-appendix.txt",
                path=draft.with_name("missing.txt"),
                gate="earlier-appendix",
            ),
        ),
        mentions_agent=True,
        metadata={"review": "approved"},
    )
    await peer.write({"inbound": message.model_dump(mode="json")})
    while True:
        event = await persisted.get()
        if cast(dict[str, Any], event.payload)["metadata"]["text"] == message.body:
            break

    def read():
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            return tuple(
                dict(row)
                for row in db.execute(
                    "SELECT a.*, m.target FROM inbound_attachments a "
                    "JOIN messages m ON m.message_id=a.message_id "
                    "WHERE m.provider_message_id=? ORDER BY a.ordinal",
                    (message.id,),
                )
            )

    downloading = await asyncio.to_thread(read)
    assert [row["name"] for row in downloading] == [
        item.name for item in message.attachments
    ]
    assert all(
        row["state"] == "downloading" and row["relative_path"] is None
        for row in downloading
    )
    request = json.loads(await asyncio.to_thread((session / "session.json").read_text))
    environment = dict(os.environ) | request["environment"]
    command = await asyncio.to_thread(shutil.which, "bcc", path=environment["PATH"])
    assert command is not None
    process = await asyncio.create_subprocess_exec(
        command,
        "message",
        "read",
        "--target",
        downloading[0]["target"],
        "--around",
        downloading[0]["message_id"],
        "--limit",
        "1",
        cwd=draft.parent,
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await process.communicate()
    assert process.returncode == 0, output.decode()
    for row in downloading:
        assert (
            f"{row['name']} (id:{row['attachment_id']}, state:downloading)"
            in output.decode()
        )
    process = await asyncio.create_subprocess_exec(
        command,
        "node",
        "version",
        cwd=draft.parent,
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await process.communicate()
    assert process.returncode == 0, output.decode()
    installed = await asyncio.to_thread(version, "bazaar-compute")
    assert f"running bazaar-compute {installed}." in output.decode()

    await peer.write({"release_attachment": "companion-note"})
    await peer.write({"release_attachment": "earlier-appendix"})
    while True:
        completed = await asyncio.to_thread(read)
        if completed[1]["state"] == "ready" and completed[2]["state"] == "failed":
            break
        await asyncio.sleep(0.02)
    assert completed[0]["state"] == "downloading"
    assert completed[2]["error"]
    stored = draft.parent / completed[1]["relative_path"]
    assert await asyncio.to_thread(stored.read_bytes) == await asyncio.to_thread(
        draft.read_bytes
    )

    await peer.write({"release_attachment": "updated-draft"})
    saved = await finish(peer)
    assert await asyncio.to_thread(saved.read_text)
    completed = await asyncio.to_thread(read)
    assert [row["attachment_id"] for row in completed] == [
        row["attachment_id"] for row in downloading
    ]
    assert completed[0]["state"] == "ready"
    assert completed[1]["state"] == "ready"
    assert completed[2]["state"] == "failed" and completed[2]["error"]
    stored = draft.parent / completed[0]["relative_path"]
    assert await asyncio.to_thread(stored.read_bytes) == await asyncio.to_thread(
        draft.read_bytes
    )

    replay = message.model_copy(
        update={
            "attachments": tuple(
                item.model_copy(update={"gate": None}) for item in message.attachments
            )
        }
    )
    await peer.write({"inbound": replay.model_dump(mode="json")})
    await report(peer, draft)

    def referenced():
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
            count = db.execute(
                "SELECT COUNT(*) FROM messages WHERE provider_message_id=?",
                (message.id,),
            ).fetchone()[0]
            paths = {
                draft.parent / row[0]
                for row in db.execute(
                    "SELECT a.relative_path FROM inbound_attachments a "
                    "JOIN messages m ON m.message_id=a.message_id "
                    "WHERE m.agent_id='Cloud' AND a.state='ready'"
                )
            }
            return count, paths

    count, paths = await asyncio.to_thread(referenced)
    assert count == 1
    assert (
        set(await asyncio.to_thread(list, (draft.parent / "attachments").iterdir()))
        == paths
    )

    message = InboundMessage(
        id=str(uuid7()),
        target=ChannelTarget(kind="group", thread_id="new-editorial-desk"),
        sender=SenderIdentity(id="guest-editor", name="Guest editor"),
        body="Hello, could you help edit this draft for our upcoming newsletter?",
        attachments=(Attachment(name=draft.name, path=draft, gate="unreviewed-draft"),),
        mentions_agent=True,
    )
    await peer.write({"inbound": message.model_dump(mode="json")})
    while True:
        rejected = await asyncio.to_thread(read)
        if rejected and rejected[0]["state"] == "failed":
            break
        await asyncio.sleep(0.02)
    assert "approved" in rejected[0]["error"]


async def inbound_context(
    peer: Peer, draft: Path, database: Path, persisted: asyncio.Queue[AuditEvent]
):
    reference = InboundMessage(
        id=str(uuid7()),
        target=ChannelTarget(kind="group", thread_id="editorial-team"),
        sender=SenderIdentity(id="editor", name="Editor"),
        body="This earlier editorial note sets the Friday publication deadline.",
        message_type="rich-text",
        target_presentation=ChannelTargetPresentation(display_name="Editorial review"),
        occurred_at_ms=None,
        received_at_ms=time_ns() // 1_000_000,
        provider_payload_ref="tcp:editorial-archive:reference",
        notifies_runtime=False,
        metadata={
            "review": "approved",
            "sender_kind": "human",
            "topic": "Friday edition",
        },
    )
    await peer.write({"inbound": reference.model_dump(mode="json")})
    while True:
        event = await persisted.get()
        if cast(dict[str, Any], event.payload)["metadata"]["text"] == reference.body:
            break
    saved = await report(
        peer,
        draft,
        reference.target.model_copy(update={"reply_to_message_id": reference.id}),
    )
    report_data = json.loads(await asyncio.to_thread(saved.read_text))
    assert reference.body in report_data["inbox"]

    def read():
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                "SELECT m.*, c.target_display_name FROM messages m "
                "JOIN channel_sessions c ON c.id=m.channel_session_id "
                "WHERE m.provider_message_id=?",
                (reference.id,),
            ).fetchone()
            return dict(row)

    row = await asyncio.to_thread(read)
    assert row["message_type"] == reference.message_type
    assert row["received_at_ms"] == reference.received_at_ms
    assert row["provider_time_ms"] is None
    assert row["sender_id"] == "editor"
    assert row["target_display_name"] == "Editorial review"
    assert row["provider_payload_ref"] == reference.provider_payload_ref
    assert json.loads(row["metadata_json"])["topic"] == "Friday edition"

    reference = reference.model_copy(
        update={
            "id": str(uuid7()),
            "body": "The Friday edition should also include the editor's latest note.",
            "received_at_ms": None,
        }
    )
    received_at_ms = time_ns() // 1_000_000
    await peer.write({"inbound": reference.model_dump(mode="json")})
    while True:
        event = await persisted.get()
        if cast(dict[str, Any], event.payload)["metadata"]["text"] == reference.body:
            break
    row = await asyncio.to_thread(read)
    assert received_at_ms <= row["received_at_ms"] <= time_ns() // 1_000_000


async def steering(peer: Peer, draft: Path, persisted: asyncio.Queue[AuditEvent]):
    first, approval = await submit(peer, draft)
    path = Path(str(approval["approval"]["approval"]["details"]["path"]))
    updates = []
    for text in (
        "The editor has confirmed Friday as the deadline.",
        "Please also include the appendix in the final delivery note.",
    ):
        update = InboundMessage(
            id=str(uuid7()),
            target=first.target,
            sender=first.sender,
            body=text,
            mentions_agent=True,
            metadata={"review": "approved"},
        )
        updates.append(update)
        await peer.write({"inbound": update.model_dump(mode="json")})
        while True:
            event = await persisted.get()
            if cast(dict[str, Any], event.payload)["metadata"]["text"] == text:
                break
        if len(updates) == 1:
            await asyncio.sleep(1)
    while True:
        packet = await peer.until("event")
        peer.events.append(packet)
        if packet["event"]["provider_event"] == "inbox.checked":
            event = HarnessOutput.model_validate(packet["event"])
            assert isinstance(event.payload, ToolCallCompleted)
            assert event.turn_id == approval["approval"]["approval"]["turn_id"]
            detail = event.payload.call.detail
            assert isinstance(detail, CommandDetail)
            assert all(update.body in (detail.output or "") for update in updates)
            break
    entries = [
        json.loads(line)
        for line in (
            await asyncio.to_thread(path.with_suffix(".steered.jsonl").read_text)
        ).splitlines()
    ]
    assert entries[-1]["at_ns"] // 1_000_000 >= updates[-1].occurred_at_ms + 2_980
    saved = await finish(peer, approval)
    assert await asyncio.to_thread(saved.read_text)
    terminal = next(
        packet
        for packet in reversed(peer.events)
        if packet.get("event", {}).get("payload", {}).get("type") == "completed"
        and packet["event"]["turn_id"] == approval["approval"]["approval"]["turn_id"]
    )
    assert ChannelTarget.model_validate(terminal["target"]) == first.target.model_copy(
        update={"reply_to_message_id": first.id}
    )
    # A replay and then another request still answer the new request's identity.
    await peer.write({"inbound": first.model_dump(mode="json")})
    continued = await report(peer, draft)
    assert continued.parent == saved.parent


async def runtime_failures(peer: Peer, draft: Path, status: Callable[[], Activity]):
    """Complete reports after real write conflicts, fallback and final feedback."""

    for failures in (1, 2):
        _, packet = await submit(peer, draft)
        conflicts = []
        try:
            for attempt in range(failures):
                if attempt:
                    packet = await peer.until("approval")
                approval = ChannelApprovalRequest.model_validate(packet["approval"])
                path = Path(str(approval.approval.details["path"]))
                # An existing directory prevents this child process's real write.
                await asyncio.to_thread(path.mkdir)
                conflicts.append(path)
                await peer.write(
                    {
                        "id": packet["id"],
                        "decision": ApprovalResult(
                            request_id=approval.approval.request_id,
                            decision="approved",
                        ).model_dump(mode="json"),
                    }
                )
            if failures == 1:
                saved = await finish(peer)
                data = json.loads(await asyncio.to_thread(saved.read_text))
                assert data["words"] > 0
                assert saved.parent != conflicts[0].parent
                # the failed runtime is banned and the next one takes its place
                first, second = status().runtimes
                assert first.banned_until_ms and not first.in_use
                assert second.in_use
            else:
                feedback = await peer.until("outbound")
                terminal = HarnessOutput.model_validate(
                    next(
                        item["event"]
                        for item in reversed(peer.events)
                        if item.get("event", {}).get("payload", {}).get("type")
                        == "failed"
                    )
                )
                assert isinstance(terminal.payload, TurnFailed)
                assert terminal.payload.failure.message in feedback["outbound"]["body"]
                assert all(
                    part in feedback["outbound"]["body"]
                    for part in conflicts[-1].parts[-2:]
                )
                assert terminal.payload.failure.code is not None
        finally:
            for path in conflicts:
                await asyncio.to_thread(path.rmdir)
        saved = await report(peer, draft)
        assert json.loads(await asyncio.to_thread(saved.read_text))["words"] > 0


async def exercise(path: Path):
    inspection = await HarnessBuilderParameters.builder().inspect(path, timeout=30)
    assert inspection.available and inspection.version and inspection.skills
    await asyncio.to_thread(path.mkdir)
    os.environ["BCN_HOME"] = str(path)
    workspaces = {
        name: path / ".bcn" / "workspaces" / name
        for name in ("Cloud", "Tifa", "Barret")
    }
    drafts = {}
    for name, workspace in workspaces.items():
        await asyncio.to_thread(workspace.mkdir, parents=True)
        draft = workspace / "draft.txt"
        await asyncio.to_thread(
            draft.write_text,
            f"{name} owns the editorial draft. The report is due Friday.\nA chart is ready for review.",
        )
        drafts[name] = draft
    composition = await asyncio.to_thread(
        load,
        [
            ContribSpec(
                contrib="agent",
                config={
                    "agents": [
                        {
                            "id": name,
                            "name": name,
                            "mode": "dangerous_individual"
                            if name == "Tifa"
                            else "session",
                            "session": {
                                "config": {
                                    "sandbox": {
                                        "policy": "danger-full-access"
                                        if name == "Barret"
                                        else "workspace-write"
                                    }
                                }
                            },
                            "channel": [{"kind": "test-channel"}],
                            "harness": [
                                {
                                    "kind": "document",
                                    "title": f"{name} editorial report",
                                },
                                {
                                    "kind": "document",
                                    "title": "Additional editorial desk",
                                },
                            ],
                        }
                        for name in workspaces
                    ]
                },
            ),
            ContribSpec(contrib="test-channel-builder"),
            ContribSpec(contrib="secondary-channel-builder"),
            ContribSpec(contrib="document-builder"),
            ContribSpec(contrib="secondary-document-builder"),
            ContribSpec(contrib="agent-activity"),
        ],
    )

    ready = asyncio.Event()
    registries: list[AgentInstances] = []
    activities: list[AgentActivity] = []
    persisted: asyncio.Queue[AuditEvent] = asyncio.Queue()
    stopped: asyncio.Queue[AuditEvent] = asyncio.Queue()

    @asynccontextmanager
    async def registry(ctx: Context):
        registries.append(ctx.accept(AGENTS))
        activities.append(ctx.accept(AGENT_ACTIVITY))
        yield

    def changed(ctx: Context, change: ContribChange):
        ready.set()

    lock = asyncio.Lock()

    async def record(ctx: Context, event: AuditEvent):
        if event.event_name == "channel.inbound.persisted":
            persisted.put_nowait(event)
        elif event.event_name == "harness.session.stopped":
            stopped.put_nowait(event)
        async with lock, aiofiles.open(path / "audit.jsonl", "a") as stream:
            await stream.write(event.model_dump_json() + "\n")

    composition = await configured(path / "config.toml", composition)
    composition = composition.model_copy(
        update={
            "entries": (
                *composition.entries,
                ContribEntry(
                    name="agent-registry",
                    definition=Contrib(
                        accepts=[AGENTS, AGENT_ACTIVITY], lifespan=registry
                    ),
                ),
                ContribEntry(
                    name="ready", definition=Contrib(listen={CONTRIB_CHANGED: changed})
                ),
                ContribEntry(
                    name="audit-file", definition=Contrib(listen={AUDIT_EVENT: record})
                ),
            )
        }
    )
    async with AsyncExitStack() as peers:
        async with node(
            configer_options=(str(path / "config.toml"),), start=lambda _: Composition()
        ) as hub:

            async def applied(
                done: Callable[[], bool], tolerated: Collection[UUID] = ()
            ) -> None:
                while True:
                    ready.clear()
                    if done():
                        break
                    failures = [
                        record.state.error
                        for record in hub.snapshots
                        if isinstance(record.state, Failed)
                        and record.id not in tolerated
                    ]
                    if failures:
                        raise RuntimeError("; ".join(failures))
                    await ready.wait()
                for record in hub.snapshots:
                    assert isinstance(record.state, Running), record

            candidate = hub.validate(composition)
            async with hub.enable(candidate) as enabled:
                await applied(lambda: running(hub.snapshots, workspaces))
                connected = {}
                for name, workspace in workspaces.items():
                    peer = await connect(workspace / "channel.json", ready, hub)
                    peers.push_async_callback(peer.aclose)
                    peer.automatic = name != "Cloud"
                    connected[name] = peer

                def status(name: str) -> Activity:
                    return activities[0].snapshot()[name]

                async def settled(done: Callable[[], bool]) -> None:
                    while not done():  # noqa: ASYNC110
                        await asyncio.sleep(0.05)

                await settled(
                    lambda: all(
                        status(name).channels[0].connected_at_ms is not None
                        for name in workspaces
                    )
                )
                for name in workspaces:
                    channel = status(name).channels[0]
                    assert channel.state == "running" and not channel.failures
                    assert channel.identity is not None and channel.identity.id == name
                    assert status(name).started_at_ms
                    assert status(name).runtimes[0].in_use
                await connected["Tifa"].aclose()
                await settled(lambda: status("Tifa").channels[0].failures == 1)
                assert status("Tifa").channels[0].state == "reconnecting"
                assert status("Tifa").channels[0].error
                connected["Tifa"] = await connect(
                    workspaces["Tifa"] / "channel.json", ready, hub
                )
                peers.push_async_callback(connected["Tifa"].aclose)
                connected["Tifa"].automatic = True
                await settled(lambda: not status("Tifa").channels[0].failures)
                assert status("Tifa").channels[0].state == "running"
                await connected["Tifa"].write({"give_up": True})
                await connected["Tifa"].aclose()
                await settled(
                    lambda: status("Tifa").channels[0].state == "disconnected"
                )
                assert status("Tifa").channels[0].error
                connected["Tifa"] = await connect(
                    workspaces["Tifa"] / "channel.json", ready, hub
                )
                peers.push_async_callback(connected["Tifa"].aclose)
                connected["Tifa"].automatic = True
                await settled(lambda: status("Tifa").channels[0].state == "running")
                await inbound_context(
                    connected["Cloud"],
                    drafts["Cloud"],
                    Path(os.environ["BCN_HOME"]) / ".bcn" / "bcn.sqlite3",
                    persisted,
                )
                for name, peer in connected.items():
                    session = await session_routes(peer, drafts[name], workspaces[name])
                    if name == "Cloud":
                        await attachment_download(
                            peer,
                            drafts[name],
                            session,
                            Path(os.environ["BCN_HOME"]) / ".bcn" / "bcn.sqlite3",
                            persisted,
                        )
                    await turn_failure(peer, drafts[name], session)
                # Reminder commands and their later wake use the same active Actor session.
                for name, peer in connected.items():
                    _, packet = await submit(
                        peer,
                        drafts[name],
                        body="After the word-count report is ready, remind me to send this draft to the editor.",
                    )
                    first = await finish(peer, packet)
                    second = await finish(peer)
                    assert first.parent == second.parent

                agent_request = InboundMessage(
                    id=str(uuid7()),
                    target=ChannelTarget(kind="group", thread_id="automated-editor"),
                    sender=SenderIdentity(
                        id="editor-agent", name="Editorial agent", kind="agent"
                    ),
                    body="Could you prepare the draft's word-count report for the editorial archive?",
                    mentions_agent=True,
                    metadata={"review": "approved"},
                )
                await connected["Cloud"].write(
                    {"inbound": agent_request.model_dump(mode="json")}
                )
                while True:
                    packet = await connected["Cloud"].until("event")
                    connected["Cloud"].events.append(packet)
                    if packet["event"]["payload"]["type"] == "completed":
                        break
                ending = packet["event"]["turn_id"]
                assert any(
                    "declined" in item["event"]["payload"]["text"]
                    for item in connected["Cloud"].events
                    if item.get("event", {}).get("turn_id") == ending
                    and item["event"]["payload"]["type"] == "content.delta"
                )
                await steering(connected["Cloud"], drafts["Cloud"], persisted)

                # Update the installed default entry while an existing approval remains live.
                primary = connected["Cloud"]
                _, pending = await submit(primary, drafts["Cloud"])
                working = status("Cloud").working
                assert working is not None and working.since_ms
                # the turn under way is named, with when that was said
                assert status("Cloud").turns
                assert status("Cloud").said_at_ms
                identity = registries[0].running["Cloud"]
                entry = next(
                    entry for entry in composition.entries if entry.name == "agent"
                )
                config = deepcopy(entry.config)
                config["agents"][0]["channel"].append(
                    {
                        "kind": "test-channel-secondary",
                        "endpoint_file": "secondary.json",
                    }
                )
                added = composition.model_copy(
                    update={
                        "entries": tuple(
                            item.model_copy(update={"config": config})
                            if item.name == "agent"
                            else item
                            for item in composition.entries
                        )
                    }
                )
                await enabled.update(added)
                await applied(
                    lambda: running(
                        hub.snapshots, ["Cloud/channel:test-channel-secondary:0"]
                    )
                )
                assert registries[0].running["Cloud"] is identity
                secondary = await connect(
                    workspaces["Cloud"] / "secondary.json", ready, hub
                )
                peers.push_async_callback(secondary.aclose)
                await report(secondary, drafts["Cloud"])
                first = await finish(primary, pending)
                continued = await report(primary, drafts["Cloud"])
                assert first.parent == continued.parent

                # Retain already admitted work while the other Channel keeps operating.
                _, pending = await submit(primary, drafts["Cloud"])
                queued = InboundMessage(
                    id=str(uuid7()),
                    target=ChannelTarget(kind="group", thread_id="editorial-team"),
                    sender=SenderIdentity(id="editor", name="Editor"),
                    body="The editor also needs a short report for the appendix.",
                    attachments=(
                        Attachment(name="appendix.txt", path=drafts["Cloud"]),
                    ),
                    mentions_agent=True,
                    metadata={"review": "approved"},
                )
                await primary.write({"inbound": queued.model_dump(mode="json")})
                while True:
                    event = await persisted.get()
                    if (
                        cast(dict[str, Any], event.payload)["metadata"]["text"]
                        == queued.body
                    ):
                        break
                config = deepcopy(config)
                config["agents"][0]["channel"] = [config["agents"][0]["channel"][1]]
                removed = added.model_copy(
                    update={
                        "entries": tuple(
                            item.model_copy(update={"config": config})
                            if item.name == "agent"
                            else item
                            for item in added.entries
                        )
                    }
                )
                await enabled.update(removed)
                await report(secondary, drafts["Cloud"])
                saved = await finish(primary, pending)
                await applied(
                    lambda: not named(hub.snapshots, ["Cloud/channel:test-channel:0"])
                )
                reads = await asyncio.gather(
                    *(
                        asyncio.to_thread(item.read_text)
                        for item in await asyncio.to_thread(
                            list, saved.parent.glob(f"{saved.stem}.*json*")
                        )
                    )
                )
                assert any(queued.body in value for value in reads)
                assert await asyncio.to_thread(
                    (workspaces["Cloud"] / "channel.closed").read_text
                )
                await report(secondary, drafts["Cloud"])
                assert registries[0].running["Cloud"] is identity
                connected["Cloud"] = secondary

                # Retire an active Harness after its admitted queue finishes.
                _, pending = await submit(secondary, drafts["Cloud"])
                queued = queued.model_copy(
                    update={
                        "id": str(uuid7()),
                        "body": "Please include a short report for the revised appendix too.",
                    }
                )
                await secondary.write({"inbound": queued.model_dump(mode="json")})
                while True:
                    event = await persisted.get()
                    if (
                        cast(dict[str, Any], event.payload)["metadata"]["text"]
                        == queued.body
                    ):
                        break
                config = deepcopy(config)
                config["agents"][0]["harness"][0]["title"] = "Revised editorial report"
                revised = removed.model_copy(
                    update={
                        "entries": tuple(
                            item.model_copy(update={"config": config})
                            if item.name == "agent"
                            else item
                            for item in removed.entries
                        )
                    }
                )
                before = {record.id for record in hub.snapshots}
                await enabled.update(revised)
                await report(connected["Tifa"], drafts["Tifa"])
                # A conversation arriving before the restart runs on the runtime
                # as it was, and its session ends with that runtime. The waiting
                # turn shares the peer, so only this session's events count.
                _, interim = await submit(
                    secondary,
                    drafts["Cloud"],
                    ChannelTarget(kind="group", thread_id="interim-desk"),
                )
                approval = ChannelApprovalRequest.model_validate(interim["approval"])
                await secondary.write(
                    {
                        "id": interim["id"],
                        "decision": ApprovalResult(
                            request_id=approval.approval.request_id,
                            decision="approved",
                        ).model_dump(mode="json"),
                    }
                )
                while True:
                    packet = await secondary.until("event")
                    secondary.events.append(packet)
                    event = HarnessOutput.model_validate(packet["event"])
                    if (
                        event.session_id == approval.approval.session_id
                        and event.payload.type == "completed"
                    ):
                        break
                data = json.loads(
                    await asyncio.to_thread(
                        Path(str(approval.approval.details["path"])).read_text
                    )
                )
                assert data["title"] == "Cloud editorial report"
                old = await finish(secondary, pending)
                await applied(
                    lambda: (
                        running(hub.snapshots, ["Cloud/harness:document:0"])
                        and all(
                            record.id not in before
                            for record in named(
                                hub.snapshots, ["Cloud/harness:document:0"]
                            )
                        )
                    )
                )
                reads = await asyncio.gather(
                    *(
                        asyncio.to_thread(item.read_text)
                        for item in await asyncio.to_thread(
                            list, old.parent.glob(f"{old.stem}.*json*")
                        )
                    )
                )
                assert any(queued.body in value for value in reads)
                new = await report(secondary, drafts["Cloud"])
                assert old.parent != new.parent
                data = json.loads(await asyncio.to_thread(new.read_text))
                assert data["title"] == "Revised editorial report"
                await settled(lambda: status("Cloud").working is None)
                assert not status("Cloud").turns

                # Reordering runtimes of different kinds keeps both running: the
                # existing conversation stays where it is, a new one takes the
                # first.
                order_workspace = path / ".bcn" / "workspaces" / "Order"
                await asyncio.to_thread(order_workspace.mkdir)
                order_draft = order_workspace / "draft.txt"
                await asyncio.to_thread(
                    order_draft.write_text, "An editorial note for the order desk."
                )
                config = deepcopy(config)
                config["agents"].append(
                    {
                        "id": "Order",
                        "name": "Order",
                        "session": {
                            "config": {"sandbox": {"policy": "danger-full-access"}}
                        },
                        "channel": [{"kind": "test-channel"}],
                        "harness": [
                            {"kind": "document", "title": "First desk report"},
                            {
                                "kind": "document-secondary",
                                "title": "Second desk report",
                            },
                        ],
                    }
                )
                ordered = revised.model_copy(
                    update={
                        "entries": tuple(
                            item.model_copy(update={"config": config})
                            if item.name == "agent"
                            else item
                            for item in revised.entries
                        )
                    }
                )
                await enabled.update(ordered)
                await applied(lambda: running(hub.snapshots, ["Order"]))
                order = await connect(order_workspace / "channel.json", ready, hub)
                peers.push_async_callback(order.aclose)
                order.automatic = True
                saved = await report(order, order_draft)
                data = json.loads(await asyncio.to_thread(saved.read_text))
                assert data["title"] == "First desk report"
                runtimes = [
                    "Order/harness:document:0",
                    "Order/harness:document-secondary:0",
                ]
                before = {record.id for record in named(hub.snapshots, runtimes)}
                config = deepcopy(config)
                config["agents"][-1]["harness"].reverse()
                ordered = ordered.model_copy(
                    update={
                        "entries": tuple(
                            item.model_copy(update={"config": config})
                            if item.name == "agent"
                            else item
                            for item in ordered.entries
                        )
                    }
                )
                await enabled.update(ordered)
                await settled(lambda: status("Order").runtimes[0].in_use)
                assert status("Order").runtimes[0].kind == "document-secondary"
                assert {record.id for record in named(hub.snapshots, runtimes)} == (
                    before
                )
                saved = await report(order, order_draft)
                data = json.loads(await asyncio.to_thread(saved.read_text))
                assert data["title"] == "First desk report"
                saved = await report(
                    order,
                    order_draft,
                    ChannelTarget(kind="group", thread_id="order-desk"),
                )
                data = json.loads(await asyncio.to_thread(saved.read_text))
                assert data["title"] == "Second desk report"

                # A changed channel waits for its agent to finish; another agent's
                # change does not wait for it.
                _, pending = await submit(secondary, drafts["Cloud"])
                endpoint = workspaces["Cloud"] / "secondary.json"
                channel = ["Cloud/channel:test-channel-secondary:0"]
                before = {record.id for record in named(hub.snapshots, channel)}
                waiting = {
                    record.id
                    for record in named(hub.snapshots, ["Tifa/harness:document:0"])
                }
                config = deepcopy(config)
                for item in config["agents"]:
                    if item["id"] == "Cloud":
                        item["channel"][0]["token"] = "rotated"
                    elif item["id"] == "Tifa":
                        item["harness"][0]["title"] = "Tifa revised report"
                ordered = ordered.model_copy(
                    update={
                        "entries": tuple(
                            item.model_copy(update={"config": config})
                            if item.name == "agent"
                            else item
                            for item in ordered.entries
                        )
                    }
                )
                await enabled.update(ordered)
                await applied(
                    lambda: (
                        running(hub.snapshots, ["Tifa/harness:document:0"])
                        and all(
                            record.id not in waiting
                            for record in named(
                                hub.snapshots, ["Tifa/harness:document:0"]
                            )
                        )
                    )
                )
                saved = await report(connected["Tifa"], drafts["Tifa"])
                data = json.loads(await asyncio.to_thread(saved.read_text))
                assert data["title"] == "Tifa revised report"
                assert {record.id for record in named(hub.snapshots, channel)} == (
                    before
                )
                await finish(secondary, pending)
                await applied(
                    lambda: (
                        running(hub.snapshots, channel)
                        and all(
                            record.id not in before
                            for record in named(hub.snapshots, channel)
                        )
                    )
                )
                secondary = await connect(endpoint, ready, hub)
                peers.push_async_callback(secondary.aclose)
                connected["Cloud"] = secondary
                await settled(
                    lambda: (
                        status("Cloud").channels[0].connected_at_ms is not None
                        and not status("Cloud").channels[0].failures
                    )
                )

                # An idle timer releases the session after active approval and reply finish.
                idle_workspace = path / ".bcn" / "workspaces" / "Idle"
                await asyncio.to_thread(idle_workspace.mkdir)
                idle_draft = idle_workspace / "draft.txt"
                await asyncio.to_thread(
                    idle_draft.write_text, "An editorial note for the weekend edition."
                )
                config = deepcopy(config)
                config["agents"].append(
                    {
                        "id": "Idle",
                        "name": "Idle",
                        "idle_timeout": 0.2,
                        "channel": [{"kind": "test-channel"}],
                        "harness": [{"kind": "document", "publication_delay": 1.2}],
                    }
                )
                batch_workspace = path / ".bcn" / "workspaces" / "Batch"
                context_workspace = path / ".bcn" / "workspaces" / "Context"
                failure_workspace = path / ".bcn" / "workspaces" / "Failure"
                for directory in (
                    batch_workspace,
                    context_workspace,
                    failure_workspace,
                ):
                    await asyncio.to_thread(directory.mkdir)
                    await asyncio.to_thread(
                        (directory / "draft.txt").write_text,
                        "The archive needs a report for this editorial draft.",
                    )
                await asyncio.to_thread(
                    (context_workspace / "STYLE.md").write_text,
                    "Use sentence-case headings.",
                )
                config["agents"].extend(
                    [
                        {
                            "id": "Batch",
                            "name": "Batch",
                            "channel": [{"kind": "test-channel"}],
                            "harness": [{"kind": "document", "input_mode": "batch"}],
                        },
                        {
                            "id": "Context",
                            "name": "Context",
                            "channel": [{"kind": "test-channel"}],
                            "harness": [{"kind": "document", "publication_delay": 1.2}],
                        },
                        {
                            "id": "Failure",
                            "name": "Failure",
                            "channel": [{"kind": "test-channel"}],
                            "harness": [{"kind": "document"}],
                        },
                    ]
                )
                idle_config = revised.model_copy(
                    update={
                        "entries": tuple(
                            item.model_copy(update={"config": config})
                            if item.name == "agent"
                            else item
                            for item in revised.entries
                        )
                    }
                )
                await enabled.update(idle_config)
                await applied(
                    lambda: running(
                        hub.snapshots, ["Idle", "Batch", "Context", "Failure"]
                    )
                )
                idle = await connect(idle_workspace / "channel.json", ready, hub)
                peers.push_async_callback(idle.aclose)
                _, packet = await submit(idle, idle_draft)
                await report(connected["Tifa"], drafts["Tifa"])
                first = await finish(idle, packet)
                while (await stopped.get()).context.session_id != first.parent.name:
                    pass
                assert await asyncio.to_thread(
                    (first.parent / "session.closed").read_text
                )
                assert await asyncio.to_thread(
                    first.with_suffix(".published.json").read_text
                )
                second = await report(idle, idle_draft)
                assert first.parent != second.parent

                batch_peer = await connect(batch_workspace / "channel.json", ready, hub)
                context_peer = await connect(
                    context_workspace / "channel.json", ready, hub
                )
                peers.push_async_callback(batch_peer.aclose)
                peers.push_async_callback(context_peer.aclose)
                message, packet = await submit(
                    batch_peer, batch_workspace / "draft.txt"
                )
                queued = message.model_copy(
                    update={
                        "id": str(uuid7()),
                        "body": "Please save a separate report for the appendix too.",
                    }
                )
                await batch_peer.write({"inbound": queued.model_dump(mode="json")})
                additional = queued.model_copy(
                    update={
                        "id": str(uuid7()),
                        "sender": SenderIdentity(id="copy-editor", name="Copy Editor"),
                        "body": "The appendix is ready. Please include its word count in the report too.",
                    }
                )
                await batch_peer.write({"inbound": additional.model_dump(mode="json")})
                while True:
                    event = await persisted.get()
                    if (
                        cast(dict[str, Any], event.payload)["metadata"]["text"]
                        == additional.body
                    ):
                        break
                await asyncio.sleep(3.2)
                previous = await finish(batch_peer, packet)
                pending = await batch_peer.until("approval")
                assert pending["approval"]["sender_id"] == queued.sender.id
                assert pending["approval"]["target"]["reply_to_message_id"] == queued.id
                following = await finish(batch_peer, pending)
                assert previous.parent == following.parent

                _, packet = await submit(context_peer, context_workspace / "draft.txt")
                style = "Use the editor's revised title style."
                await asyncio.to_thread(
                    (context_workspace / "STYLE.md").write_text, style
                )
                # The provider publishes its cache through a real owned file.
                while (  # noqa: ASYNC110
                    (
                        await asyncio.to_thread(
                            read_context_cache,
                            context_workspace / "document-context.json",
                        )
                    )["style"]
                    != style
                ):
                    await asyncio.sleep(0.02)
                first = await finish(context_peer, packet)
                second = await report(context_peer, context_workspace / "draft.txt")
                assert first.parent == second.parent
                for saved in (first, second):
                    # Publication is performed by a separate process.
                    while not await asyncio.to_thread(  # noqa: ASYNC110
                        saved.with_suffix(".published.json").exists
                    ):
                        await asyncio.sleep(0.02)
                    published = json.loads(
                        await asyncio.to_thread(
                            saved.with_suffix(".published.json").read_text
                        )
                    )
                    assert published["report"]["words"] > 0
                    assert "msg=" in published["history"]
                refreshed = await report(context_peer, context_workspace / "draft.txt")
                assert refreshed.parent != first.parent
                assert (
                    json.loads(await asyncio.to_thread(refreshed.read_text))["style"]
                    == style
                )
                await asyncio.to_thread((refreshed.parent / "session.json").unlink)
                recovered = await report(context_peer, context_workspace / "draft.txt")
                assert recovered.parent == refreshed.parent
                assert await asyncio.to_thread(
                    (recovered.parent / "session.json").read_text
                )

                failure_peer = await connect(
                    failure_workspace / "channel.json", ready, hub
                )
                peers.push_async_callback(failure_peer.aclose)
                saved = await report(failure_peer, failure_workspace / "draft.txt")
                provider = next(
                    record.id
                    for record in hub.snapshots
                    if record.name == "Failure/harness:document:0"
                )
                cache = failure_workspace / "document-context.json"
                await asyncio.to_thread(cache.unlink)
                await asyncio.to_thread(cache.mkdir)
                await asyncio.to_thread(
                    (failure_workspace / "STYLE.md").write_text,
                    "Use the editor's updated publication style.",
                )
                while True:
                    ready.clear()
                    failures = [
                        record.state
                        for record in hub.snapshots
                        if record.parent == provider
                        and isinstance(record.state, (Failed, Blocked))
                    ]
                    if (
                        any(isinstance(state, Failed) for state in failures)
                        and any(isinstance(state, Blocked) for state in failures)
                        and all(state.phase == "stopped" for state in failures)
                    ):
                        break
                    await ready.wait()
                assert await asyncio.to_thread(
                    (saved.parent / "session.closed").read_text
                )
                await report(connected["Tifa"], drafts["Tifa"])
                config = deepcopy(config)
                config["agents"] = [
                    item for item in config["agents"] if item["id"] != "Failure"
                ]
                await enabled.update(
                    idle_config.model_copy(
                        update={
                            "entries": tuple(
                                item.model_copy(update={"config": config})
                                if item.name == "agent"
                                else item
                                for item in idle_config.entries
                            )
                        }
                    )
                )
                await applied(
                    lambda: (
                        not named(hub.snapshots, ["Failure"])
                        and all(record.parent != provider for record in hub.snapshots)
                    ),
                    [
                        record.id
                        for record in hub.snapshots
                        if record.parent == provider
                    ],
                )
                assert await asyncio.to_thread(
                    (failure_workspace / "channel.closed").read_text
                )

                await runtime_failures(
                    connected["Cloud"], drafts["Cloud"], lambda: status("Cloud")
                )

                # Stop with both an unacknowledged native send and a live approval.
                _, pending = await submit(secondary, drafts["Cloud"])
                approval = ChannelApprovalRequest.model_validate(pending["approval"])
                pending_path = Path(str(approval.approval.details["path"]))
                other = InboundMessage(
                    id=str(uuid7()),
                    target=ChannelTarget(kind="group", thread_id="editorial-team"),
                    sender=SenderIdentity(id="editor", name="Editor"),
                    body="Please save the final word-count report for the weekend draft.",
                    attachments=(
                        Attachment(name=drafts["Tifa"].name, path=drafts["Tifa"]),
                    ),
                    mentions_agent=True,
                    metadata={"review": "approved"},
                )
                await connected["Tifa"].write(
                    {"inbound": other.model_dump(mode="json")}
                )
                await connected["Tifa"].until("outbound", acknowledge=False)
                other_path = Path(
                    next(
                        packet["event"]["payload"]["call"]["detail"]["changes"][0][
                            "path"
                        ]
                        for packet in reversed(connected["Tifa"].events)
                        if packet.get("event", {})
                        .get("payload", {})
                        .get("call", {})
                        .get("name")
                        == "file.write"
                    )
                )
            for peer in connected.values():
                remaining = await peer.reader.read()
                peer.events.extend(json.loads(line) for line in remaining.splitlines())
            assert any(
                packet["event"]["payload"]["type"] == "cancelled"
                for packet in connected["Cloud"].events
                if "event" in packet
            )
        assert await asyncio.to_thread(pending_path.with_suffix(".closed").read_text)
        assert await asyncio.to_thread(other_path.with_suffix(".closed").read_text)
        for workspace in workspaces.values():
            assert await asyncio.to_thread((workspace / "channel.closed").read_text)
            sessions = await asyncio.to_thread(list, (workspace / "reports").iterdir())
            for session in sessions:
                assert await asyncio.to_thread((session / "session.closed").read_text)
    assert await asyncio.to_thread((idle_workspace / "channel.closed").read_text)
    assert await asyncio.to_thread((workspaces["Cloud"] / "secondary.closed").read_text)
    async with aiofiles.open(path / "audit.jsonl") as stream:
        records = [TypeAdapter(AuditEvent).validate_json(line) async for line in stream]
    assert {"Cloud", "Tifa", "Barret"} <= {event.context.agent_id for event in records}
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
                assert isinstance(output.payload, ToolCallCompleted)
                if output.payload.call.name == "file.read":
                    reads.append((event, output))
        assert reads
        for event, output in reads:
            assert isinstance(output.payload, ToolCallCompleted)
            detail = output.payload.call.detail
            assert isinstance(detail, FileDetail) and detail.path is not None
            assert await asyncio.to_thread(Path(detail.path).read_text) == content
            assert output.payload.call.duration_ms is not None
            assert output.session_id == event.context.session_id
            assert output.turn_id == event.context.turn_id
    # The native message stays in the runtime's own events; each tool event
    # names its call, and each turn event the runtime connection serving it.
    for event in records:
        if not event.event_name.startswith("harness."):
            continue
        assert "raw" not in cast(dict[str, Any], event.payload)
        if event.context.turn_id is not None:
            assert event.context.connection in {
                "document:0",
                "document:1",
                "document-secondary:0",
            }
        if event.event_name.startswith("harness.tool."):
            output = HarnessOutput.model_validate(event.payload)
            assert isinstance(
                output.payload, (ToolCallStarted, ToolCallCompleted, ToolCallFailed)
            )
            assert event.context.call_id == output.payload.call.call_id
    models = [
        HarnessOutput.model_validate(event.payload).payload
        for event in records
        if event.event_name == "harness.model.updated"
    ]
    assert models and all(isinstance(model, ModelUpdated) for model in models)
    # A banned runtime names the one the turn goes on with, and the turn's
    # next events come from it; nothing is named when none is left.
    banned = [event for event in records if event.event_name == "harness.pool.banned"]
    assert any(
        cast(dict[str, Any], event.payload)["fallback"] is None for event in banned
    )
    for event in banned:
        fallback = cast(dict[str, Any], event.payload)["fallback"]
        if fallback is None:
            continue
        retried = next(
            later
            for later in records
            if later.sequence > event.sequence
            and later.context.turn_id == event.context.turn_id
        )
        assert retried.context.connection == fallback != event.context.connection
    # A reply sent from a turn names the turn, its session and the message it
    # answers: one the channel brought in, or one a reminder wrote.
    turns = {
        (event.context.session_id, event.context.turn_id)
        for event in records
        if event.event_name == "harness.turn.requested"
    }
    answered = {
        event.context.message_id
        for event in records
        if event.event_name in {"channel.inbound.persisted", "reminder.fired"}
    }
    assert None not in answered
    outbound = [
        event
        for event in records
        if event.event_name in {"channel.outbound.pending", "channel.outbound.sent"}
    ]
    assert outbound
    for event in outbound:
        assert (event.context.session_id, event.context.turn_id) in turns
        metadata = cast(dict[str, Any], event.payload)["metadata"]
        assert metadata["reply_to_message_id"] in answered
    # The turn a reminder starts and the reminder's firing name one message.
    fired = [event for event in records if event.event_name == "reminder.fired"]
    assert fired
    for event in fired:
        assert any(
            request.context.message_id == event.context.message_id
            for request in requests.values()
        )
    for event in records:
        if event.event_name == "harness.content.delta":
            request = requests[event.context.turn_id]
            assert event.run_id == request.run_id
            assert event.sequence > request.sequence
            assert event.monotonic_ns >= request.monotonic_ns
            assert event.context.thread_id == request.context.thread_id
    assert any(event.event_name == "approval.decided" for event in records)
