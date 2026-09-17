"""The computers and agents as the server knows them right now, read from
what each computer last reported."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .clock import now_ms
from .fields import integer, text
from .storage import Computer, ComputerHealth, IStorage, StoredEvent

PAGE_SIZE = 200
# the beat a node promises when it has not said otherwise
DEFAULT_INTERVAL_MS = 60_000

_TURN_START = "runtime.request.turn.started"
_TURN_BOUNDARIES = (
    _TURN_START,
    "runtime.turn.completed",
    "runtime.turn.failed",
    "runtime.turn.cancelled",
    "runtime.turn.unknown",
)
_INBOUND = "channel.inbound.persisted"


@dataclass(frozen=True, slots=True)
class AgentView:
    id: str
    name: str
    computer_id: str
    computer_name: str
    status: str
    channels: tuple[str, ...]
    runtimes: tuple[str, ...]
    working_on: str | None = None
    working_since_ms: int | None = None


@dataclass(frozen=True, slots=True)
class ComputerView:
    computer: Computer
    online: bool
    last_event_at_ms: int | None
    version: str | None
    python: str | None
    protocol: int | None
    queued: int | None
    agents: tuple[AgentView, ...]


async def fleet(storage: IStorage) -> list[ComputerView]:
    """Every computer with its agents, in a fixed handful of reads however
    many there are."""

    computers = await storage.list_computers(limit=PAGE_SIZE)
    health, boundary_rows = await asyncio.gather(
        storage.computer_health(computers),
        storage.latest_per_agent(
            [computer.id for computer in computers], _TURN_BOUNDARIES
        ),
    )
    boundaries = {(item.computer_id, item.agent_id): item for item in boundary_rows}
    open_turns = [
        (item.computer_id, agent_id, thread_id)
        for item in boundaries.values()
        if item.event_name == _TURN_START
        and (agent_id := item.agent_id) is not None
        and (thread_id := item.thread_id) is not None
    ]
    names = {
        (item.computer_id, item.agent_id, item.thread_id): _target_name(item)
        for item in await storage.latest_per_thread(_INBOUND, open_turns)
    }
    return [_computer_view(item, boundaries, names) for item in health]


def agents_of(computers: Sequence[ComputerView]) -> list[AgentView]:
    return [agent for computer in computers for agent in computer.agents]


def find_agent(
    computers: Sequence[ComputerView], computer_id: str | None, agent_id: str | None
) -> AgentView | None:
    return next(
        (
            agent
            for computer in computers
            for agent in computer.agents
            if computer.computer.id == computer_id and agent.id == agent_id
        ),
        None,
    )


def _target_name(message: StoredEvent) -> str | None:
    """What a conversation is called, from a message seen in it."""

    metadata = message.payload.get("metadata", {})
    return text(metadata.get("target_name")) or text(metadata.get("target"))


def _computer_view(
    item: ComputerHealth,
    boundaries: Mapping[tuple[str, str | None], StoredEvent],
    names: Mapping[tuple[str, str | None, str | None], str | None],
) -> ComputerView:
    health = {} if item.health is None else item.health.payload.get("metadata", {})
    interval = integer(health.get("interval_ms")) or DEFAULT_INTERVAL_MS
    online = (
        item.last_event_at_ms is not None
        and now_ms() - item.last_event_at_ms <= 2 * interval
    )
    agents: list[AgentView] = []
    for record in health.get("agents", []):
        if not isinstance(record, Mapping):
            continue
        agent_id = str(record.get("agent_id", ""))
        boundary = boundaries.get((item.computer.id, agent_id))
        agents.append(
            _agent_view(
                item.computer,
                record,
                online=online,
                boundary=boundary,
                working_on=(
                    None
                    if boundary is None
                    else names.get((item.computer.id, agent_id, boundary.thread_id))
                ),
            )
        )
    audit = health.get("audit")
    if not isinstance(audit, Mapping):
        audit = {}
    return ComputerView(
        computer=item.computer,
        online=online,
        last_event_at_ms=item.last_event_at_ms,
        version=text(health.get("version")),
        python=text(health.get("python")),
        protocol=integer(health.get("protocol")),
        queued=integer(audit.get("queued")),
        agents=tuple(agents),
    )


def _agent_view(
    computer: Computer,
    record: Mapping[str, Any],
    *,
    online: bool,
    boundary: StoredEvent | None,
    working_on: str | None,
) -> AgentView:
    working_since_ms: int | None = None
    status = "running"
    if not online:
        status = "offline"
    elif record.get("status") != "started":
        status = "failed"
    elif boundary is not None and boundary.event_name == _TURN_START:
        # the newest turn boundary says whether a turn is open right now
        status = "busy"
        working_since_ms = boundary.created_at_ms
    else:
        working_on = None
    return AgentView(
        id=str(record.get("agent_id", "")),
        name=str(record.get("name", "")),
        computer_id=computer.id,
        computer_name=computer.name,
        status=status,
        channels=tuple(str(kind) for kind in record.get("channels", [])),
        runtimes=tuple(str(kind) for kind in record.get("runtimes", [])),
        working_on=working_on,
        working_since_ms=working_since_ms,
    )


__all__ = [
    "AgentView",
    "ComputerView",
    "agents_of",
    "find_agent",
    "fleet",
]
