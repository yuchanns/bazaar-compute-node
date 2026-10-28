"""Agent-owned admission, review and inbound conversation association."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from bazaar_contrib_audit import AUDIT, Audit
from bazaar_contrib_channel import Channel, ChannelRetryPolicy, ChannelSender
from bazaar_contrib_legacy_node_migrations import LEGACY_NODE_MIGRATIONS
from bazaar_contrib_messages import (
    MESSAGES,
    Agent,
    MessageService,
    Thread,
    ThreadConcurrency,
)
from bazaar_contrib_messages.models import InboxTargetSummary
from bazaar_contrib_messages.states import Review
from bazaar_contrib_schema import SCHEMA
from bazaar_contrib_sidecar import RemoteEntry
from bazaar_contrib_storage import DB, Database
from bazaar_contrib_timer import TIMER, Timer
from bazaar_core import Context, Contrib, ServiceKey

from .events import CONTACT_REVIEWED, ContactReviewed
from .review import REVIEW_REPLY
from .schema import MODULE
from .service import CollaborationService
from .store import CollaborationStore

__all__ = [
    "COLLABORATION",
    "CONTACT_REVIEWED",
    "REVIEW_REPLY",
    "Collaboration",
    "CollaborationService",
    "CollaborationStore",
    "ContactReviewed",
    "collaboration",
    "remote",
]


class Collaboration:
    """Keep each agent's collaboration binding with its message service."""

    def __init__(
        self, database: Database, audit: Audit, timer: Timer, context: Context
    ):
        self.database = database
        self.audit = audit
        self.timer = timer
        self.context = context
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
            context=self.context,
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

    async def pending_contacts(self, agent_id: str) -> tuple[InboxTargetSummary, ...]:
        owner = Agent(agent_id)
        inbox = await self.for_agent(owner).messages.check_inbox(
            owner, pending_only=False, limit=None, review=Review.PENDING
        )
        return inbox.targets

    async def setting(self, agent_id: str, key: Literal["review.reply"]) -> str | None:
        return await CollaborationStore(self.database, Agent(agent_id)).get_setting(key)

    async def set_setting(
        self, agent_id: str, key: Literal["review.reply"], value: str
    ) -> None:
        await self.for_agent(Agent(agent_id)).set_setting(key, value)

    async def review_contact(self, thread_id: str, decision: Review) -> Review:
        thread = Thread(thread_id)
        for service in self._bindings.values():
            if await service.messages.storage.get_thread(thread) is not None:
                return (await service.review_contact(thread, decision)).review
        raise ValueError(f"conversation not found: {thread_id}")


COLLABORATION = ServiceKey[Collaboration]("collaboration")


@asynccontextmanager
async def lifespan(ctx: Context):
    async with ctx.accept(SCHEMA).register(MODULE):
        ctx.offer(
            COLLABORATION,
            Collaboration(ctx.accept(DB), ctx.accept(AUDIT), ctx.accept(TIMER), ctx),
        )
        yield


collaboration = Contrib(
    accepts=[SCHEMA, LEGACY_NODE_MIGRATIONS, MESSAGES, DB, AUDIT, TIMER],
    offers=[COLLABORATION],
    lifespan=lifespan,
)

remote = RemoteEntry(
    COLLABORATION, ("pending_contacts", "review_contact", "setting", "set_setting")
)
