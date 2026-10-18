"""Consume public message services from a real TCP channel in each agent scope."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import cast
from uuid import NAMESPACE_URL, uuid5

from bazaar_contrib_audit import AUDIT_EVENT, AuditEvent
from bazaar_contrib_channel import CHANNEL, CHANNEL_INPUT, ChannelInput
from bazaar_contrib_messages import (
    MESSAGES,
    Actors,
    Mode,
)
from bazaar_contrib_messages.models import (
    ChannelTargetPresentation,
    InboundAttachment,
    Message,
    SenderIdentity,
)
from bazaar_contrib_messages.states import ChannelTargetKind, MessageDirection, Review
from bazaar_contrib_messages_node import AttachmentMaterializer, ThreadLockRegistry
from bazaar_core import Composition, Context, Contrib, ContribEntry
from bcn_agent_workbench import channel
from pydantic import BaseModel, ConfigDict

from bazaar_contrib_collaboration import COLLABORATION


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    directory: Path
    agents: tuple[str, ...] = ("Cloud", "Tifa")


async def file_chunks(path: Path) -> AsyncIterator[bytes]:
    """Stream the peer's local file through the public attachment sink."""
    file = await asyncio.to_thread(path.open, "rb")
    try:
        while chunk := await asyncio.to_thread(file.read, 64 * 1024):
            yield chunk
    finally:
        await asyncio.to_thread(file.close)


@asynccontextmanager
async def bind(ctx: Context, config: Config) -> AsyncIterator[None]:
    input = ctx.accept(CHANNEL_INPUT)
    channel = ctx.accept(CHANNEL)
    concurrency = ThreadLockRegistry()
    async with (
        ctx.accept(MESSAGES).bind(
            actors=Actors(input.agent_id, Mode.DANGEROUS_INDIVIDUAL),
            name=input.agent_name,
            channel=channel,
            workspace=input.workspace,
            concurrency=concurrency,
            provider_timeout=10,
            shutdown_timeout=10,
        ) as messages,
        ctx.accept(COLLABORATION).bind(
            messages=messages,
            concurrency=concurrency,
            channel=channel,
            provider_timeout=10,
        ) as service,
    ):
        materializer = AttachmentMaterializer(
            lambda: input.workspace, messages.storage.list_ready_attachment_paths
        )
        await materializer.reconcile()

        async def receive():
            async for inbound in channel.receive():
                identity = (
                    f"{input.agent_id}:{inbound.target.kind}:{inbound.target.thread_id}"
                )
                channel_session_id = str(uuid5(NAMESPACE_URL, identity + ":channel"))
                thread_id = str(uuid5(NAMESPACE_URL, identity + ":thread"))
                attachments: list[InboundAttachment] = []
                for attachment in inbound.attachments:
                    attachments.append(
                        materializer.failed(
                            name=attachment.name,
                            kind=attachment.kind,
                            media_type=attachment.media_type,
                            error=attachment.error,
                        )
                        if attachment.state == "failed"
                        else await materializer.materialize(
                            file_chunks(cast(Path, attachment.path)),
                            name=attachment.name,
                            kind=attachment.kind,
                            media_type=attachment.media_type,
                        )
                    )
                message = Message(
                    direction=MessageDirection.INBOUND,
                    seq=0,
                    message_id=inbound.provider_message_id or inbound.id,
                    thread_id=thread_id,
                    channel_session_id=channel_session_id,
                    target=f"{inbound.target.kind}:{channel_session_id}",
                    body=inbound.body,
                    sender=SenderIdentity(
                        id=inbound.sender.id,
                        name=inbound.sender.name,
                        display_name=inbound.sender.display_name,
                    ),
                    target_kind=ChannelTargetKind(inbound.target.kind),
                    target_presentation=ChannelTargetPresentation(
                        handle=inbound.sender.name
                        if inbound.target.kind == "dm"
                        else None,
                        display_name="Design room"
                        if inbound.target.kind == "group"
                        else inbound.sender.display_name,
                    ),
                    attachments=tuple(attachments),
                    channel="test-channel",
                    channel_identity=input.agent_id,
                    provider_thread_id=inbound.target.thread_id,
                    provider_chat_id=inbound.provider_chat_id
                    or inbound.target.thread_id,
                    provider_message_id=inbound.provider_message_id or inbound.id,
                    received_at_ms=inbound.occurred_at_ms,
                    mentions_agent=inbound.mentions_agent
                    or f"@{input.agent_name}" in inbound.body,
                    notifies_runtime=inbound.notifies_runtime,
                    metadata={
                        **inbound.metadata,
                        "sender_kind": inbound.sender.kind,
                        "provider_reply_to_message_id": inbound.target.reply_to_message_id,
                    },
                )
                recorded = await service.record_inbound(message)
                if (
                    recorded is not None
                    and recorded.message_created
                    and recorded.channel_session.review is not Review.APPROVED
                ):
                    await service.answer_unreviewed(
                        recorded.message, recorded.channel_session
                    )

        task = asyncio.create_task(receive())
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


@asynccontextmanager
async def lifespan(ctx: Context, config: Config):
    async with AsyncExitStack() as stack:
        log = await asyncio.to_thread(
            (config.directory / "audit.jsonl").open, "a", encoding="utf-8"
        )
        lock = asyncio.Lock()

        async def append(ctx: Context, event: AuditEvent):
            async with lock:
                await asyncio.to_thread(log.write, event.model_dump_json() + "\n")
                await asyncio.to_thread(log.flush)

        try:
            await stack.enter_async_context(
                ctx.enable(
                    Composition(
                        entries=(
                            ContribEntry(
                                name="recorder",
                                definition=Contrib(listen={AUDIT_EVENT: append}),
                            ),
                        )
                    )
                )
            )
            for name in config.agents:
                workspace = config.directory / name
                await asyncio.to_thread(workspace.mkdir, parents=True, exist_ok=True)

                @asynccontextmanager
                async def input_lifespan(
                    ctx: Context, workspace: Path = workspace, name: str = name
                ):
                    ctx.offer(
                        CHANNEL_INPUT,
                        ChannelInput(
                            agent_id=name, agent_name=name, workspace=workspace
                        ),
                    )
                    yield

                await stack.enter_async_context(
                    ctx.enable(
                        Composition(
                            entries=(
                                ContribEntry(
                                    name="input",
                                    definition=Contrib(
                                        offers=[CHANNEL_INPUT], lifespan=input_lifespan
                                    ),
                                ),
                                ContribEntry(name="test-channel", definition=channel),
                                ContribEntry(
                                    name="bind",
                                    config=config.model_dump(mode="python"),
                                    definition=Contrib(
                                        config=Config,
                                        accepts=[
                                            MESSAGES,
                                            COLLABORATION,
                                            CHANNEL_INPUT,
                                            CHANNEL,
                                        ],
                                        lifespan=bind,
                                    ),
                                ),
                            )
                        ),
                        local=(CHANNEL_INPUT, CHANNEL),
                    )
                )
            yield
        finally:
            await stack.aclose()
            await asyncio.to_thread(log.close)


gateway = Contrib(config=Config, accepts=[MESSAGES, COLLABORATION], lifespan=lifespan)
