"""The computers and agents as the server knows them right now, read from
what each computer last reported."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from .access import Access
from .clock import now_ms
from .storage import Computer, ComputerHealth, IStorage, StoredEvent, ThreadKey

# a page is what one scroll of the list asks for; the next page is asked for
# when the end of this one comes into view
PAGE_SIZE = 50
# the beat a node promises when it has not said otherwise
# how often a node beats; a computer silent for two beats is offline
HEALTH_INTERVAL_MS = 60_000

_TURN_START = "runtime.request.turn.started"
_TURN_BOUNDARIES = (
    _TURN_START,
    "runtime.turn.completed",
    "runtime.turn.failed",
    "runtime.turn.cancelled",
    "runtime.turn.unknown",
)


@dataclass(frozen=True, slots=True)
class AgentView:
    id: str
    name: str
    computer_id: str
    computer_name: str
    system: str | None
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
    storage: IStorage,
    access: Access,
    *,
    after: str | None = None,
    until: str | None = None,
) -> Fleet:
    """The page of the account's computers past `after`, or everything up to
    `until`, with their agents, in a fixed handful of reads however many
    there are."""

    subject = access.subject_id
    if until is None:
        # one past the page says whether there is a next one
        computers = await storage.list_computers(
            subject, after=after, limit=PAGE_SIZE + 1
        )
        more = len(computers) > PAGE_SIZE
        computers = computers[:PAGE_SIZE]
    else:
        computers, beyond = await asyncio.gather(
            storage.list_computers(subject, until=until),
            storage.list_computers(subject, after=until, limit=1),
        )
        more = bool(beyond)
    return Fleet(
        computers=await _views(storage, access, computers),
        edge=computers[-1].id if computers else None,
        more=more,
    )


@dataclass(frozen=True, slots=True)
class AgentPage:
    """A stretch of the agent list, across computers. `edge` is the key
    (`computer_id/agent_id`) it ends at, nothing when it is empty; `more`
    says whether the list goes on past it."""

    agents: list[AgentView]
    edge: str | None
    more: bool


async def agent_page(
    storage: IStorage,
    access: Access,
    *,
    after: str | None = None,
    until: str | None = None,
) -> AgentPage:
    """The page of the account's agents past `after`, or everything up to
    `until`. Agents come in computer order, then by id, so a key of the two
    is a cursor; computers are read a page at a time until the agents fill
    theirs."""

    subject = access.subject_id
    cursor = _agent_key(after) if after else None
    stop = _agent_key(until) if until else None
    agents: list[AgentView] = []
    more = False
    # the cursor's own computer may have agents past the cursor
    pending = []
    if cursor is not None and await storage.has_relation(
        subject, "computer_owner", cursor[0]
    ):
        first = await storage.find_computer(cursor[0])
        pending = [first] if first is not None else []
    last_id = None if cursor is None else cursor[0]
    while True:
        batch = await storage.list_computers(subject, after=last_id, limit=PAGE_SIZE)
        computers = [*pending, *batch]
        pending = []
        if not computers:
            break
        last_id = computers[-1].id
        for view in await _views(storage, access, computers):
            for agent in view.agents:
                key = (agent.computer_id, agent.id)
                if cursor is not None and key <= cursor:
                    continue
                if stop is not None and key > stop:
                    more = True
                    break
                if stop is None and len(agents) == PAGE_SIZE:
                    more = True
                    break
                agents.append(agent)
            if more:
                break
        if more or len(batch) < PAGE_SIZE:
            break
    return AgentPage(
        agents=agents,
        edge=f"{agents[-1].computer_id}/{agents[-1].id}" if agents else None,
        more=more,
    )


def _agent_key(key: str) -> tuple[str, str]:
    computer_id, _, agent_id = key.partition("/")
    return computer_id, agent_id


async def computer_view(
    storage: IStorage, access: Access, computer_id: str
) -> ComputerView | None:
    """One computer with the agents the account may see, whichever page it
    is on."""

    computer = await storage.find_computer(computer_id)
    if computer is None:
        return None
    views = await _views(storage, access, [computer])
    return views[0]


async def _views(
    storage: IStorage, access: Access, computers: Sequence[Computer]
) -> list[ComputerView]:
    health, boundary_rows = await asyncio.gather(
        storage.computer_health(computers),
        storage.latest_per_agent(
            [computer.id for computer in computers], _TURN_BOUNDARIES
        ),
    )
    # an agent works several conversations at once; the turn it is on is the
    # newest one still open in any of them
    open_turns: dict[tuple[str, str | None], StoredEvent] = {}
    for item in boundary_rows:
        if item.event_name != _TURN_START:
            continue
        key = (item.computer_id, item.agent_id)
        if key not in open_turns or open_turns[key].id < item.id:
            open_turns[key] = item
    threads = [
        (item.computer_id, agent_id, thread_id)
        for item in open_turns.values()
        if (agent_id := item.agent_id) is not None
        and (thread_id := item.thread_id) is not None
    ]
    names = await storage.thread_names(threads)
    views = [_computer_view(item, open_turns, names) for item in health]
    # the agents the account may look at, out of all these computers report
    visible = await access.visible("agent", [agent.id for agent in agents_of(views)])
    return [
        replace(view, agents=tuple(a for a in view.agents if a.id in visible))
        for view in views
    ]


def agents_of(computers: Sequence[ComputerView]) -> list[AgentView]:
    return [agent for computer in computers for agent in computer.agents]


async def agent_view(
    storage: IStorage, access: Access, computer_id: str, agent_id: str
) -> AgentView | None:
    computer = await computer_view(storage, access, computer_id)
    if computer is None:
        return None
    return next((agent for agent in computer.agents if agent.id == agent_id), None)


@dataclass(frozen=True, slots=True)
class ChannelHealth:
    """One channel of an agent as a person reads it: whether it is up, who
    it is there, since when, when it last heard anything, and what last went
    wrong. Each kind reports these under names of its own."""

    kind: str
    ok: bool
    state: str
    identity: str | None
    since_ms: int | None
    last_ms: int | None
    error: str | None


@dataclass(frozen=True, slots=True)
class AgentHealth:
    """How an agent is doing: running, and if not quite, why; its channels."""

    status: str
    # what keeps a started agent from being well: its background work that
    # failed, named with the error
    problems: tuple[str, ...]
    channels: tuple[ChannelHealth, ...]

    @property
    def running(self) -> bool:
        return self.status == "started" and not self.problems


async def agent_health(
    storage: IStorage, computer_id: str, agent_id: str
) -> AgentHealth | None:
    """How the agent's computer last said it was doing in its health beat;
    nothing when it has not."""

    record = await _agent_record(storage, computer_id, agent_id)
    if record is None:
        return None
    orchestrator = record.get("orchestrator_health") or {}
    return AgentHealth(
        record["status"],
        tuple(
            f"{name}: {error}"
            for name, error in (orchestrator.get("background_failures") or {}).items()
        ),
        tuple(
            _channel_health(item["kind"], item.get("identity"), item["health"])
            for item in (record.get("channel_health") or {}).get("channels", [])
        ),
    )


def _channel_health(
    kind: str, identity: str | None, health: Mapping[str, Any]
) -> ChannelHealth:
    state = str(health.get("state"))
    match kind:
        case "telegram":
            started = health.get("started_at_s")
            return ChannelHealth(
                kind,
                state == "ready",
                state,
                f"@{health['bot_username']}"
                if health.get("bot_username")
                else identity,
                started * 1000 if started else None,
                health.get("last_update_at_ms"),
                health.get("last_poll_error_kind"),
            )
        case "lark":
            return ChannelHealth(
                kind,
                state == "connected",
                state,
                health.get("bot_name") or identity,
                health.get("connected_at_ms"),
                health.get("last_event_at_ms"),
                health.get("last_disconnect_kind"),
            )
        case "wecom":
            return ChannelHealth(
                kind,
                state == "connected",
                state,
                identity,
                health.get("connected_at_ms"),
                health.get("last_frame_at_ms"),
                health.get("last_disconnect_kind"),
            )
        case _:
            return ChannelHealth(
                kind, state in {"ready", "connected"}, state, identity, None, None, None
            )


async def _agent_record(
    storage: IStorage, computer_id: str, agent_id: str
) -> Mapping[str, Any] | None:
    """What the agent's computer last said of it in its health beat, as it
    said it; nothing when it has not."""

    computer = await storage.find_computer(computer_id)
    if computer is None:
        return None
    (health,) = await storage.computer_health([computer])
    if health.health is None:
        return None
    return next(
        (
            record
            for record in health.health.payload["metadata"].get("agents", [])
            if record["agent_id"] == agent_id
        ),
        None,
    )


def _computer_view(
    item: ComputerHealth,
    open_turns: Mapping[tuple[str, str | None], StoredEvent],
    names: Mapping[ThreadKey, str],
) -> ComputerView:
    health = {} if item.health is None else item.health.payload["metadata"]
    online = (
        item.last_event_at_ms is not None
        and now_ms() - item.last_event_at_ms <= 2 * HEALTH_INTERVAL_MS
    )
    agents: list[AgentView] = []
    for record in health.get("agents", []):
        agent_id = record["agent_id"]
        turn = open_turns.get((item.computer.id, agent_id))
        agents.append(
            _agent_view(
                item.computer,
                record,
                system=health.get("system"),
                online=online,
                turn=turn,
                working_on=(
                    None
                    if turn is None or turn.thread_id is None
                    else names.get((item.computer.id, agent_id, turn.thread_id))
                ),
            )
        )
    # in id order, so a page of agents across computers has a cursor
    agents.sort(key=lambda agent: agent.id)
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
    system: str | None,
    online: bool,
    turn: StoredEvent | None,
    working_on: str | None,
) -> AgentView:
    working_since_ms: int | None = None
    status = "running"
    if not online:
        status = "offline"
    elif record.get("status") != "started":
        status = "failed"
    elif turn is not None:
        # a turn still open in some conversation is what busy means
        status = "busy"
        working_since_ms = turn.created_at_ms
    else:
        working_on = None
    return AgentView(
        id=record["agent_id"],
        name=record["name"],
        computer_id=computer.id,
        computer_name=computer.name,
        system=system,
        status=status,
        channels=tuple(record.get("channels", [])),
        runtimes=tuple(record.get("runtimes", [])),
        working_on=working_on,
        working_since_ms=working_since_ms,
    )


__all__ = [
    "PAGE_SIZE",
    "AgentHealth",
    "AgentPage",
    "AgentView",
    "ChannelHealth",
    "ComputerView",
    "Fleet",
    "agent_health",
    "agent_view",
    "agents_of",
    "computer_view",
    "fleet",
]
