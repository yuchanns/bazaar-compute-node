"""Connect real TCP input and durable Reminder wakes through public services."""

from __future__ import annotations

import asyncio
import json
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from time import time_ns
from uuid import NAMESPACE_URL, uuid5

from bazaar_contrib_audit import AUDIT_EVENT, AuditEvent
from bazaar_contrib_channel import CHANNEL, CHANNEL_INPUT, ChannelInput
from bazaar_contrib_messages import MESSAGES, Actors, Agent, Mode
from bazaar_contrib_messages.models import InboundAttachment, Message, SenderIdentity
from bazaar_contrib_messages.states import ChannelTargetKind, MessageDirection, Review
from bazaar_core import Composition, Context, Contrib, ContribEntry
from bcn_agent_workbench import channel
from pydantic import BaseModel, ConfigDict

from bazaar_contrib_reminder import REMINDER, resolve_reminder_anchor
from bazaar_contrib_reminder.concurrency import ThreadLockRegistry


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    directory: Path


@asynccontextmanager
async def lifespan(ctx: Context, config: Config):
    async with AsyncExitStack() as stack:
        audit = await asyncio.to_thread(
            (config.directory / "audit.jsonl").open, "a", encoding="utf-8"
        )
        wakes = await asyncio.to_thread(
            (config.directory / "wakes.jsonl").open, "a", encoding="utf-8"
        )
        lock = asyncio.Lock()

        async def append(ctx: Context, event: AuditEvent):
            async with lock:
                await asyncio.to_thread(audit.write, event.model_dump_json() + "\n")
                await asyncio.to_thread(audit.flush)

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
        service = ctx.accept(REMINDER)
        try:
            for name, mode in (
                ("Cloud", Mode.SESSION),
                ("Tifa", Mode.DANGEROUS_INDIVIDUAL),
            ):
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

                @asynccontextmanager
                async def bind(ctx: Context, name: str = name, mode: Mode = mode):
                    input = ctx.accept(CHANNEL_INPUT)
                    channel = ctx.accept(CHANNEL)
                    actors = Actors(name, mode)
                    owner = Agent(name)
                    async with ctx.accept(MESSAGES).bind(
                        actors=actors,
                        name=name,
                        channel=channel,
                        workspace=input.workspace,
                        concurrency=ThreadLockRegistry(),
                        provider_timeout=10,
                        shutdown_timeout=10,
                    ) as messages:

                        async def wake(message: Message[InboundAttachment]) -> bool:
                            anchor = await resolve_reminder_anchor(
                                service.storage.for_agent(owner), owner, message
                            )
                            async with lock:
                                await asyncio.to_thread(
                                    wakes.write,
                                    json.dumps(
                                        {
                                            "agent_id": owner.id,
                                            "thread_id": message.thread_id,
                                            "message_id": message.message_id,
                                            "seq": message.seq,
                                            "body": message.body,
                                            "metadata": message.metadata,
                                            "anchor_message_id": None
                                            if anchor is None
                                            else anchor.message_id,
                                        },
                                        ensure_ascii=False,
                                    )
                                    + "\n",
                                )
                                await asyncio.to_thread(wakes.flush)
                            return True

                        async with ctx.accept(REMINDER).bind(
                            actors=actors, wake=wake, shutdown_timeout=10
                        ):

                            async def receive():
                                async for inbound in channel.receive():
                                    identity = f"{name}:{inbound.target.kind}:{inbound.target.thread_id}"
                                    session_id = str(
                                        uuid5(NAMESPACE_URL, identity + ":channel")
                                    )
                                    thread_id = str(
                                        uuid5(NAMESPACE_URL, identity + ":thread")
                                    )
                                    message = Message[InboundAttachment](
                                        direction=MessageDirection.INBOUND,
                                        seq=0,
                                        message_id=inbound.provider_message_id
                                        or inbound.id,
                                        thread_id=thread_id,
                                        channel_session_id=session_id,
                                        target=f"{inbound.target.kind}:{session_id}",
                                        body=inbound.body,
                                        sender=SenderIdentity(
                                            id=inbound.sender.id,
                                            name=inbound.sender.name,
                                            display_name=inbound.sender.display_name,
                                        ),
                                        target_kind=ChannelTargetKind(
                                            inbound.target.kind
                                        ),
                                        channel="test-channel",
                                        channel_identity=name,
                                        provider_thread_id=inbound.target.thread_id,
                                        provider_chat_id=inbound.target.thread_id,
                                        provider_message_id=inbound.provider_message_id
                                        or inbound.id,
                                        received_at_ms=inbound.occurred_at_ms,
                                        notifies_runtime=inbound.notifies_runtime,
                                        metadata={"sender_kind": inbound.sender.kind},
                                    )
                                    await messages.storage.record_inbound(
                                        message,
                                        now_ms=time_ns() // 1_000_000,
                                        opening=Review.APPROVED,
                                    )

                            task = asyncio.create_task(receive())
                            try:
                                yield
                            finally:
                                task.cancel()
                                await asyncio.gather(task, return_exceptions=True)

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
                                    name="binding",
                                    definition=Contrib(
                                        accepts=[
                                            MESSAGES,
                                            REMINDER,
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
            await service.start(timeout=10, shutdown_timeout=10)
            try:
                yield
            finally:
                await service.stop()
        finally:
            await stack.aclose()
            await asyncio.to_thread(wakes.close)
            await asyncio.to_thread(audit.close)


gateway = Contrib(config=Config, accepts=[MESSAGES, REMINDER], lifespan=lifespan)
