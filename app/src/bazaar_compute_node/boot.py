"""Node bootstrap inputs, packaged combinations, and concrete input graphs."""

from __future__ import annotations

from contextlib import asynccontextmanager
lazy import asyncio
lazy import tomllib
lazy from collections.abc import AsyncIterator, Callable, Sequence
lazy from importlib.resources import files

lazy from bazaar_contrib_config import (
    CONFIG_PREPARE,
    CONFIG_STORE,
    CONFIGURATION,
    ConfigDocument,
    ConfigFormatError,
    Configuration,
    ConfigurationEnvironment,
    EnabledContrib,
    configuration,
)
lazy from bazaar_contrib_config_versions import VERSIONS
lazy from bazaar_core import (
    Composition,
    Context,
    Contrib,
    ContribEntry,
    ContribHub,
    ContribSpec,
    load,
)

lazy from . import run


def build(document: ConfigDocument) -> Composition:
    """Load enabled contrib declarations without interpreting their business input."""
    return load(
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
