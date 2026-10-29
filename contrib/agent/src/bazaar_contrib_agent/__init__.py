"""Own agent configuration and assemble Actor loops with existing contribs."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from bazaar_contrib_audit import AUDIT
from bazaar_contrib_channel import (
    CHANNEL,
    CHANNEL_BUILDERS,
    CHANNEL_INPUT,
    ChannelBuilder,
    ChannelInput,
    channel_builder_key,
)
from bazaar_contrib_collaboration import (
    COLLABORATION,
    CONTACT_REVIEWED,
    ContactReviewed,
)
from bazaar_contrib_config import CONFIGURATION
from bazaar_contrib_harness import (
    HARNESS,
    HARNESS_BUILDERS,
    HARNESS_INPUT,
    HarnessBuilder,
    HarnessInput,
    harness_builder_key,
)
from bazaar_contrib_messages import MESSAGES
from bazaar_contrib_messages.states import Review
from bazaar_contrib_reminder import REMINDER
from bazaar_contrib_storage import DB
from bazaar_contrib_timer import TIMER
from bazaar_core import (
    CONTRIB_CHANGED,
    Blocked,
    Composition,
    Context,
    Contrib,
    ContribChange,
    ContribEntry,
    Failed,
    ServiceKey,
    Stopping,
)
from pydantic import BaseModel, ConfigDict

from . import states
from .loop import AgentLoop, Runtime
from .models import Agent, AgentConfig, Config, ProviderConfig
from .registry import AGENTS, SERVICES, AgentInstances, configured, execution
from .routing import Connection
from .tool import Tool
from .wrapper import install, remove

__all__ = [
    "AGENT",
    "AGENTS",
    "Agent",
    "AgentConfig",
    "AgentInstances",
    "Config",
    "ProviderConfig",
    "agent",
    "compose",
    "execution",
]

AGENT = ServiceKey[Agent]("agent.instance")
_LOOP = ServiceKey[AgentLoop]("agent.loop")


class _InstanceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    agent: AgentConfig


class _ChannelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider: ProviderConfig
    builder: ChannelBuilder


class _HarnessConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider: ProviderConfig
    builder: HarnessBuilder
    priority: int


@asynccontextmanager
async def instance_lifespan(ctx: Context, config: _InstanceConfig):
    item = config.agent
    workspace = item.workspace
    if workspace is None:
        root = Path(os.environ.get("BCN_HOME") or await asyncio.to_thread(Path.home))
        workspace = root / ".bcn" / "workspaces" / item.id
    workspace = await asyncio.to_thread(workspace.expanduser)
    workspace = await asyncio.to_thread(workspace.resolve)
    await asyncio.to_thread(workspace.mkdir, parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        await asyncio.to_thread(workspace.chmod, 0o700)
    identity = Agent(id=item.id, name=item.name, workspace=workspace)
    command = await asyncio.to_thread(install, item, workspace)
    identity._command = command
    identity._instance_id = ctx.id
    try:
        ctx.offer(AGENT, identity)
        yield
    finally:
        await asyncio.to_thread(remove, command)


@asynccontextmanager
async def loop_lifespan(ctx: Context, config: _InstanceConfig):
    identity = ctx.accept(AGENT)
    assert identity._command is not None
    registry = ctx.accept(AGENTS)
    async with Tool(ctx.accept(CONFIGURATION)) as tool:
        loop = AgentLoop(
            identity,
            config.agent,
            identity._command,
            ctx.accept(MESSAGES),
            ctx.accept(COLLABORATION),
            ctx.accept(REMINDER),
            ctx.accept(TIMER),
            ctx.accept(AUDIT),
            ctx.accept(DB),
            tool.path,
            ctx.id,
            registry.channel_order,
        )
        identity._loop = loop
        registry.running[identity.id] = identity
        try:
            async with loop.lifespan():
                ctx.offer(_LOOP, loop)
                yield
        finally:
            identity._loop = None
            registry.running.pop(identity.id, None)


@asynccontextmanager
async def channel_lifespan(ctx: Context, config: _ChannelConfig):
    agent = ctx.accept(AGENT)
    loop = ctx.accept(_LOOP)

    @asynccontextmanager
    async def connect(ctx: Context):
        connection = Connection(
            ctx.name,
            config.provider.kind,
            ctx.accept(CHANNEL),
            config.provider,
            loop.channels.order[ctx.name],
        )
        identity = connection.channel.get_identity()
        for member in loop.channels.connections.values():
            if (
                member.state is states.ConnectionState.RUNNING
                and member.kind == connection.kind
                and member.channel.get_identity().id == identity.id
            ):
                positions = sorted(
                    priority
                    for name, priority in loop.channels.order.items()
                    if name.startswith(f"{agent.name}/channel:")
                )
                first, second = sorted(
                    (
                        positions.index(member.priority) + 1,
                        positions.index(connection.priority) + 1,
                    )
                )
                error = ValueError(
                    f"channel {connection.kind} identity {identity.id} is configured "
                    f"twice (positions {first} and {second})"
                )
                loop.failure.fail(error)
                raise error
        loop.channels.connections[connection.name] = connection
        task = group.create_task(loop.receive(connection))
        try:
            yield
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if any(
                record.id in {agent._instance_id, loop.instance_id}
                and isinstance(record.state, Stopping)
                for record in ctx.snapshots
            ):
                await loop.stop()
            await loop.channels.retire(connection, loop.timer, loop.busy)

    composition = config.builder.compose(
        config.provider.model_dump(mode="python", exclude={"kind"}),
        ChannelInput(
            agent_id=agent.id, agent_name=agent.name, workspace=agent.workspace
        ),
    )
    composition = composition.model_copy(
        update={
            "entries": (
                *composition.entries,
                ContribEntry(
                    name=ctx.name,
                    definition=Contrib(accepts=[CHANNEL], lifespan=connect),
                ),
            )
        }
    )
    async with (
        asyncio.TaskGroup() as group,
        ctx.enable(composition, local=(CHANNEL_INPUT, CHANNEL)),
    ):
        yield


@asynccontextmanager
async def harness_lifespan(ctx: Context, config: _HarnessConfig):
    agent = ctx.accept(AGENT)
    loop = ctx.accept(_LOOP)

    @asynccontextmanager
    async def connect(ctx: Context):
        runtime = Runtime(
            config.provider.kind, ctx.accept(HARNESS), config.provider, config.priority
        )
        loop.runtimes[ctx.name] = runtime
        task = asyncio.create_task(
            loop.receive_events(runtime),
            name=f"harness-events:{ctx.name}",
        )
        loop.tasks.add(task)
        task.add_done_callback(loop.tasks.discard)
        try:
            await loop.recover()
            yield
        finally:
            runtime.state = states.Lifecycle.STOPPING
            if any(
                record.id in {agent._instance_id, loop.instance_id}
                and isinstance(record.state, Stopping)
                for record in ctx.snapshots
            ):
                await loop.stop()
            while runtime.pending or any(
                not isinstance(state, states.Dormant)
                and state.binding is not None
                and state.binding.runtime is runtime
                for state in (
                    actor_loop.state for actor_loop in loop.actor_loops.values()
                )
            ):
                failed = any(
                    record.id == ctx.id and isinstance(record.state, (Failed, Blocked))
                    for record in ctx.snapshots
                )
                for actor_loop in loop.actor_loops.values():
                    state = actor_loop.state
                    binding = (
                        None if isinstance(state, states.Dormant) else state.binding
                    )
                    if (
                        binding is not None
                        and binding.runtime is runtime
                        and actor_loop.task is None
                        and actor_loop.queue.empty()
                        and (failed or not await loop.background(actor_loop))
                    ):
                        await loop.close_session(actor_loop)
                await loop.timer.create(max(100, loop.timer.tick_ms)).wait()
            runtime.state = states.Lifecycle.STOPPED
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            del loop.runtimes[ctx.name]

    composition = config.builder.compose(
        config.provider.model_dump(mode="python", exclude={"kind"}),
        HarnessInput(
            agent_id=agent.id,
            agent_name=agent.name,
            workspace=agent.workspace,
            startup_timeout=60,
            shutdown_timeout=5,
        ),
    )
    composition = composition.model_copy(
        update={
            "entries": (
                *composition.entries,
                ContribEntry(
                    name=ctx.name,
                    definition=Contrib(accepts=[HARNESS], lifespan=connect),
                ),
            )
        }
    )
    async with ctx.enable(composition, local=(HARNESS_INPUT, HARNESS)):
        yield


async def stopping(ctx: Context, change: ContribChange) -> None:
    if any(
        record.id == ctx.id and isinstance(record.state, Stopping)
        for record in change.snapshots
    ):
        ctx.accept(_LOOP).drain()


async def contact_reviewed(ctx: Context, event: ContactReviewed) -> None:
    if event.review is Review.APPROVED and ctx.accept(AGENT).id == event.agent.id:
        await ctx.accept(_LOOP).recover(event.thread)


instance = Contrib(config=_InstanceConfig, offers=[AGENT], lifespan=instance_lifespan)
_loop = Contrib(
    config=_InstanceConfig,
    offers=[_LOOP],
    accepts=[*SERVICES, AGENTS, AGENT, CONFIGURATION],
    lifespan=loop_lifespan,
    listen={CONTACT_REVIEWED: contact_reviewed, CONTRIB_CHANGED: stopping},
)
_channel = Contrib(
    config=_ChannelConfig, accepts=[AGENT, _LOOP], lifespan=channel_lifespan
)
_harness = Contrib(
    config=_HarnessConfig, accepts=[AGENT, _LOOP], lifespan=harness_lifespan
)


def compose(
    config: AgentConfig,
    *,
    channel_builders: Mapping[str, ChannelBuilder] | None = None,
    harness_builders: Mapping[str, HarnessBuilder] | None = None,
) -> Composition:
    """Keep the agent declaration stable while individual provider entries change."""
    channel_builders = channel_builders or {}
    harness_builders = harness_builders or {}
    config.validate_providers(channel_builders, harness_builders)
    identity = ContribEntry(
        name=config.name,
        definition=instance,
        config={"agent": config.model_copy(update={"channel": (), "harness": ()})},
        location=f"agents[{config.id}]",
    )
    entries = [identity]
    if not config.channel and not config.harness:
        return Composition(entries=entries)
    loop = ContribEntry(
        name=f"{config.name}/loop",
        definition=_loop,
        config=identity.config,
        bindings={AGENT: identity.ref(AGENT)},
        local=(_LOOP,),
        location=f"agents[{config.id}].loop",
    )
    entries.append(loop)
    for label, providers, builders, definition in (
        ("channel", config.channel, channel_builders, _channel),
        ("harness", config.harness, harness_builders, _harness),
    ):
        positions: dict[str, int] = {}
        for index, provider in enumerate(providers):
            position = positions.get(provider.kind, 0)
            positions[provider.kind] = position + 1
            entries.append(
                ContribEntry(
                    name=f"{config.name}/{label}:{provider.kind}:{position}",
                    definition=definition,
                    config={
                        "provider": provider,
                        "builder": builders[provider.kind],
                        **({"priority": index} if label == "harness" else {}),
                    },
                    bindings={
                        AGENT: identity.ref(AGENT),
                        _LOOP: loop.ref(_LOOP),
                    },
                    location=f"agents[{config.id}].{label}[{provider.kind}:{position}]",
                )
            )
    return Composition(entries=entries)


@asynccontextmanager
async def lifespan(ctx: Context, config: Config):
    channels = {
        provider.kind: ctx.accept(channel_builder_key(provider.kind))
        for item in config.agents
        for provider in item.channel
    }
    harnesses = {
        provider.kind: ctx.accept(harness_builder_key(provider.kind))
        for item in config.agents
        for provider in item.harness
    }
    service = ctx.accept(AGENTS)
    service.error = None
    service.applied.clear()
    service.requests[ctx.id] = config, channels, harnesses
    try:
        yield
    finally:
        service.requests.pop(ctx.id, None)


def dependencies(config: Config) -> list[ServiceKey[Any]]:
    keys: list[ServiceKey[Any]] = [AGENTS]
    channels = {provider.kind for item in config.agents for provider in item.channel}
    harnesses = {provider.kind for item in config.agents for provider in item.harness}
    if channels:
        keys.extend(
            (
                CHANNEL_BUILDERS,
                *(channel_builder_key(kind) for kind in sorted(channels)),
            )
        )
    if harnesses:
        keys.extend(
            (
                HARNESS_BUILDERS,
                *(harness_builder_key(kind) for kind in sorted(harnesses)),
            )
        )
    return keys


agent = Contrib(
    config=Config,
    accepts=dependencies,
    lifespan=lifespan,
    listen={CONTRIB_CHANGED: configured},
)
