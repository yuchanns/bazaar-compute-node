"""Consume public message services from a real TCP channel in each agent scope."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from time import time_ns
from uuid import NAMESPACE_URL, uuid5

from bazaar_contrib_channel import CHANNEL, CHANNEL_INPUT, ChannelInput
from bazaar_contrib_messages import (
    MESSAGES,
    Actors,
    Mode,
)
from bazaar_contrib_messages.models import (
    ChannelTargetPresentation,
    Message,
    SenderIdentity,
)
from bazaar_contrib_messages.states import ChannelTargetKind, MessageDirection, Review
from bazaar_core import Composition, Context, Contrib, ContribEntry
from bcn_agent_workbench import channel
from pydantic import BaseModel, ConfigDict

from bazaar_contrib_messages_node import ThreadLockRegistry


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    directory: Path
    agents: tuple[str, ...] = ("Cloud", "Tifa")


@asynccontextmanager
async def bind(ctx: Context) -> AsyncIterator[None]:
    input = ctx.accept(CHANNEL_INPUT)
    channel = ctx.accept(CHANNEL)
    async with ctx.accept(MESSAGES).bind(
        actors=Actors(input.agent_id, Mode.DANGEROUS_INDIVIDUAL),
        name=input.agent_name,
        channel=channel,
        workspace=input.workspace,
        concurrency=ThreadLockRegistry(),
        provider_timeout=10,
        shutdown_timeout=10,
    ) as service:

        async def receive():
            async for inbound in channel.receive():
                identity = (
                    f"{input.agent_id}:{inbound.target.kind}:{inbound.target.thread_id}"
                )
                channel_session_id = str(uuid5(NAMESPACE_URL, identity + ":channel"))
                thread_id = str(uuid5(NAMESPACE_URL, identity + ":thread"))
                parent = None
                if inbound.target.reply_to_message_id is not None:
                    parent = await service.storage.find_message(
                        "test-channel",
                        inbound.target.thread_id,
                        inbound.target.reply_to_message_id,
                    )
                attachments = [
                    await service.import_attachment(
                        attachment,
                        attachment_id=service.prepare_attachment(
                            attachment
                        ).attachment_id,
                    )
                    for attachment in inbound.attachments
                ]
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
                    reply_to_message_id=parent.message_id
                    if parent is not None
                    else None,
                    channel="test-channel",
                    channel_identity=input.agent_id,
                    provider_thread_id=inbound.target.thread_id,
                    provider_message_id=inbound.provider_message_id or inbound.id,
                    received_at_ms=inbound.occurred_at_ms,
                    mentions_agent=f"@{input.agent_name}" in inbound.body,
                    metadata={
                        "sender_kind": inbound.metadata.get(
                            "sender_kind",
                            inbound.sender.kind,
                        )
                    },
                )
                await service.storage.record_inbound(
                    message, now_ms=time_ns() // 1_000_000, opening=Review.APPROVED
                )

        task = asyncio.create_task(receive())
        try:
            yield
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@asynccontextmanager
async def lifespan(ctx: Context, config: Config):
    async with AsyncExitStack() as stack:
        for name in config.agents:
            workspace = config.directory / name
            await asyncio.to_thread(workspace.mkdir, parents=True, exist_ok=True)

            @asynccontextmanager
            async def input_lifespan(
                ctx: Context, workspace: Path = workspace, name: str = name
            ):
                ctx.offer(
                    CHANNEL_INPUT,
                    ChannelInput(agent_id=name, agent_name=name, workspace=workspace),
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
                                definition=Contrib(
                                    accepts=[MESSAGES, CHANNEL_INPUT, CHANNEL],
                                    lifespan=bind,
                                ),
                            ),
                        )
                    ),
                    local=(CHANNEL_INPUT, CHANNEL),
                )
            )
        yield


gateway = Contrib(config=Config, accepts=[MESSAGES], lifespan=lifespan)
