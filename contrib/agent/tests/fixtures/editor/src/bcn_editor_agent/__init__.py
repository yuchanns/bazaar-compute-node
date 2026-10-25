"""An independent editorial agent choosing its own instructions and services."""

from __future__ import annotations

import asyncio
import os
import secrets
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from time import time_ns
from uuid import uuid7

from bazaar_contrib_channel import (
    CHANNEL,
    CHANNEL_INPUT,
    Channel,
    ChannelApprovalRequest,
    ChannelInput,
    ChannelTarget,
)
from bazaar_contrib_config import CONFIGURATION
from bazaar_contrib_harness import (
    HARNESS,
    HARNESS_INPUT,
    ApprovalRequest,
    HarnessAttachment,
    HarnessInput,
    HarnessSession,
    HarnessTurn,
    SessionInput,
)
from bazaar_contrib_messages import MESSAGES, Actor, Actors, Mode, Thread
from bazaar_contrib_messages.models import Message, SenderIdentity
from bazaar_contrib_messages.states import ChannelTargetKind, MessageDirection, Review
from bazaar_core import Composition, Context, Contrib, ContribEntry
from bcn_agent_workbench import ChannelBuilderParameters, HarnessBuilderParameters
from pydantic import BaseModel, ConfigDict

from bazaar_contrib_agent import AgentConfig
from bazaar_contrib_agent.tool import Tool
from bazaar_contrib_agent.wrapper import install, remove


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    name: str
    workspace: Path
    brief: str
    title: str


class Input(Config):
    command: Path
    tool_config: Path


class Locks:
    def __init__(self):
        self.locks: dict[Thread, asyncio.Lock] = {}

    def for_thread(self, actor: Thread) -> asyncio.Lock:
        return self.locks.setdefault(actor, asyncio.Lock())


@dataclass
class Approval:
    channel: Channel
    target: ChannelTarget
    sender_id: str

    async def request_approval(
        self,
        request: ApprovalRequest,
        *,
        timeout: float,  # noqa: ASYNC109
    ):
        return await self.channel.request_approval(
            ChannelApprovalRequest(
                approval=request, target=self.target, sender_id=self.sender_id
            ),
            timeout=timeout,
        )


@asynccontextmanager
async def process(ctx: Context, config: Input):
    channel, harness = ctx.accept(CHANNEL), ctx.accept(HARNESS)
    sessions: dict[Actor, tuple[HarnessSession, str]] = {}

    async def validate(actor: Actor, request):
        session, capability = sessions[actor]
        if (
            request.get("runtime_session_id") != session.id
            or request.get("session_capability") != capability
        ):
            raise ValueError("The editorial command has no active session")

    async with ctx.accept(MESSAGES).bind(
        actors=Actors(config.id, Mode.SESSION),
        name=config.name,
        channel=channel,
        workspace=config.workspace,
        concurrency=Locks(),
        provider_timeout=30,
        shutdown_timeout=30,
        session_binding_validator=validate,
    ) as messages:

        async def receive():
            async for inbound in channel.receive():
                thread = Thread(inbound.thread_id)
                channel_id = inbound.channel_session_id
                attachments = tuple(
                    [
                        await messages.import_attachment(item)
                        for item in inbound.attachments
                    ]
                )
                recorded = await messages.storage.record_inbound(
                    Message(
                        direction=MessageDirection.INBOUND,
                        seq=0,
                        message_id=inbound.id,
                        thread_id=thread.id,
                        channel_session_id=channel_id,
                        target=f"{inbound.target.kind}:{channel_id}",
                        body=inbound.body,
                        sender=SenderIdentity(
                            id=inbound.sender.id, name=inbound.sender.name
                        ),
                        target_kind=ChannelTargetKind(inbound.target.kind),
                        attachments=attachments,
                        channel="test-channel",
                        channel_identity=channel.get_identity().id,
                        provider_thread_id=inbound.target.thread_id,
                        provider_message_id=inbound.provider_message_id or inbound.id,
                        received_at_ms=inbound.occurred_at_ms,
                        mentions_agent=True,
                        metadata={
                            "sender_kind": inbound.metadata.get(
                                "sender_kind",
                                inbound.sender.kind,
                            )
                        },
                    ),
                    now_ms=time_ns() // 1_000_000,
                    opening=Review.APPROVED,
                )
                message = recorded.message
                if thread not in sessions:
                    capability = secrets.token_urlsafe(32)
                    request = SessionInput(
                        id=str(uuid7()),
                        workspace=config.workspace,
                        instructions=(config.brief,),
                        environment={
                            "PATH": os.pathsep.join(
                                (
                                    str(config.command.parent),
                                    os.environ.get("PATH", os.defpath),
                                )
                            ),
                            "BCN_AGENT_ID": config.id,
                            "BCN_AGENT_MODE": "session",
                            "BCN_ACTOR_ID": thread.id,
                            "BCN_RUNTIME_SESSION_ID": "",
                            "BCN_COMMAND_CAPABILITY": capability,
                            "BCN_TOOL_CONFIG": str(config.tool_config),
                            "EDITOR_NAME": config.title,
                        },
                    )
                    request.environment["BCN_RUNTIME_SESSION_ID"] = request.id
                    result = await harness.start_session(request, timeout=600)
                    assert result.value is not None
                    sessions[thread] = result.value, capability
                session, _ = sessions[thread]
                turn = HarnessTurn(
                    id=str(uuid7()),
                    session_id=session.id,
                    input_text=await messages.format_message(message),
                    attachments=tuple(
                        HarnessAttachment(
                            path=config.workspace / item.relative_path,
                            media_type=item.media_type,
                        )
                        for item in attachments
                        if item.relative_path
                    ),
                )

                stream = await harness.start_turn(
                    session,
                    turn,
                    Approval(
                        channel,
                        inbound.target.model_copy(
                            update={"reply_to_message_id": inbound.provider_message_id}
                        ),
                        inbound.sender.id or "",
                    ),
                    timeout=600,
                )
                try:
                    async for event in stream:
                        channel.accept_turn_event(event, target=inbound.target)
                        if event.kind in {
                            "completed",
                            "failed",
                            "cancelled",
                            "unknown",
                        }:
                            break
                finally:
                    await stream.aclose()

        async with asyncio.TaskGroup() as group:
            task = group.create_task(receive())
            try:
                yield
            finally:
                task.cancel()
        for session, _ in sessions.values():
            await harness.stop_session(session, timeout=600)


@asynccontextmanager
async def lifespan(ctx: Context, config: Config):
    await asyncio.to_thread(config.workspace.mkdir, parents=True, exist_ok=True)
    command = await asyncio.to_thread(
        install, AgentConfig(id=config.id, name=config.name), config.workspace
    )
    channel = ChannelBuilderParameters.builder().compose(
        {"endpoint_file": "editor.json"},
        ChannelInput(
            agent_id=config.id, agent_name=config.name, workspace=config.workspace
        ),
    )
    harness = HarnessBuilderParameters.builder().compose(
        {"title": config.title},
        HarnessInput(
            agent_id=config.id,
            agent_name=config.name,
            workspace=config.workspace,
            startup_timeout=60,
            shutdown_timeout=5,
        ),
    )
    try:
        async with (
            Tool(ctx.accept(CONFIGURATION)) as tool,
            ctx.enable(
                Composition(
                    entries=(
                        *channel.entries,
                        *harness.entries,
                        ContribEntry(
                            name="editor",
                            definition=Contrib(
                                config=Input,
                                accepts=[MESSAGES, CHANNEL, HARNESS],
                                lifespan=process,
                            ),
                            config={
                                **config.model_dump(mode="python"),
                                "command": command,
                                "tool_config": tool.path,
                            },
                        ),
                    )
                ),
                local=(CHANNEL, CHANNEL_INPUT, HARNESS, HARNESS_INPUT),
            ),
        ):
            yield
    finally:
        await asyncio.to_thread(remove, command)


editor = Contrib(config=Config, accepts=[MESSAGES, CONFIGURATION], lifespan=lifespan)
