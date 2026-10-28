from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager

from bazaar_contrib_audit import Audit
from bazaar_contrib_messages import Actors, Agent, Messages, SessionBindingValidator
from bazaar_contrib_messages.models import InboundAttachment, Message
from bazaar_contrib_storage import Database
from bazaar_contrib_timer import Timer
from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError

from .audit import ReminderAudit
from .commands import ReminderCommands
from .concurrency import ThreadLockRegistry
from .dispatch import CommandDispatcher
from .scheduler import ReminderScheduler
from .store import ReminderStore


class _AgentRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    agent_id: StrictStr = Field(min_length=1)


type Wake = Callable[[Message[InboundAttachment]], Awaitable[bool]]


class Reminders:
    """Bind agent commands and own the node's durable Reminder scheduler."""

    def __init__(
        self, *, database: Database, messages: Messages, timer: Timer, audit: Audit
    ):
        self.storage = ReminderStore(database, messages)
        self._audit = ReminderAudit(audit)
        self._timer = timer
        self._concurrency = ThreadLockRegistry()
        self._bindings: dict[Agent, tuple[CommandDispatcher, Wake]] = {}
        self._shutdown_timeout: float | None = None
        self.scheduler = ReminderScheduler(
            storage=self.storage,
            timer=timer,
            concurrency=self._concurrency,
            wake=self._wake,
            audit=self._audit,
        )

    async def start(self, *, timeout: float, shutdown_timeout: float) -> None:  # noqa: ASYNC109
        self._shutdown_timeout = shutdown_timeout
        await self.scheduler.start(timeout=timeout)

    async def stop(self) -> None:
        if self._shutdown_timeout is not None:
            await self.scheduler.stop(timeout=self._shutdown_timeout)

    async def wait_failure(self) -> None:
        await self.scheduler.wait_failure()

    @asynccontextmanager
    async def bind(
        self,
        *,
        actors: Actors,
        wake: Wake,
        shutdown_timeout: float,
        session_binding_validator: SessionBindingValidator | None = None,
    ) -> AsyncIterator[ReminderCommands]:
        owner = Agent(actors.agent_id)
        if owner in self._bindings:
            raise ValueError(
                f"Reminder commands are already bound for agent: {owner.id}"
            )
        service = ReminderCommands(
            actors=actors,
            storage=self.storage.for_agent(owner),
            audit=self._audit,
            poke=self.scheduler.poke,
            reminder_concurrency=self._concurrency,
        )
        dispatcher = CommandDispatcher(
            service,
            actors=actors,
            timer=self._timer,
            session_binding_validator=session_binding_validator,
        )
        dispatcher.start_accepting()
        self._bindings[owner] = dispatcher, wake
        try:
            yield service
        finally:
            dispatcher.stop_accepting()
            del self._bindings[owner]
            await dispatcher.drain(timeout=shutdown_timeout)

    async def request(self, request: Mapping[str, object]) -> Mapping[str, object]:
        try:
            owner = Agent(_AgentRequest.model_validate(request).agent_id)
        except ValidationError:
            return {
                "ok": False,
                "code": "AGENT_REQUIRED",
                "error": "agent_id must be a non-empty string",
            }
        binding = self._bindings.get(owner)
        if binding is None:
            return {
                "ok": False,
                "code": "AGENT_NOT_AVAILABLE",
                "error": "Agent is not available",
            }
        return await binding[0](request)

    async def _wake(self, owner: Agent, message: Message[InboundAttachment]) -> bool:
        binding = self._bindings.get(owner)
        if binding is None:
            logging.getLogger(__name__).warning(
                "reminder.wake.agent_unavailable",
                extra={"agent_id": owner.id, "thread_id": message.thread_id},
            )
            return False
        return await binding[1](message)
