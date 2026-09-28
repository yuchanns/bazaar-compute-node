"""State descriptions shared by initial HTML and registered poll handlers."""

from __future__ import annotations

import json
from dataclasses import asdict
from hashlib import sha256
from typing import Any

from .fleet import AgentHealth, AgentView, ComputerView
from .refs import Refs


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
