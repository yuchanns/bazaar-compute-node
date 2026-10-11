"""Application composition and concrete agent/provider input contracts."""

from __future__ import annotations

from contextlib import asynccontextmanager
lazy from collections.abc import AsyncIterator

from bazaar_core import ServiceKey
lazy from bazaar_contrib_config import AgentConfig, ProviderConfig
lazy from bazaar_core import Composition, ContribEntry, ContribHub

__all__ = ["AGENT", "CHANNEL", "RUNTIME", "run"]

AGENT = ServiceKey[AgentConfig]("node.agent")
CHANNEL = ServiceKey[ProviderConfig]("node.channel")
RUNTIME = ServiceKey[ProviderConfig]("node.runtime")


@asynccontextmanager
async def run(bootstrap: Composition, load: ContribEntry) -> AsyncIterator[ContribHub]:
    """Run a caller's bootstrap and loader in one concrete instance graph."""
    async with ContribHub().run(bootstrap, load) as hub:
        yield hub
