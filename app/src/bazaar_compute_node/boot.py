"""Node bootstrap inputs, packaged combinations, and concrete input graphs."""

from __future__ import annotations

from contextlib import asynccontextmanager
lazy import asyncio
lazy import tomllib
lazy from collections.abc import AsyncIterator, Callable, Sequence
lazy from importlib.resources import files
lazy from typing import Any
lazy from uuid import uuid7

lazy from bazaar_contrib_config import (
    CONFIG_PREPARE,
    CONFIG_STORE,
    CONFIGURATION,
    AgentConfig,
    ConfigDocument,
    ConfigFormatError,
    Configuration,
    ConfigurationEnvironment,
    EnabledContrib,
    ProviderConfig,
    configuration,
)
lazy from bazaar_contrib_config_versions import VERSIONS
lazy from bazaar_core import (
    CONTRIB_CHANGED,
    BindingRef,
    Composition,
    Context,
    Contrib,
    ContribEntry,
    ContribHub,
    ContribSpec,
    ContribStatus,
    EventKey,
    Failed,
    ServiceKey,
    load,
)

lazy from . import AGENT, CHANNEL, RUNTIME, run


@asynccontextmanager
async def agent_input(ctx: Context, config: AgentConfig):
    ctx.offer(AGENT, config)
    yield


@asynccontextmanager
async def channel_input(ctx: Context, config: ProviderConfig):
    ctx.accept(AGENT)
    ctx.offer(CHANNEL, config)
    yield


@asynccontextmanager
async def runtime_input(ctx: Context, config: ProviderConfig):
    ctx.accept(AGENT)
    ctx.offer(RUNTIME, config)
    yield


AGENT_INPUT = Contrib(config=AgentConfig, offers=[AGENT], lifespan=agent_input)
CHANNEL_INPUT = Contrib(
    config=ProviderConfig, offers=[CHANNEL], accepts=[AGENT], lifespan=channel_input
)
RUNTIME_INPUT = Contrib(
    config=ProviderConfig, offers=[RUNTIME], accepts=[AGENT], lifespan=runtime_input
)


def bind(
    entries: list[ContribEntry],
    keys: set[ServiceKey[Any] | EventKey[Any]],
    inherited: dict[ServiceKey[Any] | EventKey[Any], BindingRef[Any]],
) -> tuple[
    tuple[ContribEntry, ...], dict[ServiceKey[Any] | EventKey[Any], BindingRef[Any]]
]:
    """Connect a static instance group through its declared private providers."""
    providers: dict[ServiceKey[Any], ContribEntry] = {}
    for entry in entries:
        for key in entry.definition.offers:
            providers.setdefault(key, entry)
    bindings = inherited | {
        key: providers[key].ref(key)
        if isinstance(key, ServiceKey)
        else entries[0].ref(key)
        for key in keys
    }
    instances = []
    for entry in entries:
        local = tuple(
            key
            for key in keys
            if (
                providers[key] is entry
                if isinstance(key, ServiceKey)
                else entry is entries[0]
            )
        )
        instances.append(
            entry.model_copy(
                update={
                    "local": local,
                    "bindings": {
                        key: reference
                        for key, reference in bindings.items()
                        if key not in local
                    }
                    | entry.bindings,
                }
            )
        )
    return tuple(instances), bindings


def build(document: ConfigDocument) -> Composition:
    """Expand known business instances and bindings before resource acquisition."""
    composition = load(
        [
            ContribSpec(
                contrib=entry.entry,
                config=entry.config,
                location=f"contribs[{index}].enabled[{position}]",
            )
            for index, package in enumerate(document.contribs)
            if package.applies
            for position, entry in enumerate(package.enabled)
        ]
    )
    entries: list[ContribEntry] = []
    diagnostics = list(composition.diagnostics)
    templates: dict[ServiceKey[Any], list[ContribEntry]] = {
        AGENT: [],
        CHANNEL: [],
        RUNTIME: [],
    }
    for entry in composition.entries:
        try:
            config = entry.definition.validate_config(entry.config)
            dependencies = entry.definition.dependencies(config)
        except Exception as error:  # noqa: BLE001 - Isolate invalid entry metadata before graph expansion.
            diagnostics.append(
                ContribStatus(
                    uuid7(),
                    entry.name,
                    entry.location,
                    Failed(error=str(error), phase="stopped"),
                )
            )
            continue
        key = next(
            (key for key in (CHANNEL, RUNTIME, AGENT) if key in dependencies), None
        )
        if key is None:
            entries.append(entry)
        else:
            templates[key].append(entry)
    for agent in document.agent:
        keys: set[ServiceKey[Any] | EventKey[Any]] = {AGENT}
        for entry in templates[AGENT]:
            keys.update(entry.definition.offers)
            keys.update(entry.definition.listen)
        keys.discard(CONTRIB_CHANGED)
        instances, bindings = bind(
            [
                ContribEntry(
                    name=agent.name,
                    definition=AGENT_INPUT,
                    config=agent.model_dump(mode="json"),
                    location=f"agent[{agent.name}]",
                ),
                *(
                    entry.model_copy(
                        update={"location": f"{entry.location}/agent[{agent.name}]"}
                    )
                    for entry in templates[AGENT]
                ),
            ],
            keys,
            {},
        )
        entries.extend(instances)
        for key, providers, definition in (
            (CHANNEL, agent.channel, CHANNEL_INPUT),
            (RUNTIME, agent.runtime, RUNTIME_INPUT),
        ):
            for index, provider in enumerate(providers):
                selected = [
                    entry for entry in templates[key] if entry.name == provider.kind
                ]
                if not selected:
                    diagnostics.append(
                        ContribStatus(
                            uuid7(),
                            provider.kind,
                            f"agent[{agent.name}]/{key.name}[{index}]",
                            Failed(
                                error=f"no enabled provider {provider.kind!r} declares the required {key.name!r} input",
                                phase="stopped",
                            ),
                        )
                    )
                    continue
                keys = {key}
                for entry in selected:
                    keys.update(entry.definition.offers)
                    keys.update(entry.definition.listen)
                keys.discard(CONTRIB_CHANGED)
                instances, _ = bind(
                    [
                        ContribEntry(
                            name=f"{agent.name}/{key.name}/{index}",
                            definition=definition,
                            config=provider.model_dump(mode="json"),
                            location=f"agent[{agent.name}]/{key.name}[{index}]",
                        ),
                        *(
                            entry.model_copy(
                                update={
                                    "location": f"{entry.location}/agent[{agent.name}]/{key.name}[{index}]"
                                }
                            )
                            for entry in selected
                        ),
                    ],
                    keys,
                    bindings,
                )
                entries.extend(instances)
    return composition.model_copy(
        update={"entries": tuple(entries), "diagnostics": tuple(diagnostics)}
    )


def bootstrap(
    *,
    configer: str = "config-filesystem",
    configer_options: Sequence[str] = (),
    start: Callable[[ConfigDocument], Composition] | None = None,
) -> tuple[Composition, ContribEntry]:
    resource = files(__package__)
    definitions = load(
        [
            ContribSpec(
                contrib=configer,
                config={"options": configer_options},
                location="--configer/--configer-options/--config",
                required_offers=[CONFIG_STORE],
            ),
            *VERSIONS,
        ]
    )
    if definitions.diagnostics:
        raise ConfigFormatError(
            "; ".join(
                f"{record.name}: {record.state}" for record in definitions.diagnostics
            )
        )
    defaults = tomllib.loads(resource.joinpath("defaults.toml").read_text())
    return (
        definitions,
        ContribEntry(
            name="node-configuration",
            definition=configuration(
                defaults,
                build,
                environment=ConfigurationEnvironment(
                    hub="bazaar_core:ContribHub",
                    entries=tuple(
                        EnabledContrib(entry=entry.name, config=entry.config)
                        for entry in definitions.entries
                    ),
                ),
                start=start,
            ),
            location="defaults.toml",
        ),
    )


@asynccontextmanager
async def node(
    *,
    configer: str = "config-filesystem",
    configer_options: Sequence[str] = (),
    start: Callable[[ConfigDocument], Composition] | None = None,
) -> AsyncIterator[ContribHub]:
    """Prepare configured dependencies before selecting business definitions."""

    configurations: list[Configuration] = []

    @asynccontextmanager
    async def dependencies(ctx: Context):
        configurations.append(ctx.accept(CONFIGURATION))
        yield

    def initial(document: ConfigDocument) -> Composition:
        composition = build(document)
        entry = ContribEntry(
            name="node-dependencies",
            definition=Contrib(
                accepts=[CONFIGURATION],
                lifespan=dependencies,
            ),
        )
        return composition.model_copy(
            update={
                "entries": (*composition.entries, entry),
                "roots": (
                    entry.name,
                    *(
                        entry.name
                        for entry in composition.entries
                        if CONFIG_PREPARE in entry.definition.listen
                    ),
                ),
            }
        )

    entries, loader = await asyncio.to_thread(
        bootstrap,
        configer=configer,
        configer_options=configer_options,
        start=initial,
    )

    async def prepare(hub: ContribHub) -> None:
        Configuration.check(hub)
        await hub.announce(CONFIG_PREPARE, None)
        await configurations[0].select(start)

    async with run(entries, loader, prepare=prepare) as hub:
        yield hub
