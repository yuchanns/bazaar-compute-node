"""The computers and agents as the server knows them right now, read from
what each computer last reported."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .clock import now_ms
from .storage import Computer, ComputerHealth, IStorage, StoredEvent

# a page is what one scroll of the list asks for; the next page is asked for
# when the end of this one comes into view
PAGE_SIZE = 50
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
    system: str | None
    queued: int | None
    agents: tuple[AgentView, ...]


@dataclass(frozen=True, slots=True)
class Fleet:
    """A stretch of the computer list with their agents. `edge` is the id
    it ends at, nothing when it is empty; `more` says whether the list goes
    on past it."""

    computers: list[ComputerView]
    edge: str | None
    more: bool


async def fleet(
    storage: IStorage, *, after: str | None = None, until: str | None = None
) -> Fleet:
    """The page of computers past `after`, or everything up to `until`, with
    their agents, in a fixed handful of reads however many there are."""

    if until is None:
        # one past the page says whether there is a next one
        computers = await storage.list_computers(after=after, limit=PAGE_SIZE + 1)
        more = len(computers) > PAGE_SIZE
        computers = computers[:PAGE_SIZE]
    else:
        computers, beyond = await asyncio.gather(
            storage.list_computers(until=until),
            storage.list_computers(after=until, limit=1),
        )
        more = bool(beyond)
    return Fleet(
        computers=await _views(storage, computers),
        edge=computers[-1].id if computers else None,
        more=more,
    )


async def computer_view(storage: IStorage, computer_id: str) -> ComputerView | None:
    """One computer with its agents, whichever page it is on."""

    computer = await storage.find_computer(computer_id)
    if computer is None:
        return None
    views = await _views(storage, [computer])
    return views[0]


async def _views(
    storage: IStorage, computers: Sequence[Computer]
) -> list[ComputerView]:
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


async def agent_view(
    storage: IStorage, computer_id: str, agent_id: str
) -> AgentView | None:
    computer = await computer_view(storage, computer_id)
    if computer is None:
        return None
    return next((agent for agent in computer.agents if agent.id == agent_id), None)


def _target_name(message: StoredEvent) -> str | None:
    """What a conversation is called, from a message seen in it."""

    metadata = message.payload["metadata"]
    return metadata.get("target_name") or metadata.get("target")


def _computer_view(
    item: ComputerHealth,
    boundaries: Mapping[tuple[str, str | None], StoredEvent],
    names: Mapping[tuple[str, str | None, str | None], str | None],
) -> ComputerView:
    health = {} if item.health is None else item.health.payload["metadata"]
    interval = health.get("interval_ms") or DEFAULT_INTERVAL_MS
    online = (
        item.last_event_at_ms is not None
        and now_ms() - item.last_event_at_ms <= 2 * interval
    )
    agents: list[AgentView] = []
    for record in health.get("agents", []):
        agent_id = record["agent_id"]
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
    return ComputerView(
        computer=item.computer,
        online=online,
        last_event_at_ms=item.last_event_at_ms,
        version=health.get("version"),
        system=health.get("system"),
        queued=health.get("audit", {}).get("queued"),
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
        id=record["agent_id"],
        name=record["name"],
        computer_id=computer.id,
        computer_name=computer.name,
        status=status,
        channels=tuple(record.get("channels", [])),
        runtimes=tuple(record.get("runtimes", [])),
        working_on=working_on,
        working_since_ms=working_since_ms,
    )


__all__ = [
    "PAGE_SIZE",
    "AgentView",
    "ComputerView",
    "Fleet",
    "agent_view",
    "agents_of",
    "computer_view",
    "fleet",
]
