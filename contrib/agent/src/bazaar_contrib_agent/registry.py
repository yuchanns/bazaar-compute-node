"""Keep configured agent lifetimes independent from configuration consumers."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from contextlib import asynccontextmanager
from uuid import UUID

from bazaar_contrib_audit import AUDIT
from bazaar_contrib_channel import ChannelBuilder
from bazaar_contrib_collaboration import COLLABORATION
from bazaar_contrib_harness import HarnessBuilder
from bazaar_contrib_legacy_node_migrations import LEGACY_NODE_MIGRATIONS
from bazaar_contrib_messages import MESSAGES
from bazaar_contrib_reminder import REMINDER
from bazaar_contrib_schema import SCHEMA
from bazaar_contrib_storage import DB
from bazaar_contrib_timer import TIMER
from bazaar_core import (
    CONTRIB_CHANGED,
    Composition,
    Context,
    Contrib,
    ContribChange,
    Enablement,
    Running,
    ServiceKey,
    Starting,
)

from . import states
from .models import Agent, Config
from .schema import MODULE


class AgentInstances:
    def __init__(self, enabled: Enablement):
        self.enabled = enabled
        self.name: str | None = None
        self.running: dict[str, Agent] = {}
        self.channel_order: dict[str, int] = {}
        self.config = Config()
        self.requests: dict[
            UUID,
            tuple[Config, Mapping[str, ChannelBuilder], Mapping[str, HarnessBuilder]],
        ] = {}
        self.applied = asyncio.Event()
        self.error: Exception | None = None

    async def wait_applied(self) -> None:
        await self.applied.wait()
        if self.error is not None:
            raise self.error

    async def configure(
        self,
        ctx: Context,
        config: Config,
        channels: Mapping[str, ChannelBuilder],
        harnesses: Mapping[str, HarnessBuilder],
    ) -> None:
        from . import AGENT, _channel, compose

        entries = []
        for item in config.agents:
            composition = compose(
                item, channel_builders=channels, harness_builders=harnesses
            )
            entries.extend(
                entry.model_copy(update={"local": (AGENT,)})
                if AGENT in entry.definition.offers
                else entry
                for entry in composition.entries
            )
        desired = {entry.name: entry for entry in entries}
        self.channel_order.clear()
        self.channel_order.update(
            (entry.name, index)
            for index, entry in enumerate(entries)
            if entry.definition == _channel
        )
        for item in self.config.agents:
            identity = self.running.get(item.id)
            if identity is None or identity._loop is None:
                continue
            loop = identity._loop
            for connection in loop.channels.connections.values():
                if connection.name in self.channel_order:
                    connection.priority = self.channel_order[connection.name]
            if not any(candidate.id == item.id for candidate in config.agents):
                await loop.stop()
                continue
            retiring = [
                connection
                for connection in loop.channels.connections.values()
                if connection.name not in desired
                or desired[connection.name].config["provider"] != connection.provider
            ]
            for connection in retiring:
                connection.state = states.ConnectionState.STOPPING
            # Business work drains before the framework replaces resource declarations.
            while retiring and (loop.channels.pending or await loop.busy()):
                await loop.timer.create(max(100, loop.timer.tick_ms)).wait()
            for connection in retiring:
                connection.state = states.ConnectionState.DRAINED
            for name, runtime in loop.runtimes.items():
                if (
                    name in desired
                    and desired[name].config["provider"] == runtime.provider
                    and desired[name].config["priority"] == runtime.priority
                ):
                    continue
                runtime.state = states.Lifecycle.STOPPING
                while runtime.pending:
                    await loop.timer.create(max(100, loop.timer.tick_ms)).wait()
                for actor_loop in loop.actor_loops.values():
                    state = actor_loop.state
                    binding = (
                        None if isinstance(state, states.Dormant) else state.binding
                    )
                    if binding is not None and binding.runtime is runtime:
                        while await loop.background(actor_loop):
                            await loop.timer.create(max(100, loop.timer.tick_ms)).wait()
                        await loop.close_session(actor_loop)
                while runtime.pending:
                    await loop.timer.create(max(100, loop.timer.tick_ms)).wait()
        await self.enabled.update(Composition(entries=entries))
        for item in config.agents:
            identity = self.running.get(item.id)
            if identity is None or identity._loop is None:
                continue
            loop = identity._loop
            for connection in sorted(
                loop.channels.connections.values(), key=lambda item: item.priority
            ):
                channel_identity = connection.channel.get_identity().id
                for (
                    session
                ) in await loop.service.storage.list_channel_sessions_without_identity(
                    connection.kind
                ):
                    await loop.service.storage.backfill_channel_identity(
                        session.id,
                        channel_identity=channel_identity,
                        provider_thread_id=loop.channels.backfill_provider_thread_id(
                            session.provider_thread_id,
                            channel=connection.kind,
                            channel_identity=channel_identity,
                        ),
                    )
            for connection in loop.channels.connections.values():
                connection.ready.set()
            await loop.recover()
        self.config = config
        self.name = ctx.name


AGENTS = ServiceKey[AgentInstances]("agent.instances")
SERVICES = [
    AUDIT,
    MESSAGES,
    COLLABORATION,
    REMINDER,
    TIMER,
    DB,
]


@asynccontextmanager
async def lifespan(ctx: Context):
    async with (
        ctx.accept(SCHEMA).register(MODULE),
        ctx.enable(Composition()) as enabled,
    ):
        ctx.offer(AGENTS, AgentInstances(enabled))
        await ctx.accept(REMINDER).start(timeout=60, shutdown_timeout=5)
        yield


async def changed(ctx: Context, change: ContribChange) -> None:
    # Retained instances are notified after a composition replacement is activated.
    if not any(
        record.id == ctx.id and isinstance(record.state, Running)
        for record in change.snapshots
    ):
        return
    service = ctx.accept(AGENTS)
    if (
        service.name is not None
        and not any(record.name == service.name for record in change.snapshots)
        and not any(
            record.name == service.name
            and isinstance(record.state, (Starting, Running))
            for record in ctx.snapshots
        )
    ):
        await service.configure(ctx, Config(), {}, {})
        service.name = None


execution = Contrib(
    accepts=[*SERVICES, SCHEMA, LEGACY_NODE_MIGRATIONS],
    offers=[AGENTS],
    lifespan=lifespan,
    listen={CONTRIB_CHANGED: changed},
)


async def configured(ctx: Context, change: ContribChange) -> None:
    if not any(
        record.id == ctx.id and isinstance(record.state, Running)
        for record in change.snapshots
    ):
        return
    service = ctx.accept(AGENTS)
    request = service.requests.pop(ctx.id, None)
    if request is None:
        return
    try:
        await service.configure(ctx, *request)
    except Exception as error:
        service.error = error
        raise
    finally:
        service.applied.set()
