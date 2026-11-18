"""Each agent's state, today's usage, latest event, and how its channels and
runtimes are, kept in memory as Bazaar Server reads them."""

from __future__ import annotations

import asyncio
import logging
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import date, datetime
from time import time_ns

from bazaar_contrib_audit import AUDIT_EVENT, AuditEvent, AuditFailure
from bazaar_contrib_channel import (
    ChannelConnected,
    ChannelConnectionFailed,
    ChannelIdentity,
)
from bazaar_contrib_harness import (
    HarnessOutput,
    QuotaReader,
    QuotaSnapshot,
    UsageUpdated,
)
from bazaar_contrib_sidecar import RemoteEntry
from bazaar_core import Context, Contrib, ContribStatus, ServiceKey, Stopping

from .registry import AGENTS, AgentInstances
from .states import Lifecycle

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Usage:
    input: int = 0
    output: int = 0
    cached: int = 0
    cost: float | None = None

    def since(self, earlier: Usage) -> Usage:
        return Usage(
            self.input - earlier.input,
            self.output - earlier.output,
            self.cached - earlier.cached,
            None if self.cost is None else self.cost - (earlier.cost or 0.0),
        )

    def plus(self, other: Usage) -> Usage:
        return Usage(
            self.input + other.input,
            self.output + other.output,
            self.cached + other.cached,
            (
                None
                if self.cost is None and other.cost is None
                else (self.cost or 0.0) + (other.cost or 0.0)
            ),
        )


@dataclass(frozen=True, slots=True)
class Working:
    since_ms: int
    conversation: str | None


@dataclass(frozen=True, slots=True)
class ChannelStatus:
    kind: str
    connection: str
    state: str
    identity: ChannelIdentity | None
    connected_at_ms: int | None
    received_at_ms: int | None
    failures: int
    error: str | None


@dataclass(frozen=True, slots=True)
class RuntimeStatus:
    kind: str
    connection: str
    state: str
    in_use: bool
    banned_until_ms: int | None
    quota: QuotaSnapshot | None = None


@dataclass(frozen=True, slots=True)
class Activity:
    state: str
    reason: str | None
    usage: Usage
    event: str | None
    event_at_ms: int | None
    started_at_ms: int | None
    working: Working | None
    # the turns running, as of when this was said
    turns: tuple[str, ...]
    said_at_ms: int
    channels: tuple[ChannelStatus, ...]
    runtimes: tuple[RuntimeStatus, ...]


@dataclass(frozen=True, slots=True)
class Link:
    """A channel connection as its events tell: when it last connected, how
    often it failed since, with what, and whether it is still trying."""

    connected_at_ms: int | None = None
    failures: int = 0
    error: str | None = None
    retrying: bool = False


def _state(record: ContribStatus | None) -> tuple[str, str | None]:
    if record is None:
        return "stopped", None
    error = record.state.error if isinstance(record.state, Stopping) else None
    return (
        type(record.state).__name__.lower(),
        None if error is None else str(error).splitlines()[0],
    )


class AgentActivity:
    def __init__(self, ctx: Context, instances: AgentInstances):
        self.ctx = ctx
        self.instances = instances
        self.day = date.today()
        # a session that ran across midnight already counted part of its total
        # yesterday; today's share is what it has grown since
        self.before: dict[str, Usage] = {}
        self.totals: dict[str, tuple[str, Usage]] = {}
        self.last: dict[str, tuple[str, int]] = {}
        self.links: dict[tuple[str, str], Link] = {}

    def _roll(self, day: date) -> None:
        if day > self.day:
            self.before |= {
                session: total for session, (_, total) in self.totals.items()
            }
            self.totals = {}
            self.day = day

    def observe(self, event: AuditEvent) -> None:
        agent = event.context.agent_id
        if not agent:
            return
        self._roll(datetime.fromtimestamp(event.occurred_at_ms / 1000).date())
        if event.event_name == "harness.usage.updated":
            output = HarnessOutput.model_validate(event.payload)
            assert isinstance(output.payload, UsageUpdated)
            total = output.payload.total
            # what was written into the cache was read fresh first, so it
            # counts as input; reasoning is part of the output already
            self.totals[output.session_id] = (
                agent,
                Usage(
                    (total.input_tokens or 0) + (total.cache_write_input_tokens or 0),
                    total.output_tokens or 0,
                    total.cached_input_tokens or 0,
                    output.payload.cost_usd,
                ),
            )
        elif (
            event.event_name == ChannelConnected.event_name and event.context.connection
        ):
            self.links[agent, event.context.connection] = Link(event.occurred_at_ms)
        elif (
            event.event_name == ChannelConnectionFailed.event_name
            and event.context.connection
        ):
            link = self.links.get((agent, event.context.connection), Link())
            self.links[agent, event.context.connection] = Link(
                link.connected_at_ms,
                link.failures + 1,
                event.error.message if isinstance(event, AuditFailure) else None,
                ChannelConnectionFailed.model_validate(event.payload).retry_in_ms
                is not None,
            )
        self.last[agent] = (event.event_name, event.occurred_at_ms)

    async def agents(self) -> dict[str, Activity]:
        async def read_quota(
            agent: str, connection: str, reader: QuotaReader
        ) -> QuotaSnapshot | None:
            try:
                return await reader.read_quota()
            except Exception:
                logger.exception(
                    "runtime quota read failed for agent %s, connection %s",
                    agent,
                    connection,
                )
                return None

        snapshot = self.snapshot()
        readers: dict[tuple[str, str], asyncio.Task[QuotaSnapshot | None]] = {}
        async with asyncio.TaskGroup() as group:
            for agent, identity in self.instances.running.items():
                loop = identity._loop
                if agent not in snapshot or loop is None:
                    continue
                for runtime in loop.runtimes.values():
                    if runtime.state is Lifecycle.RUNNING and isinstance(
                        runtime.harness, QuotaReader
                    ):
                        readers[agent, runtime.key] = group.create_task(
                            read_quota(agent, runtime.key, runtime.harness)
                        )
        for agent, activity in snapshot.items():
            snapshot[agent] = replace(
                activity,
                runtimes=tuple(
                    replace(runtime, quota=reader.result())
                    if (reader := readers.get((agent, runtime.connection))) is not None
                    else runtime
                    for runtime in activity.runtimes
                ),
            )
        return snapshot

    def snapshot(self) -> dict[str, Activity]:
        self._roll(date.today())
        said_at_ms = time_ns() // 1_000_000
        usage: dict[str, Usage] = {}
        for session, (agent, total) in self.totals.items():
            usage[agent] = usage.get(agent, Usage()).plus(
                total.since(self.before.get(session, Usage()))
            )
        records = {record.location: record for record in self.ctx.snapshots}
        configs = {item.id: item for item in self.instances.config.agents}
        result: dict[str, Activity] = {}
        for location, record in records.items():
            match = re.fullmatch(r"agents\[([^\]]+)\]", location)
            if match is None:
                continue
            agent = match[1]
            state, reason = _state(records.get(f"{location}.loop", record))
            event = self.last.get(agent)
            identity = self.instances.running.get(agent)
            loop = None if identity is None else identity._loop
            channels: list[ChannelStatus] = []
            runtimes: list[RuntimeStatus] = []
            config = configs.get(agent)
            if config is not None:
                selected = None if loop is None else loop.select()
                for label, providers in (
                    ("channel", config.channel),
                    ("harness", config.harness),
                ):
                    positions: dict[str, int] = {}
                    for provider in providers:
                        position = positions.get(provider.kind, 0)
                        positions[provider.kind] = position + 1
                        key = f"{provider.kind}:{position}"
                        name = f"{config.name}/{label}:{key}"
                        entry, error = _state(
                            records.get(f"agents[{agent}].{label}[{key}]")
                        )
                        if label == "channel":
                            connection = (
                                None
                                if loop is None
                                else loop.channels.connections.get(name)
                            )
                            link = self.links.get((agent, key), Link())
                            channels.append(
                                ChannelStatus(
                                    kind=provider.kind,
                                    connection=key,
                                    state=entry
                                    if entry != "running" or not link.failures
                                    else "reconnecting"
                                    if link.retrying
                                    else "disconnected",
                                    identity=None
                                    if connection is None
                                    else connection.channel.get_identity(),
                                    connected_at_ms=link.connected_at_ms,
                                    received_at_ms=None
                                    if connection is None
                                    else connection.received_at_ms,
                                    failures=link.failures,
                                    error=link.error or error,
                                )
                            )
                            continue
                        runtime = None if loop is None else loop.runtimes.get(name)
                        # a ban is kept on the timer's monotonic clock
                        banned = (
                            0
                            if loop is None or runtime is None
                            else runtime.banned_until_ms - loop.timer.now_ms
                        )
                        runtimes.append(
                            RuntimeStatus(
                                kind=provider.kind,
                                connection=key,
                                state=entry,
                                in_use=runtime is not None and runtime is selected,
                                banned_until_ms=time_ns() // 1_000_000 + banned
                                if banned > 0
                                else None,
                            )
                        )
            result[agent] = Activity(
                state=state,
                reason=reason,
                usage=usage.get(agent, Usage()),
                event=None if event is None else event[0],
                event_at_ms=None if event is None else event[1],
                started_at_ms=None if loop is None else loop.started_at_ms,
                working=None
                if loop is None
                else min(
                    (
                        Working(item.working_since_ms, item.working_on)
                        for item in loop.actor_loops.values()
                        if item.working_since_ms is not None
                    ),
                    key=lambda item: item.since_ms,
                    default=None,
                ),
                turns=()
                if loop is None
                else tuple(
                    item.active.turn.id
                    for item in loop.actor_loops.values()
                    if item.active is not None
                ),
                said_at_ms=said_at_ms,
                channels=tuple(channels),
                runtimes=tuple(runtimes),
            )
        return result


AGENT_ACTIVITY = ServiceKey[AgentActivity]("agent.activity")


@asynccontextmanager
async def _lifespan(ctx: Context):
    ctx.offer(AGENT_ACTIVITY, AgentActivity(ctx, ctx.accept(AGENTS)))
    yield


async def _observe(ctx: Context, event: AuditEvent) -> None:
    ctx.accept(AGENT_ACTIVITY).observe(event)


activity = Contrib(
    accepts=[AGENTS],
    offers=[AGENT_ACTIVITY],
    lifespan=_lifespan,
    listen={AUDIT_EVENT: _observe},
)
remote = RemoteEntry(AGENT_ACTIVITY, ("agents",))
