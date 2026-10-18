"""Agent-owned admission, review and inbound conversation association."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from bazaar_contrib_audit import AUDIT, Audit
from bazaar_contrib_channel import Channel, ChannelRetryPolicy, ChannelSender
from bazaar_contrib_legacy_node_migrations import LEGACY_NODE_MIGRATIONS
from bazaar_contrib_messages import (
    MESSAGES,
    Agent,
    MessageService,
    ThreadConcurrency,
)
from bazaar_contrib_schema import SCHEMA
from bazaar_contrib_storage import DB, Database
from bazaar_contrib_timer import TIMER, Timer
from bazaar_core import Context, Contrib, ServiceKey

from .review import REVIEW_REPLY
from .schema import MODULE
from .service import CollaborationService
from .store import CollaborationStore

__all__ = [
    "COLLABORATION",
    "REVIEW_REPLY",
    "Collaboration",
    "CollaborationService",
    "CollaborationStore",
    "collaboration",
]


class Collaboration:
    """Keep each agent's collaboration binding with its message service."""

    def __init__(self, database: Database, audit: Audit, timer: Timer):
        self.database = database
        self.audit = audit
        self.timer = timer
        self._bindings: dict[Agent, CollaborationService] = {}

    @asynccontextmanager
    async def bind(
        self,
        *,
        messages: MessageService,
        concurrency: ThreadConcurrency,
        channel: Channel,
        provider_timeout: float,
    ) -> AsyncIterator[CollaborationService]:
        owner = messages.storage.owner
        if owner in self._bindings:
            raise ValueError(f"collaboration is already bound for agent: {owner.id}")
        service = CollaborationService(
            messages=messages,
            storage=CollaborationStore(self.database, owner),
            audit=self.audit,
            concurrency=concurrency,
            sender=ChannelSender(
                channel,
                self.timer,
                policy=ChannelRetryPolicy(timeout=provider_timeout),
            ),
        )
        self._bindings[owner] = service
        try:
            yield service
        finally:
            del self._bindings[owner]

    def for_agent(self, owner: Agent) -> CollaborationService:
        service = self._bindings.get(owner)
        if service is None:
            raise ValueError("Agent is not available")
        return service


COLLABORATION = ServiceKey[Collaboration]("collaboration")


@asynccontextmanager
async def lifespan(ctx: Context):
    async with ctx.accept(SCHEMA).register(MODULE):
        ctx.offer(
            COLLABORATION,
            Collaboration(ctx.accept(DB), ctx.accept(AUDIT), ctx.accept(TIMER)),
        )
        yield


collaboration = Contrib(
    accepts=[SCHEMA, LEGACY_NODE_MIGRATIONS, MESSAGES, DB, AUDIT, TIMER],
    offers=[COLLABORATION],
    lifespan=lifespan,
)
