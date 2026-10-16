"""Application composition and concrete agent/provider input contracts."""

from __future__ import annotations

from contextlib import asynccontextmanager
lazy from collections.abc import AsyncIterator, Awaitable, Callable

from bazaar_core import ServiceKey
lazy from bazaar_contrib_audit import AUDIT_EVENT, AuditEvent
lazy from bazaar_contrib_config import AgentConfig, ProviderConfig
lazy from bazaar_core import Composition, ContribEntry, ContribHub

__all__ = ["AGENT", "CHANNEL", "RUNTIME", "run"]

AGENT = ServiceKey[AgentConfig]("node.agent")
CHANNEL = ServiceKey[ProviderConfig]("node.channel")
RUNTIME = ServiceKey[ProviderConfig]("node.runtime")


@asynccontextmanager
async def run(
    bootstrap: Composition,
    load: ContribEntry,
    *,
    prepare: Callable[[ContribHub], Awaitable[None]] | None = None,
) -> AsyncIterator[ContribHub]:
    """Run a caller's bootstrap and loader in one concrete instance graph."""
    async with ContribHub().run(bootstrap, load) as hub:
        if prepare is not None:
            await prepare(hub)
        await hub.announce(AUDIT_EVENT, AuditEvent.capture(event_name="node.started"))
        try:
            yield hub
        finally:
            await hub.announce(
                AUDIT_EVENT, AuditEvent.capture(event_name="node.stopping")
            )
