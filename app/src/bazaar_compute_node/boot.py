"""Node bootstrap inputs, packaged combinations, and concrete input graphs."""

from __future__ import annotations

from contextlib import asynccontextmanager
lazy import asyncio
lazy import json
lazy import os
lazy import tomllib
lazy from collections.abc import AsyncIterator, Sequence
lazy from importlib.resources import files
lazy from pathlib import Path
lazy from string import Template
lazy from typing import Any
lazy from uuid import NAMESPACE_URL, uuid5, uuid7

lazy from bazaar_contrib_audit import AUDIT_EVENT
lazy from bazaar_contrib_config import (
    CONFIG_STORE,
    AgentConfig,
    ConfigDocument,
    ConfigFormatError,
    ProviderConfig,
    configuration,
)
lazy from bazaar_contrib_config_versions import VERSIONS
lazy from bazaar_core import (
    CONTRIB_CHANGED,
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
lazy from .audit import audit


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


def build(document: ConfigDocument) -> Composition:
    """Expand known business instances and bindings before resource acquisition."""
    composition = load(
        [
            ContribSpec(
                contrib=entry.contrib,
                config=entry.config,
                location=f"contribs[{index}]",
            )
            for index, entry in enumerate(document.contribs)
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
        identity = uuid5(NAMESPACE_URL, agent.id)
        keys: set[ServiceKey[Any] | EventKey[Any]] = {AGENT}
        for entry in templates[AGENT]:
            keys.update(entry.definition.offers)
            keys.update(entry.definition.listen)
        keys.difference_update((AUDIT_EVENT, CONTRIB_CHANGED))
        bindings = {key: identity for key in keys}
        entries.append(
            ContribEntry(
                name=agent.name,
                definition=AGENT_INPUT,
                config=agent.model_dump(mode="json"),
                location=f"agent[{agent.name}]",
                bindings=bindings,
            )
        )
        entries.extend(
            entry.model_copy(
                update={
                    "bindings": bindings | entry.bindings,
                    "location": f"{entry.location}/agent[{agent.name}]",
                }
            )
            for entry in templates[AGENT]
        )
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
                identity = uuid5(bindings[AGENT], f"{key.name}/{index}")
                keys = {key}
                for entry in selected:
                    keys.update(entry.definition.offers)
                    keys.update(entry.definition.listen)
                keys.difference_update((AUDIT_EVENT, CONTRIB_CHANGED))
                local = bindings | {key: identity for key in keys}
                entries.append(
                    ContribEntry(
                        name=f"{agent.name}/{key.name}/{index}",
                        definition=definition,
                        config=provider.model_dump(mode="json"),
                        location=f"agent[{agent.name}]/{key.name}[{index}]",
                        bindings=local,
                    )
                )
                entries.extend(
                    entry.model_copy(
                        update={
                            "bindings": local | entry.bindings,
                            "location": f"{entry.location}/agent[{agent.name}]/{key.name}[{index}]",
                        }
                    )
                    for entry in selected
                )
    return composition.model_copy(
        update={"entries": tuple(entries), "diagnostics": tuple(diagnostics)}
    )


def bootstrap(
    *,
    configer: str = "config-filesystem",
    configer_options: Sequence[str] = (),
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
    catalog = (
        Path(os.environ.get("BCN_HOME") or Path.home())
        / ".bcn"
        / "contrib"
        / "catalog.json"
    ).resolve()
    catalog.parent.mkdir(parents=True, exist_ok=True)
    catalog.write_bytes(resource.joinpath("catalog.json").read_bytes())
    defaults = Template(resource.joinpath("defaults.toml").read_text()).substitute(
        catalog=json.dumps(str(catalog), ensure_ascii=False)
    )
    defaults = tomllib.loads(defaults)
    return (
        Composition(
            entries=(
                *definitions.entries,
                ContribEntry(name="node-audit", definition=audit),
            ),
        ),
        ContribEntry(
            name="node-configuration",
            definition=configuration(defaults, build),
            location="defaults.toml",
        ),
    )


@asynccontextmanager
async def node(
    *,
    configer: str = "config-filesystem",
    configer_options: Sequence[str] = (),
) -> AsyncIterator[ContribHub]:
    """Run the installed configuration source and node composition."""
    entries, loader = await asyncio.to_thread(
        bootstrap,
        configer=configer,
        configer_options=configer_options,
    )
    async with run(entries, loader) as hub:
        yield hub
