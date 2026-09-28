"""Registered browser subscriptions and their request-local snapshots."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from .access import Access
from .activity import QUIET
from .fleet import (
    AgentView,
    ComputerView,
    agent_health,
    agent_page,
    computer_view,
    fleet,
)
from .permissions import POLL_HANDLERS, PermissionHandler
from .poll_rendering import (
    activity_state,
    agent_info,
    computer_detail,
    computer_info,
    computer_state,
    digest,
    health_state,
)
from .storage import IStorage

REMINDERS = (
    "reminder.scheduled",
    "reminder.snoozed",
    "reminder.updated",
    "reminder.canceled",
    "reminder.fired",
)
CARD_QUIET = tuple(name for name in QUIET if name != "usage.updated")
Short = Annotated[str, Field(pattern=r"^[0-9]{1,18}$")]
Cursor = Annotated[int, Field(ge=0)]
Status = Literal["running", "busy", "failed", "offline"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Digest(Model):
    digest: str


class Since(Model):
    since: Cursor


class Messages(Since):
    offline: bool


class Activity(Since, Digest):
    day: str


class AgentStatus(Model):
    status: Status


class ComputerState(Model):
    online: bool
    last_event_at_ms: int | None
    queued: int | None


class Scope(Model):
    topic: str
    computer: Short | None = None
    agent: Short | None = None
    thread: Short | None = None
    until: str | None = None
    review: Literal["approved", "pending"] | None = None

    @model_validator(mode="after")
    def fields(self) -> Self:
        topic = TOPICS.get(self.topic)
        if topic is None:
            raise ValueError("unknown topic")
        required = set(topic.required)
        optional = {"until"} if topic.edge_parts else set()
        supplied = self.model_fields_set - {"topic"}
        if not required <= supplied or not supplied <= required | optional:
            raise ValueError("fields do not match topic")
        if any(getattr(self, name) is None for name in required):
            raise ValueError("missing scope value")
        if self.until is not None:
            parts = self.until.split("/")
            if len(parts) != topic.edge_parts:
                raise ValueError("invalid list edge")
            for part in parts:
                TypeAdapter(Short).validate_python(part)
        return self


class Subscription(Model):
    id: str
    scope: Scope
    seen: dict[str, Any] | None

    @model_validator(mode="after")
    def state(self) -> Self:
        if self.seen is not None:
            TOPICS[self.scope.topic].state.model_validate(self.seen)
        return self


class PollRequest(Model):
    subscriptions: list[Subscription]

    @model_validator(mode="after")
    def identities(self) -> Self:
        ids = [item.id for item in self.subscriptions]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate subscription id")
        return self


@dataclass(slots=True)
class PollContext:
    storage: IStorage
    access: Access
    ids: dict[str, str | None]
    day: str
    computers: dict[str, ComputerView | None] = field(default_factory=dict)

    async def computer(self, scope: Scope) -> ComputerView | None:
        computer_id = self.ids.get(scope.computer or "")
        if computer_id is None:
            return None
        if computer_id not in self.computers:
            self.computers[computer_id] = await computer_view(
                self.storage, self.access, computer_id
            )
        return self.computers[computer_id]

    async def agent(self, scope: Scope) -> AgentView | None:
        computer = await self.computer(scope)
        if computer is None:
            return None
        return next(
            (
                agent
                for agent in computer.agents
                if agent.id == self.ids.get(scope.agent or "")
            ),
            None,
        )

    def edge(self, scope: Scope) -> str | None:
        return (
            "/".join(self.ids[part] or "" for part in scope.until.split("/"))
            if scope.until
            else None
        )


type Fetch = Callable[[PollContext, Scope], Awaitable[dict[str, Any] | None]]


@dataclass(frozen=True, slots=True)
class Topic:
    fetch: Fetch
    state: type[Model]
    permission: PermissionHandler
    required: tuple[str, ...]
    edge_parts: int = 0
    inline: bool = False
    priority: int = 0


TOPICS: dict[str, Topic] = {}


def register(
    name: str,
    state: type[Model],
    *,
    required: tuple[str, ...] = (),
    edge_parts: int = 0,
    inline: bool = False,
    priority: int = 0,
) -> Callable[[Fetch], Fetch]:
    def attach(fetch: Fetch) -> Fetch:
        TOPICS[name] = Topic(
            fetch, state, POLL_HANDLERS[name], required, edge_parts, inline, priority
        )
        return fetch

    return attach


@register("agents", Digest, edge_parts=2)
async def agents(context: PollContext, scope: Scope) -> dict[str, Any]:
    page = await agent_page(context.storage, context.access, until=context.edge(scope))
    return digest([[[agent.computer_id, agent.id] for agent in page.agents], page.more])


@register("computers", Digest, edge_parts=1, priority=-1)
async def computers(context: PollContext, scope: Scope) -> dict[str, Any]:
    page = await fleet(context.storage, context.access, until=context.edge(scope))
    context.computers.update((view.computer.id, view) for view in page.computers)
    return digest([[view.computer.id for view in page.computers], page.more])


@register("computer", Digest, required=("computer",))
async def detail(context: PollContext, scope: Scope) -> dict[str, Any] | None:
    view = await context.computer(scope)
    return None if view is None else computer_detail(view)


@register("computer-info", Digest, required=("computer",))
async def computer_information(
    context: PollContext, scope: Scope
) -> dict[str, Any] | None:
    view = await context.computer(scope)
    return None if view is None else computer_info(view)


@register("computer-state", ComputerState, required=("computer",), inline=True)
async def computer_status(context: PollContext, scope: Scope) -> dict[str, Any] | None:
    view = await context.computer(scope)
    return None if view is None else computer_state(view)


@register("agent-status", AgentStatus, required=("computer", "agent"), inline=True)
async def agent_status(context: PollContext, scope: Scope) -> dict[str, Any] | None:
    agent = await context.agent(scope)
    return None if agent is None else {"status": agent.status}


@register("agent-info", Digest, required=("computer", "agent"))
async def agent_information(
    context: PollContext, scope: Scope
) -> dict[str, Any] | None:
    agent = await context.agent(scope)
    return None if agent is None else agent_info(agent)


@register("contacts", Messages, required=("computer", "agent", "review"))
@register("messages", Messages, required=("computer", "agent", "thread"))
async def messages(context: PollContext, scope: Scope) -> dict[str, Any] | None:
    agent = await context.agent(scope)
    if agent is None:
        return None
    since = await context.storage.latest_message_event(
        agent.computer_id, agent.id, thread_id=context.ids.get(scope.thread or "")
    )
    return {"since": since, "offline": agent.status == "offline"}


@register("events", Since, required=("computer", "agent"))
async def events(context: PollContext, scope: Scope) -> dict[str, Any] | None:
    agent = await context.agent(scope)
    if agent is None:
        return None
    return {
        "since": await context.storage.latest_activity_event(
            agent.computer_id, agent.id, skipping=QUIET
        )
    }


@register("activity", Activity, required=("computer", "agent"))
async def activity(context: PollContext, scope: Scope) -> dict[str, Any] | None:
    agent = await context.agent(scope)
    if agent is None:
        return None
    since = await context.storage.latest_activity_event(
        agent.computer_id, agent.id, skipping=CARD_QUIET
    )
    return activity_state(agent, since, context.day)


@register("health", Activity, required=("computer", "agent"))
async def health(context: PollContext, scope: Scope) -> dict[str, Any] | None:
    agent = await context.agent(scope)
    if agent is None:
        return None
    since = await context.storage.latest_named_event(
        agent.computer_id, agent.id, names=("usage.updated",)
    )
    health = await agent_health(context.storage, agent.computer_id, agent.id)
    return health_state(health, agent, since, context.day)


@register("reminders", Messages, required=("computer", "agent", "thread"))
async def reminders(context: PollContext, scope: Scope) -> dict[str, Any] | None:
    agent = await context.agent(scope)
    if agent is None:
        return None
    since = await context.storage.latest_named_event(
        agent.computer_id,
        agent.id,
        names=REMINDERS,
        thread_id=context.ids.get(scope.thread or ""),
    )
    return {"since": since, "offline": agent.status == "offline"}
