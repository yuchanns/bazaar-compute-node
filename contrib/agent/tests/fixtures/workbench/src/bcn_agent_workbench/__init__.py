"""Independent providers using only public core, channel and harness packages."""

# Provider methods implement the public Harness call budgets.
# ruff: noqa: ASYNC109

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import aclosing, asynccontextmanager
from functools import partial
from pathlib import Path
from time import time_ns
from typing import Any, ClassVar, Literal, cast
from uuid import NAMESPACE_URL, uuid5, uuid7

from bazaar_contrib_channel import (
    CHANNEL,
    CHANNEL_BUILDERS,
    CHANNEL_INPUT,
    ChannelApprovalRequest,
    ChannelBuilderConfig,
    ChannelConnectionError,
    ChannelDeliveryReceipt,
    ChannelError,
    ChannelErrorDetail,
    ChannelIdentity,
    ChannelInput,
    ChannelSendAttempt,
    ChannelSendPart,
    ChannelSendRequest,
    ChannelTarget,
    ChannelTargetPresentation,
    DmAddress,
    DmRequest,
    InboundAttachment,
    InboundMessage,
    SenderIdentity,
    channel_builder_key,
)
from bazaar_contrib_harness import (
    HARNESS,
    HARNESS_BUILDERS,
    HARNESS_INPUT,
    ApprovalHandler,
    ApprovalRequest,
    ApprovalResult,
    HarnessBuilderConfig,
    HarnessDescription,
    HarnessExpire,
    HarnessInput,
    HarnessOutput,
    HarnessSession,
    HarnessSessionReconciliation,
    HarnessSessionUnavailable,
    HarnessSkill,
    HarnessTurn,
    ProviderCallResult,
    ProviderCallStatus,
    SessionInput,
    harness_builder_key,
)
from bazaar_core import Context, Contrib
from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class ChannelConfig(BaseModel):
    kind: ClassVar[str] = "test-channel"
    model_config = ConfigDict(extra="forbid", frozen=True)
    endpoint_file: str = "channel.json"


class Attachment(BaseModel):
    """A source file supplied by the TCP control peer."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    kind: str = "file"
    state: Literal["ready", "failed"] = "ready"
    path: Path | None = None
    gate: str | None = None
    media_type: str | None = None
    error: str | None = None

    @model_validator(mode="after")
    def source(self):
        if self.state == "ready" and self.path is None:
            raise ValueError("a ready attachment requires a local source")
        if self.state == "failed" and self.path is not None:
            raise ValueError("a failed attachment has no local source")
        return self


async def file_chunks(
    path: Path, gate: asyncio.Event | None = None
) -> AsyncGenerator[bytes]:
    """Read the peer's source lazily through the public attachment contract."""
    if gate is not None:
        await gate.wait()
    stream = await asyncio.to_thread(path.open, "rb")
    try:
        while chunk := await asyncio.to_thread(stream.read, 64 * 1024):
            yield chunk
    finally:
        await asyncio.to_thread(stream.close)


class ChannelMessage(BaseModel):
    """The test peer's wire message before the provider assigns local identities."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    target: ChannelTarget
    sender: SenderIdentity
    body: str
    message_type: str = "text"
    target_presentation: ChannelTargetPresentation | None = None
    reply_to_message_id: str | None = None
    received_at_ms: int | None = None
    provider_payload_ref: str | None = None
    attachments: tuple[Attachment, ...] = ()
    occurred_at_ms: int | None = Field(default_factory=lambda: time_ns() // 1_000_000)
    provider_chat_id: str | None = None
    notifies_runtime: bool = True
    mentions_agent: bool = False
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class TestChannel:
    """A connected TCP channel controlled by a peer that observes and approves."""

    def __init__(self, input: ChannelInput, group: asyncio.TaskGroup, kind: str):
        self.input = input
        self.group = group
        self.kind = kind
        self.writer: asyncio.StreamWriter | None = None
        self.connected = asyncio.Event()
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue()
        self.outbound: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self.downloads: dict[str, asyncio.Event] = {}

    def get_identity(self) -> ChannelIdentity:
        return ChannelIdentity(id=self.input.agent_id, name=self.input.agent_name)

    def dm_address(self, request: DmRequest) -> DmAddress | None:
        if request.sender_id is None:
            return None
        identity = f"{self.kind}:{self.input.agent_id}:dm:{request.sender_id}"
        return DmAddress(
            channel_session_id=str(uuid5(NAMESPACE_URL, identity + ":channel")),
            thread_id=str(uuid5(NAMESPACE_URL, identity + ":thread")),
            provider_thread_id=request.sender_id,
            channel_identity=request.channel_identity,
        )

    def connect(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.writer = writer
        self.connected.set()
        self.group.create_task(self.connection(reader, writer))

    async def connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        try:
            while data := await reader.readline():
                packet = json.loads(data)
                if "inbound" in packet:
                    message = ChannelMessage.model_validate(packet["inbound"])
                    identity = f"{self.kind}:{self.input.agent_id}:{message.target.kind}:{message.target.thread_id}"
                    await self.inbound.put(
                        InboundMessage(
                            **message.model_dump(exclude={"id", "attachments"}),
                            attachments=tuple(
                                InboundAttachment(
                                    **attachment.model_dump(exclude={"path", "gate"}),
                                    source=aclosing(
                                        file_chunks(
                                            cast(Path, attachment.path),
                                            self.downloads.setdefault(
                                                attachment.gate, asyncio.Event()
                                            )
                                            if attachment.gate is not None
                                            else None,
                                        )
                                    )
                                    if attachment.state == "ready"
                                    else None,
                                )
                                for attachment in message.attachments
                            ),
                            id=str(
                                uuid5(
                                    NAMESPACE_URL,
                                    f"{self.kind}:{self.input.agent_id}:message:{message.id}",
                                )
                            ),
                            provider_message_id=message.id,
                            channel_session_id=str(
                                uuid5(NAMESPACE_URL, identity + ":channel")
                            ),
                            thread_id=str(uuid5(NAMESPACE_URL, identity + ":thread")),
                        )
                    )
                elif "release_attachment" in packet:
                    self.downloads.pop(packet["release_attachment"]).set()
                else:
                    pending = self.pending.pop(packet["id"], None)
                    if pending is not None and not pending.done():
                        pending.set_result(packet)
        finally:
            if self.writer is writer:
                self.writer = None
                self.connected.clear()
                for pending in self.pending.values():
                    if not pending.done():
                        pending.set_exception(
                            ChannelConnectionError(
                                "channel peer disconnected before acknowledgement",
                                phase="acknowledge",
                            )
                        )
                self.pending.clear()
            writer.close()
            await writer.wait_closed()

    async def pump(self):
        while True:
            packet = await self.outbound.get()
            while self.writer is None:
                await self.connected.wait()
            writer = self.writer
            writer.write(json.dumps(packet).encode() + b"\n")
            await writer.drain()

    async def receive(self) -> AsyncIterator[InboundMessage]:
        while True:
            yield await self.inbound.get()

    def accept_turn_event(self, event: HarnessOutput, *, target: ChannelTarget):
        self.outbound.put_nowait(
            {
                "event": event.model_dump(mode="json"),
                "target": target.model_dump(mode="json"),
            }
        )

    async def prepare_send(
        self, request: ChannelSendRequest
    ) -> tuple[ChannelSendPart, ...]:
        requests = [
            request.model_copy(
                update={"body": request.body[start : start + 4096], "attachments": ()}
            )
            for start in range(0, len(request.body), 4096)
        ]
        requests.extend(
            request.model_copy(update={"body": "", "attachments": (attachment,)})
            for attachment in request.attachments
        )
        return tuple(
            ChannelSendPart(
                send=partial(self.send_part, request, index, len(requests)),
                message=request,
            )
            for index, request in enumerate(requests)
        )

    async def send_part(
        self,
        request: ChannelSendRequest,
        index: int,
        count: int,
        attempt: ChannelSendAttempt,
    ) -> ChannelDeliveryReceipt:
        writer = self.writer
        if writer is None or writer.is_closing():
            raise ChannelConnectionError(
                "channel peer is disconnected", phase="connect", delivery="not_sent"
            )
        id = str(uuid7())
        self.pending[id] = asyncio.get_running_loop().create_future()
        try:
            writer.write(
                json.dumps(
                    {
                        "id": id,
                        "operation_id": attempt.id,
                        "attempt": attempt.number,
                        "part": {"index": index, "count": count},
                        "outbound": request.model_dump(mode="json"),
                    }
                ).encode()
                + b"\n"
            )
            await writer.drain()
            packet = await self.pending[id]
        finally:
            self.pending.pop(id, None)
        if "error" in packet:
            detail = ChannelErrorDetail.model_validate(packet["error"])
            error = ChannelError(
                detail.message,
                phase=detail.phase,
                delivery=detail.delivery,
                retryable=detail.retryable,
                code=detail.code,
                retry_after=detail.retry_after,
            )
            error.detail = detail
            raise error
        return (
            ChannelDeliveryReceipt.model_validate(packet["receipt"])
            if "receipt" in packet
            else ChannelDeliveryReceipt(status="confirmed", provider_message_id=id)
        )

    async def request_approval(
        self, request: ChannelApprovalRequest, *, timeout: float
    ) -> ApprovalResult:
        id = request.approval.request_id
        self.pending[id] = asyncio.get_running_loop().create_future()
        await self.outbound.put({"id": id, "approval": request.model_dump(mode="json")})
        try:
            async with asyncio.timeout(timeout):
                packet = await self.pending[id]
            return ApprovalResult.model_validate(packet["decision"])
        finally:
            self.pending.pop(id, None)


@asynccontextmanager
async def channel_lifespan(ctx: Context, config: ChannelConfig):
    input = ctx.accept(CHANNEL_INPUT)
    async with asyncio.TaskGroup() as group:
        channel = TestChannel(input, group, config.kind)
        server = await asyncio.start_server(channel.connect, "127.0.0.1", 0)
        pump = group.create_task(channel.pump())
        endpoint = input.workspace / config.endpoint_file
        temporary = endpoint.with_suffix(".tmp")
        await asyncio.to_thread(
            temporary.write_text,
            json.dumps(
                {
                    "port": server.sockets[0].getsockname()[1],
                    "agent_id": input.agent_id,
                }
            ),
        )
        await asyncio.to_thread(temporary.replace, endpoint)
        ctx.offer(CHANNEL, channel)
        try:
            yield
        finally:
            server.close()
            if channel.writer is not None:
                channel.writer.close()
                await channel.writer.wait_closed()
            pump.cancel()
            await server.wait_closed()
            await asyncio.to_thread(
                endpoint.with_suffix(".closed").write_text, input.agent_id
            )


channel = Contrib(
    config=ChannelConfig,
    accepts=[CHANNEL_INPUT],
    offers=[CHANNEL],
    lifespan=channel_lifespan,
)


class ChannelBuilderParameters(ChannelBuilderConfig):
    kind: ClassVar[str] = "test-channel"
    entity_model: ClassVar[type[BaseModel]] = ChannelConfig
    entry: ClassVar[Contrib[Any]] = channel
    capabilities: ClassVar[tuple[str, ...]] = ("receive", "send", "approval")


@asynccontextmanager
async def channel_builder_lifespan(ctx: Context, config: ChannelBuilderParameters):
    with ctx.accept(CHANNEL_BUILDERS).register(config.builder()) as builder:
        ctx.offer(channel_builder_key(builder.kind), builder)
        yield


channel_builder = Contrib(
    config=ChannelBuilderParameters,
    accepts=[CHANNEL_BUILDERS],
    offers=[channel_builder_key("test-channel")],
    lifespan=channel_builder_lifespan,
)


class HarnessConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    title: str = "Document report"
    input_mode: Literal["interactive", "batch"] = "interactive"
    publication_delay: float = Field(default=0, ge=0)
    env: dict[str, str] = Field(default_factory=dict)


class DocumentStream:
    def __init__(
        self,
        process: asyncio.subprocess.Process,
        approval: ApprovalHandler,
        timeout: float,
    ):
        self.process = process
        self.approval = approval
        self.timeout = timeout
        self.stdin = cast(asyncio.StreamWriter, process.stdin)
        self.stdout = cast(asyncio.StreamReader, process.stdout)
        self.stderr = cast(asyncio.StreamReader, process.stderr)
        self.events: asyncio.Queue[HarnessOutput | Exception | None] = asyncio.Queue()
        self.decisions: set[asyncio.Task[None]] = set()
        self.pending: dict[str, asyncio.Future[bool]] = {}
        self.reader = asyncio.create_task(self.read())

    def __aiter__(self) -> DocumentStream:
        return self

    async def __anext__(self) -> HarnessOutput:
        item = await self.events.get()
        if item is None:
            raise StopAsyncIteration
        if isinstance(item, Exception):
            raise item
        return item

    async def decide(self, request: ApprovalRequest):
        try:
            result = await self.approval.request_approval(request, timeout=self.timeout)
            self.stdin.write(result.model_dump_json().encode() + b"\n")
            await self.stdin.drain()
        except Exception as error:  # noqa: BLE001
            self.events.put_nowait(error)

    async def read(self):
        try:
            while data := await self.stdout.readline():
                packet = json.loads(data)
                if "event" in packet:
                    event = packet["event"]
                    self.events.put_nowait(
                        HarnessOutput.model_validate(
                            event
                            | {
                                "provider": "document",
                                "provider_event": event["kind"],
                                "raw": event,
                            }
                        )
                    )
                elif "steer" in packet:
                    pending = self.pending.get(packet["steer"])
                    if pending is not None and not pending.done():
                        pending.set_result(packet["accepted"])
                else:
                    task = asyncio.create_task(
                        self.decide(ApprovalRequest.model_validate(packet["approval"]))
                    )
                    self.decisions.add(task)
                    task.add_done_callback(self.decisions.discard)
            await self.process.wait()
            if self.process.returncode:
                raise RuntimeError((await self.stderr.read()).decode())
        except Exception as error:  # noqa: BLE001
            self.events.put_nowait(error)
        finally:
            for pending in self.pending.values():
                if not pending.done():
                    pending.set_result(False)
            self.events.put_nowait(None)

    async def steer(self, text: str, *, timeout: float) -> bool:
        if self.process.returncode is not None or self.stdin.is_closing():
            return False
        id = str(uuid7())
        self.pending[id] = asyncio.get_running_loop().create_future()
        try:
            self.stdin.write(
                json.dumps({"steer": id, "input_text": text}).encode() + b"\n"
            )
            await self.stdin.drain()
            async with asyncio.timeout(timeout):
                return await self.pending[id]
        except OSError, TimeoutError:
            return False
        finally:
            self.pending.pop(id, None)

    async def aclose(self):
        self.stdin.close()
        for task in self.decisions:
            task.cancel()
        await asyncio.gather(*tuple(self.decisions), return_exceptions=True)
        await self.process.wait()
        await self.reader


class DocumentHarness:
    """Execute real document statistics and approved writes in child processes."""

    task: asyncio.Task[None]

    def __init__(self, input: HarnessInput, config: HarnessConfig):
        self.input = input
        self.config = config
        self.streams: list[DocumentStream] = []
        self.sessions: dict[str, Path] = {}
        self.inputs: dict[str, SessionInput] = {}
        self.events: asyncio.Queue[HarnessExpire] = asyncio.Queue()
        self.turns: dict[tuple[str, str], DocumentStream] = {}
        self.backgrounds: dict[str, dict[Path, asyncio.subprocess.Process]] = {}
        self.context = ""
        self.contexts: dict[str, str] = {}

    async def describe(self, *, timeout: float) -> HarnessDescription:
        return HarnessDescription(
            skills=(
                HarnessSkill(
                    name="document-report",
                    description="Count a document and save its report",
                    source=__name__,
                ),
            )
        )

    async def start_session(
        self, request: SessionInput, *, timeout: float
    ) -> ProviderCallResult[HarnessSession]:
        request = request.model_copy(
            update={
                "environment": request.environment
                | {name: os.environ[source] for name, source in self.config.env.items()}
            }
        )
        directory = request.workspace / "reports" / request.id
        await asyncio.to_thread(directory.mkdir, parents=True)
        self.sessions[request.id] = directory
        self.inputs[request.id] = request
        self.contexts[request.id] = self.context
        await asyncio.to_thread((directory / "style.txt").write_text, self.context)
        await asyncio.to_thread(
            (directory / "session.json").write_text, request.model_dump_json()
        )
        return ProviderCallResult(
            status=ProviderCallStatus.CONFIRMED,
            value=HarnessSession(id=request.id, provider_thread_id=request.id),
        )

    async def start_turn(
        self,
        session: HarnessSession,
        turn: HarnessTurn,
        approval_handler: ApprovalHandler,
        *,
        timeout: float,
    ) -> DocumentStream:
        if not await asyncio.to_thread(
            (self.sessions[session.id] / "session.json").exists
        ):
            raise HarnessSessionUnavailable("The document session metadata was removed")
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "bcn_agent_workbench.worker",
            cwd=self.input.workspace,
            env=dict(os.environ) | self.inputs[session.id].environment,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stream = DocumentStream(process, approval_handler, timeout)
        self.streams.append(stream)
        self.turns[session.id, turn.id] = stream
        request = {
            "directory": str(self.sessions[session.id]),
            "agent_id": self.input.agent_id,
            "title": self.config.title,
            "input_mode": self.config.input_mode,
            "style": self.contexts[session.id],
            "turn": turn.model_dump(mode="json"),
            "session": self.inputs[session.id].model_dump(mode="json"),
        }
        stream.stdin.write(json.dumps(request).encode() + b"\n")
        await stream.stdin.drain()
        if self.config.publication_delay:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "bcn_agent_workbench.archive",
                str(self.sessions[session.id] / (turn.id + ".json")),
                str(self.config.publication_delay),
                env=dict(os.environ) | self.inputs[session.id].environment,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
            )
            self.backgrounds.setdefault(session.id, {})[
                self.sessions[session.id] / (turn.id + ".json")
            ] = process
        return stream

    async def receive_event(self) -> HarnessExpire:
        return await self.events.get()

    async def reconcile_session(
        self,
        session: HarnessSession,
        turn: HarnessTurn | None,
        approval_handler: ApprovalHandler | None,
        *,
        timeout: float,
    ) -> ProviderCallResult[HarnessSessionReconciliation]:
        if session.id not in self.sessions:
            return ProviderCallResult(
                status=ProviderCallStatus.FAILED,
                error_kind="provider_failed",
                error_message="Document session no longer exists",
            )
        await asyncio.to_thread(
            (self.sessions[session.id] / "session.json").write_text,
            self.inputs[session.id].model_dump_json(),
        )
        stream = None if turn is None else self.turns.get((session.id, turn.id))
        if stream is not None and stream.process.returncode is not None:
            stream = None
        return ProviderCallResult(
            status=ProviderCallStatus.CONFIRMED,
            value=HarnessSessionReconciliation(session, stream),
        )

    async def steer_turn(
        self,
        session: HarnessSession,
        turn: HarnessTurn,
        input_text: str,
        *,
        timeout: float,
    ) -> bool:
        stream = self.turns.get((session.id, turn.id))
        return (
            False if stream is None else await stream.steer(input_text, timeout=timeout)
        )

    async def has_background_job(
        self, session: HarnessSession, *, timeout: float
    ) -> bool:
        if self.task.done():
            self.task.result()
        for report, process in self.backgrounds.get(session.id, {}).items():
            if process.returncode is None and not await asyncio.to_thread(
                report.with_suffix(".published.json").exists
            ):
                return True
        return False

    async def stop_session(
        self, session: HarnessSession, *, timeout: float
    ) -> ProviderCallResult[HarnessSession]:
        directory = self.sessions.get(session.id)
        for process in self.backgrounds.get(session.id, {}).values():
            if process.returncode is None:
                process.terminate()
            await process.wait()
        if directory is not None:
            await asyncio.to_thread(
                (directory / "session.closed").write_text, session.id
            )
        self.sessions.pop(session.id, None)
        self.inputs.pop(session.id, None)
        self.contexts.pop(session.id, None)
        self.backgrounds.pop(session.id, None)
        return ProviderCallResult(status=ProviderCallStatus.CONFIRMED, value=session)

    def read_context(self) -> str:
        try:
            return (self.input.workspace / "STYLE.md").read_text()
        except FileNotFoundError:
            return ""

    def save_context(self):
        temporary = self.input.workspace / ("document-context-" + str(uuid7()) + ".tmp")
        temporary.write_text(json.dumps({"style": self.context}))
        temporary.replace(self.input.workspace / "document-context.json")

    async def watch_context(self):
        while True:
            await asyncio.sleep(0.1)
            context = await asyncio.to_thread(self.read_context)
            if context != self.context:
                self.context = context
                if self.sessions:
                    self.events.put_nowait(
                        HarnessExpire(session_id=next(iter(self.sessions)))
                    )
                await asyncio.to_thread(self.save_context)

    async def aclose(self):
        for processes in self.backgrounds.values():
            for process in processes.values():
                if process.returncode is None:
                    process.terminate()
                await process.wait()
        for stream in self.streams:
            await stream.aclose()
        for id in tuple(self.sessions):
            await self.stop_session(HarnessSession(id=id), timeout=600)


@asynccontextmanager
async def harness_lifespan(ctx: Context, config: HarnessConfig):
    harness = DocumentHarness(ctx.accept(HARNESS_INPUT), config)
    harness.context = await asyncio.to_thread(harness.read_context)
    await asyncio.to_thread(harness.save_context)
    async with asyncio.TaskGroup() as group:
        harness.task = group.create_task(harness.watch_context())
        ctx.offer(HARNESS, harness)
        try:
            yield
        finally:
            harness.task.cancel()
            await harness.aclose()


harness = Contrib(
    config=HarnessConfig,
    accepts=[HARNESS_INPUT],
    offers=[HARNESS],
    lifespan=harness_lifespan,
)


class HarnessBuilderParameters(HarnessBuilderConfig):
    kind: ClassVar[str] = "document"
    entity_model: ClassVar[type[BaseModel]] = HarnessConfig
    entry: ClassVar[Contrib[Any]] = harness
    capabilities: ClassVar[tuple[str, ...]] = ("document-report", "approval")


@asynccontextmanager
async def harness_builder_lifespan(ctx: Context, config: HarnessBuilderParameters):
    with ctx.accept(HARNESS_BUILDERS).register(config.builder()) as builder:
        ctx.offer(harness_builder_key(builder.kind), builder)
        yield


harness_builder = Contrib(
    config=HarnessBuilderParameters,
    accepts=[HARNESS_BUILDERS],
    offers=[harness_builder_key("document")],
    lifespan=harness_builder_lifespan,
)


class SecondaryChannelConfig(ChannelConfig):
    kind: ClassVar[str] = "test-channel-secondary"


secondary_channel = Contrib(
    config=SecondaryChannelConfig,
    accepts=[CHANNEL_INPUT],
    offers=[CHANNEL],
    lifespan=channel_lifespan,
)


class SecondaryChannelParameters(ChannelBuilderParameters):
    kind: ClassVar[str] = "test-channel-secondary"
    entity_model: ClassVar[type[BaseModel]] = SecondaryChannelConfig
    entry: ClassVar[Contrib[Any]] = secondary_channel


secondary_channel_builder = Contrib(
    config=SecondaryChannelParameters,
    accepts=[CHANNEL_BUILDERS],
    offers=[channel_builder_key("test-channel-secondary")],
    lifespan=channel_builder_lifespan,
)
