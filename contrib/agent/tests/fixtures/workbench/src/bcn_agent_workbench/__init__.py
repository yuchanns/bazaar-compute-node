"""Independent providers using only public core, channel and harness packages."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path
from time import time_ns
from typing import Any, ClassVar, cast
from uuid import (
    NAMESPACE_URL,
    uuid5,
    uuid7,
)

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
    HarnessSkill,
    HarnessTurn,
    ProviderCallResult,
    ProviderCallStatus,
    SessionInput,
    harness_builder_key,
)
from bazaar_core import Context, Contrib
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
)


class ChannelMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(min_length=1)
    target: ChannelTarget
    sender: SenderIdentity
    body: str
    attachments: tuple[InboundAttachment, ...] = ()
    occurred_at_ms: int = Field(default_factory=lambda: time_ns() // 1_000_000)
    provider_chat_id: str | None = None
    notifies_runtime: bool = True
    mentions_agent: bool = False
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class ChannelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    endpoint_file: str = "channel.json"


class TestChannel:
    """A connected TCP channel controlled by a peer that observes and approves."""

    def __init__(self, input: ChannelInput, group: asyncio.TaskGroup):
        self.input = input
        self.group = group
        self.writer: asyncio.StreamWriter | None = None
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue()
        self.outbound: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.pending: dict[str, asyncio.Future[dict[str, Any]]] = {}

    def get_identity(self) -> ChannelIdentity:
        return ChannelIdentity(id=self.input.agent_id, name=self.input.agent_name)

    def dm_address(self, request: DmRequest) -> DmAddress | None:
        if request.sender_id is None:
            return None
        identity = f"{self.input.agent_id}:dm:{request.sender_id}"
        return DmAddress(
            channel_session_id=str(uuid5(NAMESPACE_URL, identity + ":channel")),
            thread_id=str(uuid5(NAMESPACE_URL, identity + ":thread")),
            provider_thread_id=request.sender_id,
            channel_identity=request.channel_identity,
        )

    def connect(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.writer = writer
        self.group.create_task(self.connection(reader, writer))

    async def connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        try:
            while data := await reader.readline():
                packet = json.loads(data)
                if "inbound" in packet:
                    message = ChannelMessage.model_validate(packet["inbound"])
                    identity = f"{self.input.agent_id}:{message.target.kind}:{message.target.thread_id}"
                    await self.inbound.put(
                        InboundMessage(
                            **message.model_dump(exclude={"id"}),
                            id=str(
                                uuid5(
                                    NAMESPACE_URL,
                                    f"{self.input.agent_id}:message:{message.id}",
                                )
                            ),
                            provider_message_id=message.id,
                            channel_session_id=str(
                                uuid5(NAMESPACE_URL, identity + ":channel")
                            ),
                            thread_id=str(uuid5(NAMESPACE_URL, identity + ":thread")),
                        )
                    )
                else:
                    pending = self.pending.pop(packet["id"], None)
                    if pending is not None and not pending.done():
                        pending.set_result(packet)
        finally:
            if self.writer is writer:
                self.writer = None
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
            writer = self.writer
            if writer is None:
                raise RuntimeError("channel peer is disconnected")
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
    ) -> ApprovalResult:  # noqa: ASYNC109
        id = request.approval.request_id
        self.pending[id] = asyncio.get_running_loop().create_future()
        await self.outbound.put({"id": id, "approval": request.model_dump(mode="json")})
        packet = await self.pending[id]
        return ApprovalResult.model_validate(packet["decision"])


@asynccontextmanager
async def channel_lifespan(ctx: Context, config: ChannelConfig):
    input = ctx.accept(CHANNEL_INPUT)
    async with asyncio.TaskGroup() as group:
        channel = TestChannel(input, group)
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

    def __aiter__(self) -> DocumentStream:
        return self

    async def __anext__(self) -> HarnessOutput:
        while data := await self.stdout.readline():
            packet = json.loads(data)
            if "event" in packet:
                event = packet["event"]
                return HarnessOutput.model_validate(
                    event
                    | {
                        "provider": "document",
                        "provider_event": event["kind"],
                        "raw": event,
                    }
                )
            request = ApprovalRequest.model_validate(packet["approval"])
            result = await self.approval.request_approval(request, timeout=self.timeout)
            self.stdin.write(result.model_dump_json().encode() + b"\n")
            await self.stdin.drain()
        await self.process.wait()
        if self.process.returncode:
            raise RuntimeError((await self.stderr.read()).decode())
        raise StopAsyncIteration

    async def aclose(self):
        self.stdin.close()
        await self.process.wait()


class DocumentHarness:
    """Execute real document statistics and approved writes in child processes."""

    def __init__(self, input: HarnessInput, config: HarnessConfig):
        self.input = input
        self.config = config
        self.streams: list[DocumentStream] = []
        self.sessions: dict[str, Path] = {}
        self.turns: dict[tuple[str, str], DocumentStream] = {}
        self.events: asyncio.Queue[HarnessExpire] = asyncio.Queue()
        self.inputs: dict[str, SessionInput] = {}

    async def describe(self, *, timeout: float) -> HarnessDescription:  # noqa: ASYNC109
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
    ) -> ProviderCallResult[HarnessSession]:  # noqa: ASYNC109
        directory = request.workspace / "reports" / request.id
        await asyncio.to_thread(directory.mkdir, parents=True)
        self.sessions[request.id] = directory
        self.inputs[request.id] = request
        await asyncio.to_thread(
            (directory / "session.json").write_text, request.model_dump_json()
        )
        return ProviderCallResult(
            status=ProviderCallStatus.CONFIRMED, value=HarnessSession(id=request.id)
        )

    async def start_turn(
        self,
        session: HarnessSession,
        turn: HarnessTurn,
        approval_handler: ApprovalHandler,
        *,
        timeout: float,  # noqa: ASYNC109
    ) -> DocumentStream:
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
            "turn": turn.model_dump(mode="json"),
            "session": self.inputs[session.id].model_dump(mode="json"),
        }
        stream.stdin.write(json.dumps(request).encode() + b"\n")
        await stream.stdin.drain()
        return stream

    async def receive_event(self) -> HarnessExpire:
        return await self.events.get()

    async def reconcile_session(
        self,
        session: HarnessSession,
        turn: HarnessTurn | None,
        approval_handler: ApprovalHandler | None,
        *,
        timeout: float,  # noqa: ASYNC109
    ) -> ProviderCallResult[HarnessSessionReconciliation]:
        if session.id not in self.sessions:
            return ProviderCallResult(
                status=ProviderCallStatus.FAILED,
                error_kind="provider_failed",
                error_message="Document session no longer exists",
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
        timeout: float,  # noqa: ASYNC109
    ) -> bool:
        return False

    async def has_background_job(
        self,
        session: HarnessSession,
        *,
        timeout: float,  # noqa: ASYNC109
    ) -> bool:
        return False

    async def stop_session(
        self, session: HarnessSession, *, timeout: float
    ) -> ProviderCallResult[HarnessSession]:  # noqa: ASYNC109
        self.inputs.pop(session.id)
        await asyncio.to_thread(
            (self.sessions.pop(session.id) / "session.closed").write_text, session.id
        )
        return ProviderCallResult(status=ProviderCallStatus.CONFIRMED, value=session)

    async def aclose(self):
        for stream in self.streams:
            await stream.aclose()


@asynccontextmanager
async def harness_lifespan(ctx: Context, config: HarnessConfig):
    harness = DocumentHarness(ctx.accept(HARNESS_INPUT), config)
    ctx.offer(HARNESS, harness)
    try:
        yield
    finally:
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
