"""Run the owning agent’s receive, turn and output loop."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import uuid7
lazy import asyncio
lazy import logging
lazy import os

from bazaar_contrib_audit import AUDIT, Audit, AuditContext
from bazaar_contrib_channel import (
    CHANNEL,
    Channel,
    ChannelApprovalRequest,
    ChannelTarget,
)
from bazaar_contrib_harness import (
    HARNESS,
    ApprovalRequest,
    ApprovalResult,
    Harness,
    HarnessAttachment,
    HarnessOutput,
    HarnessSession,
    HarnessTurn,
    ProviderCallStatus,
    SessionInput,
)
from bazaar_core import Context, Contrib
from pydantic import BaseModel, ConfigDict

from .instruction import DeveloperInstructionContext
from .models import Agent

logger = logging.getLogger(__name__)


class LoopConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    agent: Agent
    channel: str
    harness: str
    mode: Literal["session", "dangerous_individual"]
    command: Path
    bot_names: tuple[str, ...]


@dataclass(frozen=True)
class ApprovalRoute:
    channel: Channel
    target: ChannelTarget
    session_id: str
    turn_id: str
    sender_id: str
    audit: Audit
    context: AuditContext

    async def request_approval(
        self, request: ApprovalRequest, *, timeout: float
    ) -> ApprovalResult:  # noqa: ASYNC109
        channel_request = ChannelApprovalRequest(
            approval=request, target=self.target, sender_id=self.sender_id
        )
        await self.audit.publish(
            "approval.requested",
            context=self.context,
            payload=channel_request.model_dump(mode="json"),
            occurred_at_ms=request.created_at_ms,
        )
        if (request.session_id, request.turn_id) != (self.session_id, self.turn_id):
            reason = "The approval request for this operation is invalid."
            logger.warning(
                "approval session/turn mismatch: request_id=%r; "
                "expected session_id=%r, turn_id=%r; "
                "received session_id=%r, turn_id=%r; "
                "agent_id=%r, channel=%r, harness=%r, target_kind=%r, thread_id=%r",
                request.request_id,
                self.session_id,
                self.turn_id,
                request.session_id,
                request.turn_id,
                self.context.agent_id,
                self.context.channel,
                self.context.harness,
                self.target.kind,
                self.target.thread_id,
            )
            result = ApprovalResult(
                request_id=request.request_id, decision="rejected", reason=reason
            )
        else:
            result = await self.channel.request_approval(
                channel_request, timeout=timeout
            )
            if result.request_id != request.request_id:
                reason = "The approval decision could not be matched to this operation."
                logger.warning(
                    "approval result request_id mismatch: "
                    "expected request_id=%r, received request_id=%r; "
                    "session_id=%r, turn_id=%r; "
                    "agent_id=%r, channel=%r, harness=%r, target_kind=%r, thread_id=%r",
                    request.request_id,
                    result.request_id,
                    self.session_id,
                    self.turn_id,
                    self.context.agent_id,
                    self.context.channel,
                    self.context.harness,
                    self.target.kind,
                    self.target.thread_id,
                )
                result = ApprovalResult(
                    request_id=request.request_id, decision="rejected", reason=reason
                )
        await self.audit.publish(
            "approval.decided",
            context=self.context,
            payload=result.model_dump(mode="json"),
            occurred_at_ms=result.decided_at_ms,
        )
        return result


async def forward(
    audit: Audit,
    context: AuditContext,
    channel: Channel,
    event: HarnessOutput,
    target: ChannelTarget,
) -> None:
    """Publish turn events and forward them to the channel."""
    await audit.publish(
        f"harness.{event.kind}",
        context=context.model_copy(
            update={
                key: value
                for key, value in (
                    ("provider_session_id", event.provider_session_id),
                    ("provider_turn_id", event.provider_turn_id),
                )
                if value is not None
            }
        ),
        payload=event.model_dump(mode="json"),
        occurred_at_ms=event.occurred_at_ms,
    )
    try:
        channel.accept_turn_event(event, target=target)
    except Exception:
        logger.exception("channel rejected %s", event.kind)


async def run(config: LoopConfig, channel: Channel, harness: Harness, audit: Audit):
    agent = config.agent
    sessions: dict[tuple[str, str], HarnessSession] = {}
    try:
        async for message in channel.receive():
            target = ChannelTarget(
                kind=message.target.kind, thread_id=message.target.thread_id
            )
            key = (target.kind, target.thread_id)
            context = AuditContext(
                agent_id=agent.id,
                channel=config.channel,
                harness=config.harness,
                target_kind=target.kind,
                thread_id=target.thread_id,
                message_id=message.id,
                sender_id=message.sender.id or "",
            )
            await audit.publish(
                "channel.received",
                context=context,
                payload=message.model_dump(mode="json"),
                occurred_at_ms=message.occurred_at_ms,
            )
            session = sessions.get(key)
            if session is None:
                session_id = str(uuid7())
                instruction = await asyncio.to_thread(
                    DeveloperInstructionContext(
                        agent_name=agent.name,
                        bot_names=config.bot_names,
                        agent_id=agent.id,
                        runtime_session_id=session_id,
                        runtime=config.harness,
                        workspace=str(agent.workspace),
                        mode=config.mode,
                    ).render
                )
                request = SessionInput(
                    id=session_id,
                    workspace=agent.workspace,
                    instructions=(instruction,),
                    environment={
                        "PATH": os.pathsep.join(
                            (
                                str(config.command.parent),
                                os.environ.get("PATH", os.defpath),
                            )
                        ),
                        "BCN_AGENT_ID": agent.id,
                        "BCN_AGENT_MODE": config.mode,
                        "BCN_ACTOR_ID": target.thread_id
                        if config.mode == "session"
                        else agent.id,
                        "BCN_RUNTIME_SESSION_ID": session_id,
                    },
                )
                context = context.model_copy(update={"session_id": request.id})
                await audit.publish(
                    "harness.session.requested",
                    context=context,
                    payload=request.model_dump(mode="json"),
                )
                result = await harness.start_session(request, timeout=600)
                if (
                    result.status is not ProviderCallStatus.CONFIRMED
                    or result.value is None
                ):
                    raise RuntimeError(
                        result.error_message or "Harness session was not confirmed"
                    )
                session = result.value
                sessions[key] = session
                await audit.publish(
                    "harness.session.started",
                    context=context.model_copy(update={"session_id": session.id}),
                    payload=session.model_dump(mode="json"),
                )
            turn = HarnessTurn(
                id=str(uuid7()),
                session_id=session.id,
                input_text=message.body,
                attachments=tuple(
                    HarnessAttachment(path=item.path, media_type=item.media_type)
                    for item in message.attachments
                    if item.path is not None
                ),
            )
            context = context.model_copy(
                update={
                    "session_id": session.id,
                    "turn_id": turn.id,
                    "provider_session_id": session.provider_thread_id,
                }
            )
            stream = None
            try:
                await audit.publish(
                    "harness.turn.requested",
                    context=context,
                    payload=turn.model_dump(mode="json"),
                )
                stream = await harness.start_turn(
                    session,
                    turn,
                    ApprovalRoute(
                        channel,
                        target.model_copy(
                            update={"reply_to_message_id": message.provider_message_id}
                        ),
                        session.id,
                        turn.id,
                        message.sender.id or "",
                        audit,
                        context,
                    ),
                    timeout=600,
                )
                terminal = False
                async for event in stream:
                    await forward(audit, context, channel, event, target)
                    if event.kind in {"completed", "failed", "cancelled", "unknown"}:
                        terminal = True
                        break
                if not terminal:
                    await forward(
                        audit,
                        context,
                        channel,
                        HarnessOutput(
                            session_id=session.id,
                            turn_id=turn.id,
                            kind="unknown",
                            text="runtime stream ended without a terminal event",
                        ),
                        target,
                    )
            except asyncio.CancelledError:
                await forward(
                    audit,
                    context,
                    channel,
                    HarnessOutput(
                        session_id=session.id,
                        turn_id=turn.id,
                        kind="cancelled",
                        text="runtime turn cancelled",
                    ),
                    target,
                )
                raise
            except Exception as error:  # noqa: BLE001 - Report provider failures as turn events.
                await audit.publish(
                    "harness.turn.error",
                    context=context,
                    payload={
                        "error_type": type(error).__name__,
                        "error_message": str(error),
                    },
                )
                await forward(
                    audit,
                    context,
                    channel,
                    HarnessOutput(
                        session_id=session.id,
                        turn_id=turn.id,
                        kind="failed",
                        text=f"runtime turn failed: {type(error).__name__}",
                    ),
                    target,
                )
            finally:
                if stream is not None:
                    await stream.aclose()
                    await audit.publish(
                        "harness.stream.closed", context=context, payload={}
                    )
    finally:
        for (kind, thread_id), session in sessions.items():
            context = AuditContext(
                agent_id=agent.id,
                channel=config.channel,
                harness=config.harness,
                target_kind=kind,
                thread_id=thread_id,
                session_id=session.id,
                provider_session_id=session.provider_thread_id,
            )
            await audit.publish(
                "harness.session.stopping",
                context=context,
                payload=session.model_dump(mode="json"),
            )
            result = await harness.stop_session(session, timeout=600)
            await audit.publish(
                "harness.session.stopped",
                context=context,
                payload={"status": result.status.value, "error": result.error_message},
            )


@asynccontextmanager
async def lifespan(ctx: Context, config: LoopConfig):
    async with asyncio.TaskGroup() as group:
        task = group.create_task(
            run(config, ctx.accept(CHANNEL), ctx.accept(HARNESS), ctx.accept(AUDIT)),
            name=f"agent-loop:{config.agent.id}",
        )
        try:
            yield
        finally:
            task.cancel()


loop = Contrib(
    config=LoopConfig,
    accepts=[CHANNEL, HARNESS, AUDIT],
    lifespan=lifespan,
)
