"""Assemble one agent's Actor loops, conversation bindings and providers."""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
from collections.abc import Container, Mapping, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from time import time_ns
from typing import cast
from uuid import UUID, uuid7

from bazaar_contrib_audit import Audit, AuditContext
from bazaar_contrib_channel import (
    ChannelIdentity,
    ChannelRetryPolicy,
    ChannelSender,
    ChannelSendRequest,
    ChannelTarget,
    InboundMessage,
)
from bazaar_contrib_collaboration import Collaboration, CollaborationService
from bazaar_contrib_harness import (
    ApprovalPolicy,
    Harness,
    HarnessFailure,
    HarnessFailureReason,
    HarnessOutput,
    HarnessSession,
    HarnessSessionUnavailable,
    HarnessTurn,
    HarnessTurnStream,
    ProviderCallStatus,
    SandboxPolicy,
    SessionInput,
)
from bazaar_contrib_messages import (
    Actor,
    Actors,
    Messages,
    MessageService,
    Mode,
    Thread,
)
from bazaar_contrib_messages import Agent as AgentActor
from bazaar_contrib_messages.models import (
    ChannelTargetPresentation,
    InboundAttachment,
    Message,
    SenderIdentity,
)
from bazaar_contrib_messages.states import ChannelTargetKind, MessageDirection, Review
from bazaar_contrib_reminder import Reminders, resolve_reminder_anchor
from bazaar_contrib_storage import Column, Compare, Database, Insert, Select, Value
from bazaar_contrib_timer import Timer
from bazaar_core import TaskFailureSignal

from . import states
from .events import ApprovalRoute, forward
from .instruction import DeveloperInstructionContext
from .models import Agent, AgentConfig, HarnessConfig
from .notice import inbox_notice
from .routing import Channels, Connection, Ingress

logger = logging.getLogger(__name__)


@dataclass(eq=False, slots=True)
class Runtime:
    name: str
    harness: Harness
    provider: HarnessConfig
    priority: int
    state: states.Lifecycle = states.Lifecycle.RUNNING
    pending: int = 0
    banned_until_ms: int = 0


@dataclass(frozen=True, slots=True)
class Wakeup:
    message: Message[InboundAttachment]
    runtime: Runtime
    anchor: Message
    connection: Connection
    wake_id: str | None = None


@dataclass(slots=True)
class ActiveTurn:
    session: HarnessSession
    runtime: Runtime
    turn: HarnessTurn
    context: AuditContext
    participants: dict[str, Wakeup]
    state: states.TurnState = states.TurnState.PREPARING


@dataclass(slots=True)
class ActorLoop:
    """Keep the queue and execution state belonging to one Actor."""

    actor: Actor
    queue: asyncio.Queue[Wakeup] = field(default_factory=asyncio.Queue)
    state: states.SessionState = field(default_factory=states.Dormant)
    task: asyncio.Task[None] | None = None
    idle_remaining_ms: int = 0
    active: ActiveTurn | None = None

    def drain(self) -> list[Wakeup]:
        pending: list[Wakeup] = []
        while not self.queue.empty():
            pending.append(self.queue.get_nowait())
        return pending


class AgentLoop:
    """Own one Agent's Actor loops, ingress and provider bindings."""

    def __init__(
        self,
        agent: Agent,
        config: AgentConfig,
        command: Path,
        messages: Messages,
        collaboration: Collaboration,
        reminders: Reminders,
        timer: Timer,
        audit: Audit,
        database: Database,
        tool_config: Path,
        instance_id: UUID,
        channel_order: dict[str, int],
    ):
        self.instance_id = instance_id
        self.agent = agent
        self.idle_timeout_ms = int(config.idle_timeout * 1000)
        self.command = command
        self.messages = messages
        self.collaboration = collaboration
        self.reminders = reminders
        self.timer = timer
        self.audit = audit
        self.database = database
        self.tool_config = tool_config
        self.actors = Actors(agent.id, Mode(config.mode))
        self.channels = Channels(
            ChannelIdentity(id=agent.id, name=agent.name), channel_order
        )
        self.runtimes: dict[str, Runtime] = {}
        self.actor_loops: dict[Actor, ActorLoop] = {}
        self.ingress: dict[Thread, asyncio.Queue[Ingress]] = {}
        self.tasks: set[asyncio.Task[None]] = set()
        self.teardowns: set[asyncio.Task[None]] = set()
        self.dispatcher: asyncio.Task[None] | None = None
        self.bindings = AsyncExitStack()
        self.failure = TaskFailureSignal()
        self.failure.reset()
        self.notified: dict[Thread, int] = {}
        self.locks: dict[Thread, asyncio.Lock] = {}
        self.service: MessageService
        self.contacts: CollaborationService

    async def wait_failure(self) -> None:
        await self.failure.wait()

    def for_thread(self, actor: Thread) -> asyncio.Lock:
        return self.locks.setdefault(actor, asyncio.Lock())

    async def busy(self) -> bool:
        if any(
            loop.task is not None or not loop.queue.empty()
            for loop in self.actor_loops.values()
        ):
            return True
        for loop in self.actor_loops.values():
            if await self.background(loop):
                return True
        return any(
            loop.task is not None or not loop.queue.empty()
            for loop in self.actor_loops.values()
        )

    async def validate_binding(
        self, actor: Actor, request: Mapping[str, object]
    ) -> None:
        loop = self.actor_loops.get(actor)
        binding = (
            None
            if loop is None or isinstance(loop.state, states.Dormant)
            else loop.state.binding
        )
        if (
            binding is None
            or request.get("runtime_session_id") != binding.session.id
            or not secrets.compare_digest(
                str(request.get("session_capability", "")), binding.capability
            )
        ):
            raise ValueError("The command is not bound to an active runtime session")

    async def receive(self, connection: Connection) -> None:
        async for message in connection.channel.receive():
            if (
                self.channels.state is not states.Lifecycle.RUNNING
                or connection.state is not states.ConnectionState.RUNNING
            ):
                return
            runtime: str | None = None
            if any(
                item.priority == 0 and item.state is states.Lifecycle.RUNNING
                for item in self.runtimes.values()
            ):
                selected = self.select()
                for name, item in self.runtimes.items():
                    if item is selected:
                        item.pending += 1
                        runtime = name
                        break
            self.channels.pending += 1
            self.channels.inbound.put_nowait(Ingress(connection, runtime, message))

        raise RuntimeError("Channel receive stream ended unexpectedly")

    def select(
        self, *, loop: ActorLoop | None = None, exclude: Container[Runtime] = ()
    ) -> Runtime | None:
        binding = (
            None
            if loop is None or isinstance(loop.state, states.Dormant)
            else loop.state.binding
        )
        if (
            binding is not None
            and binding.runtime.state is states.Lifecycle.RUNNING
            and binding.runtime not in exclude
            and binding.runtime.banned_until_ms <= self.timer.now_ms
        ):
            return binding.runtime
        candidates = sorted(
            (
                item
                for item in self.runtimes.values()
                if item.state is states.Lifecycle.RUNNING and item not in exclude
            ),
            key=lambda item: item.priority,
        )
        for item in candidates:
            if item.banned_until_ms <= self.timer.now_ms:
                return item
        return min(candidates, key=lambda item: item.banned_until_ms, default=None)

    async def wake(
        self,
        message: Message[InboundAttachment],
        connection: Connection | None = None,
        runtime: Runtime | None = None,
    ) -> bool:
        if self.channels.state is not states.Lifecycle.RUNNING:
            return False
        wake_id = str(uuid7()) if connection is None else None
        if runtime is None:
            if not any(
                item.priority == 0 and item.state is states.Lifecycle.RUNNING
                for item in self.runtimes.values()
            ):
                return False
            runtime = self.select()
        if runtime is None:
            return False
        anchor = await resolve_reminder_anchor(
            self.reminders.storage, self.service.storage.owner, message
        )
        if anchor is None:
            return False
        if connection is None:
            session = await self.service.storage.get_channel_session(
                anchor.channel_session_id
            )
            if session is None:
                return False
            connection = next(
                (
                    item
                    for item in self.channels.connections.values()
                    if item.state is states.ConnectionState.RUNNING
                    and item.kind == session.channel
                    and item.channel.get_identity().id == session.channel_identity
                ),
                None,
            )
            if connection is None:
                return False
        thread = Thread(message.thread_id)
        self.notified[thread] = max(message.seq, self.notified.get(thread, -1))
        loop = self.actor_loops.get(self.actors.for_thread(message.thread_id))
        if loop is None:
            loop = ActorLoop(self.actors.for_thread(message.thread_id))
            self.actor_loops[loop.actor] = loop
            task = asyncio.create_task(
                self.failure.observe(
                    self.run(loop), component=f"actor:{loop.actor.id}"
                ),
                name=f"actor:{loop.actor.id}",
            )
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
        elif runtime.state is states.Lifecycle.RUNNING:
            runtime = self.select(loop=loop) or runtime
        runtime.pending += 1
        await loop.queue.put(Wakeup(message, runtime, anchor, connection, wake_id))
        return True

    async def recover(self) -> None:
        if not self.runtimes:
            return
        for owner in await self.messages.list_unread_message_owners():
            if owner.agent.id != self.agent.id:
                continue
            message = owner.trigger_message
            if message.seq <= self.notified.get(Thread(message.thread_id), -1):
                continue
            await self.wake(message)

    async def stop(self) -> None:
        self.channels.state = states.Lifecycle.STOPPING
        self.failure.disable()
        tasks = (
            *self.tasks,
            *((self.dispatcher,) if self.dispatcher is not None else ()),
        )
        for task in tasks:
            task.cancel()
        await self.bindings.aclose()
        await asyncio.gather(*tasks, return_exceptions=True)
        teardowns = tuple(self.teardowns)
        for task in teardowns:
            task.cancel()
        await asyncio.gather(*teardowns, return_exceptions=True)
        for queue in (self.channels.inbound, *self.ingress.values()):
            while not queue.empty():
                inbound = queue.get_nowait()
                runtime = self.runtimes.get(inbound.runtime or "")
                if runtime is not None:
                    runtime.pending -= 1
                self.channels.pending -= 1
                queue.task_done()
        self.channels.state = states.Lifecycle.STOPPED

    async def dispatch(self) -> None:
        async for entry in self.channels.entries():
            thread = Thread(entry.message.thread_id)
            queue = self.ingress.get(thread)
            if queue is None:
                queue = asyncio.Queue()
                self.ingress[thread] = queue
                task = asyncio.create_task(
                    self.failure.observe(
                        self.ingest(queue), component=f"ingress:{thread.id}"
                    ),
                    name=f"ingress:{thread.id}",
                )
                self.tasks.add(task)
                task.add_done_callback(self.tasks.discard)
            self.channels.pending += 1
            queue.put_nowait(entry)

    async def ingest(self, queue: asyncio.Queue[Ingress]) -> None:
        while True:
            entry = await queue.get()
            connection = entry.connection
            runtime = self.runtimes.get(entry.runtime or "")
            inbound = entry.message
            try:
                await self.accept(inbound, connection, runtime)
            finally:
                if runtime is not None:
                    runtime.pending -= 1
                self.channels.pending -= 1
                queue.task_done()

    async def accept(
        self, inbound: InboundMessage, connection: Connection, runtime: Runtime | None
    ) -> None:
        channel = connection.channel.get_identity()
        context = AuditContext(
            agent_id=self.agent.id,
            channel=connection.kind,
            target_kind=inbound.target.kind,
            thread_id=inbound.thread_id,
            message_id=inbound.id,
            sender_id=inbound.sender.id,
        )
        await self.audit.publish(
            "channel.received",
            context=context,
            payload=inbound.model_dump(mode="json"),
            occurred_at_ms=inbound.occurred_at_ms,
        )
        channel_session_id = inbound.channel_session_id
        attachments = [
            self.service.prepare_attachment(attachment)
            for attachment in inbound.attachments
        ]
        message = Message(
            direction=MessageDirection.INBOUND,
            seq=0,
            message_id=inbound.id,
            thread_id=inbound.thread_id,
            channel_session_id=channel_session_id,
            target=f"{inbound.target.kind}:{channel_session_id}",
            body=inbound.body,
            sender=SenderIdentity(
                id=inbound.sender.id,
                name=inbound.sender.name,
                display_name=inbound.sender.display_name,
            ),
            message_type=inbound.message_type,
            reply_to_message_id=inbound.reply_to_message_id,
            provider_payload_ref=inbound.provider_payload_ref,
            target_kind=ChannelTargetKind(inbound.target.kind),
            target_presentation=ChannelTargetPresentation(
                display_name=inbound.target_presentation.display_name,
                handle=inbound.target_presentation.handle,
            )
            if inbound.target_presentation is not None
            else None,
            attachments=tuple(attachments),
            channel=connection.kind,
            channel_identity=channel.id,
            provider_thread_id=inbound.target.thread_id,
            provider_chat_id=inbound.provider_chat_id,
            provider_message_id=inbound.provider_message_id,
            provider_time_ms=inbound.occurred_at_ms,
            received_at_ms=inbound.received_at_ms
            if inbound.received_at_ms is not None
            else time_ns() // 1_000_000,
            mentions_agent=inbound.mentions_agent,
            notifies_runtime=inbound.notifies_runtime,
            metadata={
                **inbound.metadata,
                "sender_kind": inbound.metadata.get(
                    "sender_kind",
                    inbound.sender.kind,
                ),
                **(
                    {"provider_reply_to_message_id": inbound.target.reply_to_message_id}
                    if inbound.target.reply_to_message_id is not None
                    else {}
                ),
            },
        )
        try:
            async with self.for_thread(Thread(message.thread_id)):
                recorded = await self.contacts.record_inbound(message)
            if recorded is None or not recorded.message_created:
                return
            message = recorded.message
            if recorded.channel_session.review is not Review.APPROVED:
                attachments = [
                    replace(
                        attachment,
                        state="failed",
                        error="Attachment download requires an approved conversation.",
                    )
                    if attachment.state == "downloading"
                    else attachment
                    for attachment in message.attachments
                ]
                if attachments:
                    await self.service.storage.update_inbound_attachments(
                        message.message_id, tuple(attachments)
                    )
                message = replace(message, attachments=tuple(attachments))
                if inbound.notifies_runtime:
                    await self.contacts.answer_unreviewed(
                        message, recorded.channel_session
                    )
                return
            attachments = list(message.attachments)

            async def download(index: int) -> None:
                attachment = attachments[index]
                attachment = await self.service.import_attachment(
                    inbound.attachments[index], attachment_id=attachment.attachment_id
                )
                await self.service.storage.update_inbound_attachments(
                    message.message_id, (attachment,)
                )
                attachments[index] = attachment

            try:
                async with asyncio.TaskGroup() as group:
                    for index, attachment in enumerate(attachments):
                        if attachment.state == "downloading":
                            group.create_task(download(index))
            except BaseException as error:
                attachments = [
                    replace(
                        attachment,
                        state="failed",
                        error=str(error) or type(error).__name__,
                    )
                    if attachment.state == "downloading"
                    else attachment
                    for attachment in attachments
                ]
                await self.service.storage.update_inbound_attachments(
                    message.message_id, tuple(attachments)
                )
                if not isinstance(error, Exception):
                    raise
                logger.exception(
                    "agent could not download inbound attachments",
                    extra={"agent_id": self.agent.id},
                )
            message = replace(message, attachments=tuple(attachments))
            if message.notifies_runtime:
                await self.wake(message, connection, runtime)
        except Exception:
            logger.exception(
                "agent could not accept inbound message",
                extra={"agent_id": self.agent.id},
            )

    async def start_session(
        self, loop: ActorLoop, runtime: Runtime, connection: Connection
    ) -> None:
        session_id = str(uuid7())
        capability = secrets.token_urlsafe(32)
        mode = (
            Mode.DANGEROUS_INDIVIDUAL
            if isinstance(loop.actor, AgentActor)
            else Mode.SESSION
        )
        names: dict[str, None] = {}
        for item in sorted(
            self.channels.connections.values(), key=lambda item: item.priority
        ):
            identity = item.channel.get_identity()
            names[identity.name or identity.id] = None
        instruction = await asyncio.to_thread(
            DeveloperInstructionContext(
                agent_name=self.agent.name,
                bot_names=tuple(names),
                agent_id=self.agent.id,
                workspace=str(self.agent.workspace),
                mode=mode,
            ).render
        )
        request = SessionInput(
            id=session_id,
            workspace=self.agent.workspace,
            instructions=(instruction,),
            environment={
                "PATH": os.pathsep.join(
                    (str(self.command.parent), os.environ.get("PATH", os.defpath))
                ),
                "BCN_AGENT_ID": self.agent.id,
                "BCN_TOOL_CONFIG": str(self.tool_config),
                "BCN_AGENT_MODE": mode.value,
                "BCN_ACTOR_ID": loop.actor.id,
                "BCN_RUNTIME_SESSION_ID": session_id,
                "BCN_COMMAND_CAPABILITY": capability,
            },
            approval_policy=ApprovalPolicy.NEVER
            if isinstance(loop.actor, AgentActor)
            else ApprovalPolicy.ON_REQUEST,
            sandbox_policy=SandboxPolicy.DANGER_FULL_ACCESS
            if isinstance(loop.actor, AgentActor)
            else runtime.provider.sandbox_policy,
            network_access=runtime.provider.network_access,
        )
        context = AuditContext(
            agent_id=self.agent.id,
            channel=connection.kind,
            harness=runtime.name,
            session_id=session_id,
        )
        await self.audit.publish(
            "harness.session.requested",
            context=context,
            payload=request.model_dump(
                mode="json", exclude={"environment": {"BCN_COMMAND_CAPABILITY"}}
            ),
        )
        binding = states.SessionBinding(
            HarnessSession(id=session_id), runtime, capability
        )
        loop.state = states.Recovering(binding)
        result = await runtime.harness.start_session(request, timeout=600)
        if result.status is ProviderCallStatus.CONFIRMED:
            if result.value is None or result.value.id != session_id:
                raise ValueError("Harness returned a mismatched session")
            binding.session = result.value
            loop.state = states.Idle(binding)
        else:
            failure = cast(HarnessFailure, result.failure)
            loop.state = (
                states.Recovering(binding, failure)
                if failure.needs_reconciliation
                else states.Failed(failure, binding)
            )
        await self.audit.publish(
            "harness.session.started"
            if result.status is ProviderCallStatus.CONFIRMED
            else f"harness.session.{result.status.value}",
            context=context,
            payload={
                "status": result.status.value,
                "session": binding.session.model_dump(mode="json"),
                "failure": asdict(result.failure) if result.failure else None,
            },
        )

    async def close_session(self, loop: ActorLoop) -> None:
        state = loop.state
        binding = None if isinstance(state, states.Dormant) else state.binding
        loop.state = states.Dormant()
        if binding is not None:
            session, runtime = binding.session, binding.runtime
            context = AuditContext(
                agent_id=self.agent.id, harness=runtime.name, session_id=session.id
            )
            await self.audit.publish(
                "harness.session.stopping",
                context=context,
                payload=session.model_dump(mode="json"),
            )
            runtime.pending += 1
            task = asyncio.create_task(
                self.close_runtime(runtime, session, context),
                name=f"harness-close:{session.id}",
            )
            self.teardowns.add(task)

            def done(task: asyncio.Task[None]) -> None:
                self.teardowns.discard(task)
                runtime.pending -= 1
                if not task.cancelled() and (error := task.exception()) is not None:
                    logger.error(
                        "Harness teardown failed",
                        exc_info=(type(error), error, error.__traceback__),
                    )

            task.add_done_callback(done)

    async def close_runtime(
        self, runtime: Runtime, session: HarnessSession, context: AuditContext
    ) -> None:
        try:
            result = await runtime.harness.stop_session(session, timeout=600)
        except Exception as error:
            logger.exception("Harness session stop failed")
            await self.audit.publish(
                "harness.session.stop.failed",
                context=context,
                payload={
                    "error_type": type(error).__name__,
                    "failure": asdict(
                        HarnessFailure(HarnessFailureReason.INTERNAL, str(error))
                    ),
                    "session_id": session.id,
                },
            )
            return
        await self.audit.publish(
            "harness.session.stopped"
            if result.status is ProviderCallStatus.CONFIRMED
            else f"harness.session.stop.{result.status.value}",
            context=context,
            payload={
                "status": result.status.value,
                "session_id": session.id,
                "failure": asdict(result.failure) if result.failure else None,
            },
        )

    async def background(self, loop: ActorLoop) -> bool:
        state = loop.state
        binding = None if isinstance(state, states.Dormant) else state.binding
        if binding is None:
            return False
        try:
            return await binding.runtime.harness.has_background_job(
                binding.session, timeout=600
            )
        except Exception:
            logger.exception("runtime background job check failed")
            return True

    async def receive_events(self, runtime: Runtime) -> None:
        try:
            while True:
                event = await runtime.harness.receive_event()
                bindings = [
                    state.binding
                    for state in (loop.state for loop in self.actor_loops.values())
                    if not isinstance(state, states.Dormant)
                    and state.binding is not None
                ]
                if any(
                    binding.runtime is runtime
                    and binding.session.id == event.session_id
                    for binding in bindings
                ):
                    for binding in bindings:
                        if binding.runtime is runtime:
                            binding.validity = states.Validity.EXPIRED
        except asyncio.CancelledError:
            if (
                runtime.state is states.Lifecycle.RUNNING
                and self.channels.state is states.Lifecycle.RUNNING
            ):
                self.failure.fail(
                    RuntimeError(
                        f"Harness event receiver for {runtime.name} was cancelled unexpectedly"
                    )
                )
            raise
        except BaseException as error:  # noqa: BLE001
            self.failure.fail(error)

    async def reconcile(
        self,
        loop: ActorLoop,
        active: ActiveTurn | None = None,
        approval: ApprovalRoute | None = None,
    ) -> HarnessTurnStream | None:
        state = loop.state
        binding = None if isinstance(state, states.Dormant) else state.binding
        assert binding is not None
        session, runtime = binding.session, binding.runtime
        result = await runtime.harness.reconcile_session(
            session,
            active.turn if active is not None else None,
            approval,
            timeout=60,
        )
        stream = None
        if result.status is ProviderCallStatus.CONFIRMED:
            if result.value is None or result.value.session.id != session.id:
                raise ValueError("Harness reconciled a mismatched session")
            binding.session = result.value.session
            stream = result.value.stream
            loop.state = (
                states.Working(binding) if stream is not None else states.Idle(binding)
            )
        else:
            failure = cast(HarnessFailure, result.failure)
            loop.state = (
                states.Recovering(binding, failure)
                if failure.needs_reconciliation
                else states.Failed(failure, binding)
            )
        await self.audit.publish(
            "harness.session.reconciled",
            context=AuditContext(
                agent_id=self.agent.id, harness=runtime.name, session_id=session.id
            ),
            payload={
                "status": result.status.value,
                "recovered_turn": stream is not None,
                "failure": asdict(result.failure) if result.failure else None,
            },
        )
        return stream

    async def establish(
        self, loop: ActorLoop, runtime: Runtime, connection: Connection
    ) -> bool:
        state = loop.state
        binding = None if isinstance(state, states.Dormant) else state.binding
        if (
            binding is None
            or binding.runtime is not runtime
            or (
                binding.validity is states.Validity.EXPIRED
                and not await self.background(loop)
            )
        ):
            await self.close_session(loop)
        retries = 3
        for attempt in range(retries + 1):
            state = loop.state
            if isinstance(state, states.Dormant) or state.binding is None:
                await self.start_session(loop, runtime, connection)
            if isinstance(loop.state, states.Recovering):
                stream = await self.reconcile(loop)
                if stream is not None:
                    await stream.aclose()
                    raise RuntimeError("Session establishment recovered an active turn")
            if isinstance(loop.state, states.Idle):
                return True
            if attempt == retries:
                return False
            if not isinstance(loop.state, states.Recovering):
                await self.close_session(loop)
            delay = secrets.randbelow(
                min(1_000 << attempt, self.timer.maximum_delay_ms) + 1
            )
            handle = self.timer.create(delay)
            try:
                await handle.wait()
            finally:
                handle.cancel()
        return False

    async def notice(self, loop: ActorLoop) -> str | None:
        summary = await self.service.read_unread_summary(loop.actor, limit=100)
        if not summary.messages:
            return None
        return await asyncio.to_thread(
            inbox_notice,
            summary.messages,
            total_unread_count=summary.total,
            closing_bracket_on_own_line=True,
        )

    async def steer(self, loop: ActorLoop, pending: Sequence[Wakeup]) -> bool:
        active = loop.active
        if active is None or active.state is not states.TurnState.RUNNING:
            return False
        fresh = []
        for item in pending:
            cursor = await self.service.storage.get_consumer_cursor(
                Thread(item.message.thread_id)
            )
            if cursor is None or item.message.seq > cursor.delivered_through_seq:
                fresh.append(item)
        if not fresh:
            return True
        summary = await self.service.read_unread_summary(loop.actor, limit=0)
        text = await asyncio.to_thread(
            inbox_notice,
            tuple(item.message for item in fresh),
            total_unread_count=max(summary.total, len(fresh)),
            closing_bracket_on_own_line=False,
        )
        try:
            accepted = await active.runtime.harness.steer_turn(
                active.session, active.turn, text, timeout=600
            )
        except Exception:
            logger.exception("runtime turn steer failed")
            accepted = False
        if accepted and (
            loop.active is not active or active.state is not states.TurnState.RUNNING
        ):
            accepted = False
        if accepted:
            for item in fresh:
                if item.message.thread_id in active.participants:
                    continue
                active.participants[item.message.thread_id] = item
                self.channels.turns[active.session.id, active.turn.id][
                    item.message.thread_id
                ] = (
                    item.connection,
                    ChannelTarget(
                        kind=item.anchor.target_kind.value,
                        thread_id=item.anchor.provider_thread_id or "",
                        reply_to_message_id=item.anchor.provider_message_id,
                    ),
                )
        await self.audit.publish(
            "harness.turn.steer.accepted"
            if accepted
            else "harness.turn.steer.not_accepted",
            context=active.context,
            payload={"provider_method": "turn/steer"},
        )
        return accepted

    async def run(self, loop: ActorLoop) -> None:
        pending: list[Wakeup] = []
        try:
            while True:
                if not pending:
                    receive = asyncio.create_task(loop.queue.get())
                    delay = None
                    handle = None
                    expiry: asyncio.Task[None] | None = None
                    try:
                        if (
                            not isinstance(loop.state, states.Dormant)
                            and loop.state.binding is not None
                            and self.idle_timeout_ms
                            and loop.queue.empty()
                        ):
                            delay = min(
                                self.timer.maximum_delay_ms, loop.idle_remaining_ms
                            )
                            handle = self.timer.create(delay)
                            expiry = asyncio.create_task(handle.wait())
                        await asyncio.wait(
                            (receive,) if expiry is None else (receive, expiry),
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                    finally:
                        if handle is not None:
                            handle.cancel()
                        receive.cancel()
                        if expiry is not None:
                            expiry.cancel()
                        await asyncio.gather(
                            receive,
                            *((expiry,) if expiry is not None else ()),
                            return_exceptions=True,
                        )
                        if receive.done() and not receive.cancelled():
                            pending.append(receive.result())
                    if delay is not None and not pending:
                        loop.idle_remaining_ms -= delay
                        if loop.idle_remaining_ms <= 0:
                            if await self.background(loop):
                                loop.idle_remaining_ms = self.idle_timeout_ms
                            else:
                                await self.close_session(loop)
                        continue
                pending.extend(loop.drain())
                batch, pending = pending, []
                joined: list[Wakeup] = []
                turn = asyncio.create_task(self.turn(loop, batch))
                loop.task = turn
                receive = asyncio.create_task(loop.queue.get())
                received = False
                quiet = None
                due: asyncio.Task[None] | None = None
                try:
                    while True:
                        await asyncio.wait(
                            (turn, receive) if due is None else (turn, receive, due),
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if receive.done():
                            pending.append(receive.result())
                            pending.extend(loop.drain())
                            received = True
                            if quiet is not None:
                                quiet.cancel()
                            if due is not None:
                                due.cancel()
                                await asyncio.gather(due, return_exceptions=True)
                            quiet = self.timer.create(3_000)
                            due = asyncio.create_task(quiet.wait())
                        if due is not None and due.done():
                            if (
                                not due.cancelled()
                                and due.exception() is None
                                and not turn.done()
                                and await self.steer(loop, pending)
                            ):
                                joined.extend(pending)
                                pending.clear()
                            due = None
                            quiet = None
                        if turn.done():
                            await turn
                            break
                        if receive.done():
                            receive = asyncio.create_task(loop.queue.get())
                            received = False
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception(
                        "agent turn failed",
                        extra={
                            "agent_id": self.agent.id,
                            "actor_id": loop.actor.id,
                        },
                    )
                finally:
                    turn.cancel()
                    receive.cancel()
                    if quiet is not None:
                        quiet.cancel()
                    if due is not None:
                        due.cancel()
                    await asyncio.gather(
                        turn,
                        receive,
                        *((due,) if due is not None else ()),
                        return_exceptions=True,
                    )
                    if receive.done() and not receive.cancelled() and not received:
                        pending.append(receive.result())
                    for item in (*batch, *joined):
                        loop.queue.task_done()
                        item.runtime.pending -= 1
                    loop.task = None
                    loop.idle_remaining_ms = self.idle_timeout_ms
                state = loop.state
                binding = None if isinstance(state, states.Dormant) else state.binding
                if (
                    binding is not None
                    and binding.runtime.state is not states.Lifecycle.RUNNING
                    and not pending
                    and loop.queue.empty()
                ):
                    await self.close_session(loop)
        finally:
            pending.extend(loop.drain())
            for item in pending:
                item.runtime.pending -= 1
                loop.queue.task_done()
            await self.close_session(loop)

    async def consume(
        self, loop: ActorLoop, active: ActiveTurn, stream: HarnessTurnStream
    ) -> HarnessOutput:
        state = loop.state
        binding = None if isinstance(state, states.Dormant) else state.binding
        assert binding is not None
        try:
            async for event in stream:
                if (event.session_id, event.turn_id) != (
                    active.session.id,
                    active.turn.id,
                ):
                    logger.error(
                        "Harness event correlation mismatch",
                        extra={
                            "session_id": active.session.id,
                            "turn_id": active.turn.id,
                            "received_session_id": event.session_id,
                            "received_turn_id": event.turn_id,
                        },
                    )
                    continue
                if event.kind == "started":
                    active.state = states.TurnState.RUNNING
                    loop.state = states.Working(binding)
                terminal = event.kind in {"completed", "failed", "cancelled"}
                await forward(
                    self.audit,
                    active.context,
                    self.channels,
                    event,
                    next(
                        iter(
                            self.channels.turns[
                                active.session.id, active.turn.id
                            ].values()
                        )
                    )[1],
                    deliver=not terminal,
                )
                if terminal:
                    active.state = states.TurnState.FINISHED
                    loop.state = (
                        states.Recovering(binding, event.failure)
                        if event.failure is not None
                        and event.failure.needs_reconciliation
                        else states.Idle(binding)
                    )
                    return event
            outcome = HarnessOutput(
                session_id=active.session.id,
                turn_id=active.turn.id,
                kind="failed",
                text="runtime stream ended without a terminal event",
                failure=HarnessFailure(
                    HarnessFailureReason.CONNECTION_LOST,
                    "Execution stream ended without a terminal event",
                    needs_reconciliation=True,
                ),
            )
            loop.state = states.Recovering(binding, outcome.failure)
            active.state = states.TurnState.FINISHED
            await forward(
                self.audit,
                active.context,
                self.channels,
                outcome,
                next(
                    iter(
                        self.channels.turns[active.session.id, active.turn.id].values()
                    )
                )[1],
                deliver=False,
            )
            return outcome
        finally:
            await stream.aclose()
            await self.audit.publish(
                "harness.stream.closed", context=active.context, payload={}
            )

    async def report_error(self, active: ActiveTurn, outcome: HarnessOutput) -> None:
        from .instruction import template

        sender = ChannelSender(
            self.channels, self.timer, policy=ChannelRetryPolicy(timeout=600)
        )
        failure = cast(HarnessFailure, outcome.failure)
        code = f" ({failure.code})" if failure.code is not None else ""
        text = f"{failure.reason.value.replace('_', ' ')}{code}: {failure.message}"
        body = await asyncio.to_thread(
            lambda: template("runtime_failed.tpl").render(error=text)
        )
        for item in active.participants.values():
            try:
                receipt = await sender.send(
                    ChannelSendRequest(
                        target=ChannelTarget(
                            kind=item.anchor.target_kind.value,
                            thread_id=item.anchor.provider_thread_id or "",
                            reply_to_message_id=item.anchor.provider_message_id,
                        ),
                        body=body,
                        actor_id=item.message.thread_id,
                        channel=item.connection.kind,
                        channel_identity=item.connection.channel.get_identity().id,
                    )
                )
                await self.audit.publish(
                    "harness.error_feedback.sent"
                    if receipt.status in {"confirmed", "queued"}
                    else "harness.error_feedback.failed",
                    context=active.context.model_copy(
                        update={"thread_id": item.message.thread_id}
                    ),
                    payload=receipt.model_dump(mode="json"),
                )
            except Exception:
                logger.exception("runtime error feedback failed")

    async def turn(self, loop: ActorLoop, batch: Sequence[Wakeup]) -> None:
        text = await self.notice(loop)
        if text is None:
            return
        wakeup = batch[0]
        message, anchor, connection = wakeup.message, wakeup.anchor, wakeup.connection
        turn_id = f"turn-{wakeup.wake_id or message.message_id}"
        if await self.database.select(
            Select(
                table="runtime_attempts",
                where=Compare(
                    left=Column(name="turn_id"),
                    operator="=",
                    right=Value(value=turn_id),
                ),
                limit=1,
            )
        ):
            return
        recorded = False
        runtime = (
            wakeup.runtime
            if wakeup.runtime.state is states.Lifecycle.STOPPING
            else self.select(loop=loop) or wakeup.runtime
        )
        attempted: set[Runtime] = set()
        participants: dict[str, Wakeup] = {}
        for item in batch:
            participants.setdefault(item.message.thread_id, item)
        while True:
            attempt = runtime
            attempted.add(runtime)
            runtime.pending += 1
            try:
                try:
                    established = await self.establish(loop, runtime, connection)
                except Exception as error:
                    logger.exception("runtime session failed")
                    state = loop.state
                    binding = (
                        None if isinstance(state, states.Dormant) else state.binding
                    )
                    loop.state = states.Failed(
                        HarnessFailure(
                            HarnessFailureReason.INTERNAL,
                            str(error) or type(error).__name__,
                        ),
                        binding,
                    )
                    established = False
                state = loop.state
                binding = None if isinstance(state, states.Dormant) else state.binding
                session = (
                    binding.session
                    if binding is not None
                    else HarnessSession(id=str(uuid7()))
                )
                turn = HarnessTurn(id=turn_id, session_id=session.id, input_text=text)
                context = AuditContext(
                    agent_id=self.agent.id,
                    channel=connection.kind,
                    harness=runtime.name,
                    target_kind=anchor.target_kind.value,
                    thread_id=message.thread_id,
                    message_id=message.message_id,
                    sender_id=None if anchor.sender is None else anchor.sender.id,
                    session_id=session.id,
                    turn_id=turn.id,
                    provider_session_id=session.provider_thread_id,
                )
                active = ActiveTurn(session, runtime, turn, context, participants)
                loop.active = active
                routes = {
                    item.message.thread_id: (
                        item.connection,
                        ChannelTarget(
                            kind=item.anchor.target_kind.value,
                            thread_id=item.anchor.provider_thread_id or "",
                            reply_to_message_id=item.anchor.provider_message_id,
                        ),
                    )
                    for item in participants.values()
                }
                self.channels.turns[session.id, turn.id] = routes
                target = routes[message.thread_id][1]
                approval = ApprovalRoute(
                    self.channels,
                    target,
                    session.id,
                    turn.id,
                    "" if anchor.sender is None else anchor.sender.id or "",
                    self.audit,
                    context,
                    actor=loop.actor,
                    sender_kind=anchor.sender_kind.value,
                )
                try:
                    if not recorded:
                        await self.database.insert(
                            Insert(
                                table="runtime_attempts",
                                rows=(
                                    {
                                        "turn_id": turn.id,
                                        "session_id": session.id,
                                        "client_user_message_id": wakeup.wake_id
                                        or message.message_id,
                                        "started_at_ms": time_ns() // 1_000_000,
                                        "agent_id": self.agent.id,
                                    },
                                ),
                            )
                        )
                        recorded = True
                    if not established:
                        outcome = HarnessOutput(
                            session_id=session.id,
                            turn_id=turn.id,
                            kind="failed",
                            failure=loop.state.failure
                            if isinstance(
                                loop.state, (states.Recovering, states.Failed)
                            )
                            else None,
                        )
                        await forward(
                            self.audit,
                            context,
                            self.channels,
                            outcome,
                            target,
                            deliver=False,
                        )
                    else:
                        assert binding is not None
                        try:
                            stream = None
                            failure = None
                            for unavailable in range(2):
                                try:
                                    await self.audit.publish(
                                        "harness.turn.requested",
                                        context=context,
                                        payload=turn.model_dump(mode="json"),
                                    )
                                    stream = await runtime.harness.start_turn(
                                        session, turn, approval, timeout=600
                                    )
                                    break
                                except HarnessSessionUnavailable as error:
                                    failure = HarnessFailure(
                                        HarnessFailureReason.UNAVAILABLE, str(error)
                                    )
                                    await self.audit.publish(
                                        "harness.turn.unavailable",
                                        context=context,
                                        payload={
                                            "attempt": unavailable + 1,
                                            "failure": asdict(failure),
                                        },
                                    )
                                    if unavailable:
                                        break
                                    loop.state = states.Recovering(binding)
                                    recovered = await self.reconcile(loop)
                                    if recovered is not None:
                                        await recovered.aclose()
                                        raise RuntimeError(
                                            "Unwritten turn unexpectedly recovered a stream"
                                        )
                                    if not isinstance(loop.state, states.Idle):
                                        failure = cast(
                                            states.Recovering | states.Failed,
                                            loop.state,
                                        ).failure
                                        break
                                    session = binding.session
                                    active.session = session
                            if stream is None:
                                outcome = HarnessOutput(
                                    session_id=session.id,
                                    turn_id=turn.id,
                                    kind="failed",
                                    failure=failure,
                                )
                                await forward(
                                    self.audit,
                                    context,
                                    self.channels,
                                    outcome,
                                    target,
                                    deliver=False,
                                )
                            else:
                                outcome = await self.consume(loop, active, stream)
                            while stream is not None and isinstance(
                                loop.state, states.Recovering
                            ):
                                stream = await self.reconcile(loop, active, approval)
                                if stream is None:
                                    state = loop.state
                                    if (
                                        isinstance(
                                            state, (states.Recovering, states.Failed)
                                        )
                                        and state.failure is not None
                                    ):
                                        outcome = outcome.model_copy(
                                            update={
                                                "failure": state.failure,
                                                "text": state.failure.message,
                                            }
                                        )
                                        await forward(
                                            self.audit,
                                            active.context,
                                            self.channels,
                                            outcome,
                                            target,
                                            deliver=False,
                                        )
                                    break
                                active.session = binding.session
                                active.state = states.TurnState.RUNNING
                                outcome = await self.consume(loop, active, stream)
                        except asyncio.CancelledError:
                            raise
                        except Exception as error:
                            logger.exception(
                                "harness turn failed",
                                extra={
                                    "agent_id": self.agent.id,
                                    "actor_id": loop.actor.id,
                                },
                            )
                            await self.audit.publish(
                                "harness.turn.error",
                                context=context,
                                payload={
                                    "error_type": type(error).__name__,
                                    "error_message": str(error),
                                },
                            )
                            loop.state = states.Idle(binding)
                            outcome = HarnessOutput(
                                session_id=session.id,
                                turn_id=turn.id,
                                kind="failed",
                                failure=HarnessFailure(
                                    HarnessFailureReason.INTERNAL,
                                    str(error) or type(error).__name__,
                                ),
                            )
                            await forward(
                                self.audit,
                                context,
                                self.channels,
                                outcome,
                                target,
                                deliver=False,
                            )
                    active.state = states.TurnState.FINISHED
                    if outcome.kind == "failed":
                        if binding is not None:
                            failure = cast(HarnessFailure, outcome.failure)
                            outcome = outcome.model_copy(
                                update={
                                    "text": outcome.text.replace(
                                        binding.capability, "<redacted>"
                                    ),
                                    "failure": replace(
                                        failure,
                                        message=failure.message.replace(
                                            binding.capability, "<redacted>"
                                        ),
                                        code=failure.code.replace(
                                            binding.capability, "<redacted>"
                                        )
                                        if isinstance(failure.code, str)
                                        else failure.code,
                                    ),
                                }
                            )
                        if not isinstance(loop.state, states.Idle):
                            await self.close_session(loop)
                        runtime.banned_until_ms = self.timer.now_ms + 3_600_000
                        await self.audit.publish(
                            "harness.pool.banned",
                            context=context,
                            payload={
                                "ban_until_ms": runtime.banned_until_ms,
                                "terminal_state": outcome.kind,
                            },
                        )
                        alternative = self.select(exclude=attempted)
                        if alternative is not None:
                            await self.close_session(loop)
                            runtime = alternative
                            continue
                    elif outcome.kind == "completed" and runtime.banned_until_ms:
                        runtime.banned_until_ms = 0
                        await self.audit.publish(
                            "harness.pool.released", context=context, payload={}
                        )
                    try:
                        self.channels.accept_turn_event(outcome, target=target)
                    except Exception:
                        logger.exception("channel rejected terminal event")
                    if outcome.kind == "failed":
                        await self.report_error(active, outcome)
                    return
                except asyncio.CancelledError:
                    await forward(
                        self.audit,
                        context,
                        self.channels,
                        HarnessOutput(
                            session_id=session.id,
                            turn_id=turn.id,
                            kind="cancelled",
                            text="runtime turn cancelled",
                        ),
                        target,
                    )
                    raise
                finally:
                    active.state = states.TurnState.FINISHED
                    loop.active = None
                    self.channels.turns.pop((session.id, turn.id), None)
            finally:
                # The accepted queue keeps its own reference until its Actor loop settles it.
                attempt.pending -= 1

    @asynccontextmanager
    async def lifespan(self):
        async with self.bindings as stack:
            self.service = await stack.enter_async_context(
                self.messages.bind(
                    actors=self.actors,
                    name=self.agent.name,
                    channel=self.channels,
                    workspace=self.agent.workspace,
                    concurrency=self,
                    provider_timeout=600,
                    shutdown_timeout=0,
                    session_binding_validator=self.validate_binding,
                )
            )
            self.contacts = await stack.enter_async_context(
                self.collaboration.bind(
                    messages=self.service,
                    concurrency=self,
                    channel=self.channels,
                    provider_timeout=600,
                )
            )
            await stack.enter_async_context(
                self.reminders.bind(
                    actors=self.actors,
                    wake=self.wake,
                    shutdown_timeout=0,
                    session_binding_validator=self.validate_binding,
                )
            )
            self.dispatcher = asyncio.create_task(
                self.failure.observe(
                    self.dispatch(), component=f"agent-dispatch:{self.agent.id}"
                ),
                name=f"agent-dispatch:{self.agent.id}",
            )
            try:
                yield
            finally:
                await self.stop()
