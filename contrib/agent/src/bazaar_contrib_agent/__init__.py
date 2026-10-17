"""Own agent configuration and assemble each instance in a local composition."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any
lazy import asyncio
lazy import os
lazy from pathlib import Path

from bazaar_contrib_audit import AUDIT
from bazaar_contrib_channel import (
    CHANNEL,
    CHANNEL_BUILDERS,
    CHANNEL_INPUT,
    ChannelBuilder,
    ChannelInput,
    channel_builder_key,
)
from bazaar_contrib_harness import (
    HARNESS,
    HARNESS_BUILDERS,
    HARNESS_INPUT,
    HarnessBuilder,
    HarnessInput,
    harness_builder_key,
)
from bazaar_core import (
    Composition,
    Context,
    Contrib,
    ContribEntry,
    Enablement,
    ServiceKey,
)
from pydantic import BaseModel, ConfigDict

from .loop import loop
from .models import Agent, AgentConfig, Config, ProviderConfig

__all__ = [
    "AGENT",
    "Agent",
    "AgentConfig",
    "Config",
    "ProviderConfig",
    "agent",
    "compose",
]

AGENT = ServiceKey[Agent]("agent.instance")


class _InstanceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    agent: AgentConfig
    channels: tuple[ChannelBuilder, ...] = ()
    harnesses: tuple[HarnessBuilder, ...] = ()


async def operate(
    ctx: Context,
    agent: Agent,
    config: AgentConfig,
    channel_builders: tuple[ChannelBuilder, ...],
    harness_builders: tuple[HarnessBuilder, ...],
):
    async with AsyncExitStack() as stack:
        channels: list[Enablement] = []
        harnesses: list[Enablement] = []
        for provider, builder in zip(config.channel, channel_builders):
            channel = await stack.enter_async_context(
                ctx.enable(
                    builder.compose(
                        provider.model_dump(mode="python", exclude={"kind"}),
                        ChannelInput(
                            agent_id=agent.id,
                            agent_name=agent.name,
                            workspace=agent.workspace,
                        ),
                    ),
                    local=(CHANNEL_INPUT, CHANNEL),
                )
            )
            channels.append(channel)
        for provider, builder in zip(config.harness, harness_builders):
            harness = await stack.enter_async_context(
                ctx.enable(
                    builder.compose(
                        provider.model_dump(mode="python", exclude={"kind"}),
                        HarnessInput(
                            agent_id=agent.id,
                            agent_name=agent.name,
                            workspace=agent.workspace,
                            mode=config.mode,
                        ),
                    ),
                    local=(HARNESS_INPUT, HARNESS),
                )
            )
            harnesses.append(harness)
        if harnesses:
            harness = harnesses[0]
            for provider, channel in zip(config.channel, channels):
                await stack.enter_async_context(
                    ctx.enable(
                        Composition(
                            entries=(
                                ContribEntry(
                                    name="agent-loop",
                                    definition=loop,
                                    config={
                                        "agent": agent,
                                        "channel": provider.kind,
                                        "harness": config.harness[0].kind,
                                    },
                                    bindings={
                                        CHANNEL: channel.ref(CHANNEL),
                                        HARNESS: harness.ref(HARNESS),
                                    },
                                ),
                            )
                        )
                    )
                )
        await asyncio.Event().wait()


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
    ctx.offer(AGENT, identity)
    async with asyncio.TaskGroup() as group:
        task = group.create_task(
            operate(
                ctx,
                identity,
                item,
                config.channels,
                config.harnesses,
            ),
            name=f"agent:{item.id}",
        )
        try:
            yield
        finally:
            task.cancel()


instance = Contrib(
    config=_InstanceConfig,
    offers=[AGENT],
    lifespan=instance_lifespan,
)


def compose(
    config: AgentConfig,
    *,
    channel_builders: Mapping[str, ChannelBuilder] | None = None,
    harness_builders: Mapping[str, HarnessBuilder] | None = None,
) -> Composition:
    """Build one agent's known instances before acquiring their resources."""
    channel_builders = channel_builders or {}
    harness_builders = harness_builders or {}
    config.validate_providers(channel_builders, harness_builders)
    return Composition(
        entries=(
            ContribEntry(
                name=config.name,
                definition=instance,
                config={
                    "agent": config,
                    "channels": tuple(
                        channel_builders[item.kind] for item in config.channel
                    ),
                    "harnesses": tuple(
                        harness_builders[item.kind] for item in config.harness
                    ),
                },
                location=f"agents[{config.id}]",
            ),
        )
    )


@asynccontextmanager
async def lifespan(ctx: Context, config: Config):
    async with AsyncExitStack() as stack:
        for item in config.agents:
            channels = {
                provider.kind: ctx.accept(channel_builder_key(provider.kind))
                for provider in item.channel
            }
            harnesses = {
                provider.kind: ctx.accept(harness_builder_key(provider.kind))
                for provider in item.harness
            }
            await stack.enter_async_context(
                ctx.enable(
                    compose(
                        item, channel_builders=channels, harness_builders=harnesses
                    ),
                    local=(AGENT,),
                )
            )
        yield


def dependencies(config: Config) -> list[ServiceKey[Any]]:
    keys: list[ServiceKey[Any]] = [AUDIT]
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


agent = Contrib(config=Config, accepts=dependencies, lifespan=lifespan)
