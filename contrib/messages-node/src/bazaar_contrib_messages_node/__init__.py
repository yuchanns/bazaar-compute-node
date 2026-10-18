"""Agent-owned conversations, messages and resource-scoped commands."""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from copy import copy
from pathlib import Path
from typing import cast

from bazaar_contrib_audit import AUDIT, Audit
from bazaar_contrib_invoke import COMMANDS, CommandEntry
from bazaar_contrib_legacy_node_migrations import LEGACY_NODE_MIGRATIONS
from bazaar_contrib_messages import (
    MESSAGES,
    Actors,
    Agent,
    MessageSession,
    SessionBindingValidator,
    ThreadConcurrency,
)
from bazaar_contrib_messages.operations import UnreadMessageOwner
from bazaar_contrib_schema import SCHEMA
from bazaar_contrib_storage import DB, Database, Session
from bazaar_contrib_timer import TIMER, Timer
from bazaar_core import Context, Contrib, ContribSpec
from pydantic import BaseModel, ConfigDict, Field, StrictStr
lazy from bazaar_contrib_channel import Channel
lazy from bazaar_contrib_sidecar import CLIENT, Remote, Symbol

from .attachments import AttachmentMaterializer
from .concurrency import ThreadLockRegistry
from .dispatch import CommandDispatcher
from .repository import Repository
from .repository.unread import list_unread_message_owners
from .schema import MODULE
from .service import NodeMessageService
from .store import MessageStore
lazy from .cli._client import MessageClient
lazy from .cli.inbox import inbox
lazy from .cli.message import message
lazy from .cli.thread import thread

__all__ = [
    "MESSAGES",
    "AttachmentMaterializer",
    "CommandDispatcher",
    "MessageStore",
    "NodeMessageService",
    "NodeMessages",
    "ThreadLockRegistry",
    "commands",
    "inbox_entry",
    "message_entry",
    "messages",
    "thread_entry",
]


class _AgentRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    agent_id: StrictStr = Field(min_length=1)


class NodeMessages:
    """Keep command bindings within their owning agent's lifespan."""

    def __init__(self, database: Database, audit: Audit, timer: Timer):
        self.database = database
        self.audit = audit
        self.timer = timer
        self._bindings: dict[Agent, CommandDispatcher] = {}

    def storage(self, owner: Agent) -> MessageStore:
        return MessageStore(self.database, owner, owner.id)

    def in_session(self, session: Session, owner: Agent) -> MessageSession:
        return Repository(session, owner, owner.id)

    async def list_unread_message_owners(self) -> tuple[UnreadMessageOwner, ...]:
        return await self.database.read(list_unread_message_owners)

    @asynccontextmanager
    async def bind(
        self,
        *,
        actors: Actors,
        name: str,
        channel: Channel,
        workspace: Path,
        concurrency: ThreadConcurrency,
        provider_timeout: float,
        shutdown_timeout: float,
        session_binding_validator: SessionBindingValidator | None = None,
    ) -> AsyncIterator[NodeMessageService]:
        owner = Agent(actors.agent_id)
        if owner in self._bindings:
            raise ValueError(
                f"message commands are already bound for agent: {owner.id}"
            )
        service = NodeMessageService(
            storage=MessageStore(self.database, owner, name),
            audit=self.audit,
            concurrency=concurrency,
            channel=channel,
            workspace=workspace,
            provider_timeout=provider_timeout,
            timer=self.timer,
        )
        dispatcher = CommandDispatcher(
            service, actors=actors, session_binding_validator=session_binding_validator
        )
        dispatcher.start_accepting()
        self._bindings[owner] = dispatcher
        try:
            yield service
        finally:
            dispatcher.stop_accepting()
            del self._bindings[owner]
            await dispatcher.drain(timeout=shutdown_timeout)

    async def request(self, request: Mapping[str, object]) -> Mapping[str, object]:
        owner = Agent(_AgentRequest.model_validate(request).agent_id)
        dispatcher = self._bindings.get(owner)
        if dispatcher is None:
            return {
                "ok": False,
                "code": "SERVICE_NOT_READY",
                "error": "command service is not accepting requests",
            }
        return await dispatcher(request)


@asynccontextmanager
async def lifespan(ctx: Context):
    async with ctx.accept(SCHEMA).register(MODULE):
        ctx.offer(
            MESSAGES, NodeMessages(ctx.accept(DB), ctx.accept(AUDIT), ctx.accept(TIMER))
        )
        yield


@asynccontextmanager
async def cli_lifespan(ctx: Context):
    client = ctx.accept(CLIENT)
    timeout = client.timeout
    client.timeout = None
    try:
        remote = cast(
            Remote,
            await client.context.call(
                "accept", Symbol("bazaar_contrib_messages:MESSAGES")
            ),
        )
        async with AsyncExitStack() as stack:
            for group in (message, inbox, thread):
                group = copy(group)
                group.context_settings = {
                    **group.context_settings,
                    "obj": MessageClient(remote),
                }
                stack.enter_context(ctx.accept(COMMANDS).register(group))
            yield
    finally:
        client.timeout = timeout


messages = Contrib(
    accepts=[SCHEMA, LEGACY_NODE_MIGRATIONS, DB, AUDIT, TIMER],
    offers=[MESSAGES],
    lifespan=lifespan,
)
commands = Contrib(accepts=[CLIENT, COMMANDS], lifespan=cli_lifespan)
_TRANSPORT = ContribSpec(
    contrib="sidecar-windows" if sys.platform == "win32" else "sidecar-unix",
    config={"endpoint": os.environ.get("BCN_ENDPOINT")},
)
message_entry = CommandEntry(
    contrib="messages-cli", help="Message operations", dependencies=(_TRANSPORT,)
)
thread_entry = CommandEntry(
    contrib="messages-cli",
    help="Thread attention operations",
    dependencies=(_TRANSPORT,),
)

inbox_entry = CommandEntry(
    contrib="messages-cli",
    help="Inbox target summary operations",
    dependencies=(_TRANSPORT,),
)
