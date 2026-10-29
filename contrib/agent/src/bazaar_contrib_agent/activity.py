"""Each agent's state, today's usage and latest event, kept in memory as bcs reads them."""

from __future__ import annotations

import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime

from bazaar_contrib_audit import AUDIT_EVENT, AuditEvent
from bazaar_contrib_harness import HarnessOutput, UsageUpdated
from bazaar_contrib_sidecar import RemoteEntry
from bazaar_core import Context, Contrib, ServiceKey, Stopping


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
class Activity:
    state: str
    reason: str | None
    usage: Usage
    event: str | None
    event_at_ms: int | None


class AgentActivity:
    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.day = date.today()
        # a session that ran across midnight already counted part of its total
        # yesterday; today's share is what it has grown since
        self.before: dict[str, Usage] = {}
        self.totals: dict[str, tuple[str, Usage]] = {}
        self.last: dict[str, tuple[str, int]] = {}

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
        self.last[agent] = (event.event_name, event.occurred_at_ms)

    def agents(self) -> dict[str, Activity]:
        self._roll(date.today())
        usage: dict[str, Usage] = {}
        for session, (agent, total) in self.totals.items():
            usage[agent] = usage.get(agent, Usage()).plus(
                total.since(self.before.get(session, Usage()))
            )
        records = {record.location: record for record in self.ctx.snapshots}
        result: dict[str, Activity] = {}
        for location, record in records.items():
            match = re.fullmatch(r"agents\[([^\]]+)\]", location)
            if match is None:
                continue
            agent = match[1]
            state = records.get(f"{location}.loop", record).state
            error = state.error if isinstance(state, Stopping) else None
            event = self.last.get(agent)
            result[agent] = Activity(
                state=type(state).__name__.lower(),
                reason=None if error is None else str(error).splitlines()[0],
                usage=usage.get(agent, Usage()),
                event=None if event is None else event[0],
                event_at_ms=None if event is None else event[1],
            )
        return result


AGENT_ACTIVITY = ServiceKey[AgentActivity]("agent.activity")


@asynccontextmanager
async def _lifespan(ctx: Context):
    ctx.offer(AGENT_ACTIVITY, AgentActivity(ctx))
    yield


async def _observe(ctx: Context, event: AuditEvent) -> None:
    ctx.accept(AGENT_ACTIVITY).observe(event)


activity = Contrib(
    offers=[AGENT_ACTIVITY], lifespan=_lifespan, listen={AUDIT_EVENT: _observe}
)
remote = RemoteEntry(AGENT_ACTIVITY, ("agents",))
