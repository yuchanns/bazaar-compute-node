"""Browser-owned cursors and the database snapshots they describe."""

from __future__ import annotations

import json
from dataclasses import asdict
from hashlib import sha256
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from .activity import QUIET
from .fleet import AgentHealth, AgentView, ComputerView
from .refs import Refs

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


class Scope(Model):
    topic: Literal[
        "agents",
        "agent-status",
        "agent-info",
        "computers",
        "computer-state",
        "computer-info",
        "computer",
        "contacts",
        "messages",
        "events",
        "activity",
        "health",
        "reminders",
    ]
    computer: Short | None = None
    agent: Short | None = None
    thread: Short | None = None
    until: str | None = None
    review: Literal["approved", "pending"] | None = None

    @model_validator(mode="after")
    def fields(self) -> Self:
        required: set[str] = set()
        optional: set[str] = set()
        if self.topic in ("agents", "computers"):
            optional.add("until")
            if self.until is not None:
                parts = self.until.split("/")
                if len(parts) != (2 if self.topic == "agents" else 1):
                    raise ValueError("invalid list edge")
                for part in parts:
                    TypeAdapter(Short).validate_python(part)
        else:
            required.add("computer")
            if self.topic not in ("computer", "computer-state", "computer-info"):
                required.add("agent")
            if self.topic in ("messages", "reminders"):
                required.add("thread")
            if self.topic == "contacts":
                required.add("review")
        supplied = self.model_fields_set - {"topic"}
        if not required <= supplied or not supplied <= required | optional:
            raise ValueError("fields do not match topic")
        if any(getattr(self, field) is None for field in required):
            raise ValueError("missing scope value")
        return self


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


STATES: dict[str, type[Model]] = {
    "agents": Digest,
    "agent-info": Digest,
    "computers": Digest,
    "computer-info": Digest,
    "computer": Digest,
    "agent-status": AgentStatus,
    "computer-state": ComputerState,
    "contacts": Messages,
    "messages": Messages,
    "events": Since,
    "activity": Activity,
    "health": Activity,
    "reminders": Messages,
}


class Subscription(Model):
    id: str
    scope: Scope
    seen: dict[str, Any] | None

    @model_validator(mode="after")
    def state(self) -> Self:
        if self.seen is not None:
            STATES[self.scope.topic].model_validate(self.seen)
        return self


class PollRequest(Model):
    subscriptions: list[Subscription]

    @model_validator(mode="after")
    def identities(self) -> Self:
        ids = [item.id for item in self.subscriptions]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate subscription id")
        return self


def digest(value: object) -> dict[str, Any]:
    return {
        "digest": sha256(
            json.dumps(
                value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
    }


def agent_info(agent: AgentView) -> dict[str, Any]:
    return digest(
        [
            agent.computer_id,
            agent.id,
            agent.name,
            agent.computer_name,
            agent.system,
            agent.channels,
            agent.runtimes,
        ]
    )


def computer_state(item: ComputerView) -> dict[str, Any]:
    return {
        "online": item.online,
        "last_event_at_ms": item.last_event_at_ms,
        "queued": item.queued,
    }


def computer_info(item: ComputerView) -> dict[str, Any]:
    return digest([item.computer.id, item.computer.name, item.version, item.system])


def computer_detail(item: ComputerView) -> dict[str, Any]:
    return digest(
        [
            item.computer.id,
            item.computer.name,
            item.system,
            [[a.id, a.name, a.channels, a.runtimes] for a in item.agents],
        ]
    )


def activity_state(agent: AgentView, since: int, day: str) -> dict[str, Any]:
    return {
        **digest([agent.name, agent.working_on, agent.working_since_ms]),
        "since": since,
        "day": day,
    }


def health_state(
    health: AgentHealth | None, agent: AgentView, since: int, day: str
) -> dict[str, Any]:
    return {
        **digest(
            [None if health is None else asdict(health), agent.status == "offline"]
        ),
        "since": since,
        "day": day,
    }


def changed(seen: dict[str, Any] | None, current: dict[str, Any]) -> bool:
    if seen is None:
        return True
    return any(
        (value > seen[key] if key == "since" else value != seen[key])
        for key, value in current.items()
    )


class Descriptors:
    """The same state functions for initial HTML and subsequent checks."""

    def __init__(self, refs: Refs) -> None:
        self.refs = refs

    def __call__(
        self,
        topic: str,
        value: Any,
        *,
        since: int = 0,
        thread: str | None = None,
        review: str = "approved",
        seen: dict[str, Any] | None = None,
        failed: bool = False,
    ) -> dict[str, Any]:
        scope: dict[str, Any] = {"topic": topic}
        match topic:
            case "agents":
                seen = digest(
                    [[[a.computer_id, a.id] for a in value.agents], value.more]
                )
                scope["until"] = None
            case "computers":
                seen = digest([[c.computer.id for c in value.computers], value.more])
                scope["until"] = None
            case "computer" | "computer-info" | "computer-state":
                scope["computer"] = str(self.refs.ref(value.computer.id))
                seen = {
                    "computer": computer_detail,
                    "computer-info": computer_info,
                    "computer-state": computer_state,
                }[topic](value)
            case _:
                scope.update(
                    computer=str(self.refs.ref(value.computer_id)),
                    agent=str(self.refs.ref(value.id)),
                )
                if thread is not None:
                    scope["thread"] = str(self.refs.ref(thread))
                if topic == "contacts":
                    scope["review"] = review
                if topic == "agent-status":
                    seen = {"status": value.status}
                elif topic == "agent-info":
                    seen = agent_info(value)
                elif topic in ("contacts", "messages", "reminders"):
                    seen = {"since": since, "offline": value.status == "offline"}
                elif topic == "events":
                    seen = {"since": since}
        return {"scope": scope, "seen": None if failed else seen}
