"""Own agent configuration and assemble each instance in a local composition."""

from __future__ import annotations

from contextlib import AsyncExitStack, asynccontextmanager
lazy import asyncio
lazy import os
lazy from pathlib import Path

from bazaar_core import Composition, Context, Contrib, ContribEntry, ServiceKey

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


@asynccontextmanager
async def instance_lifespan(ctx: Context, config: AgentConfig):
    workspace = config.workspace
    if workspace is None:
        root = Path(os.environ.get("BCN_HOME") or await asyncio.to_thread(Path.home))
        workspace = root / ".bcn" / "workspaces" / config.id
    workspace = await asyncio.to_thread(workspace.expanduser)
    workspace = await asyncio.to_thread(workspace.resolve)
    await asyncio.to_thread(workspace.mkdir, parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        await asyncio.to_thread(workspace.chmod, 0o700)
    ctx.offer(AGENT, Agent(id=config.id, name=config.name, workspace=workspace))
    yield


instance = Contrib(config=AgentConfig, offers=[AGENT], lifespan=instance_lifespan)


def compose(config: AgentConfig) -> Composition:
    """Build one agent's known instances before acquiring their resources."""
    return Composition(
        entries=(
            ContribEntry(
                name=config.name,
                definition=instance,
                config=config.model_dump(mode="python"),
                location=f"agents[{config.id}]",
            ),
        )
    )


@asynccontextmanager
async def lifespan(ctx: Context, config: Config):
    async with AsyncExitStack() as stack:
        for item in config.agents:
            await stack.enter_async_context(ctx.enable(compose(item), local=(AGENT,)))
        yield


agent = Contrib(config=Config, lifespan=lifespan)
