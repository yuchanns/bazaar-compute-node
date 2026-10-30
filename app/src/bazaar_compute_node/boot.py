"""Node bootstrap inputs, packaged combinations, and concrete input graphs."""

from __future__ import annotations

from contextlib import asynccontextmanager
lazy import asyncio
lazy import importlib
lazy import tomllib
lazy from collections.abc import AsyncIterator, Callable, Mapping, Sequence
lazy from importlib.resources import files

lazy from bazaar_contrib_config import (
    CONFIG_PREPARE,
    CONFIG_STORE,
    CONFIGURATION,
    ConfigDocument,
    ConfigFormatError,
    Configuration,
    ConfigurationEnvironment,
    ContribPackage,
    EnabledContrib,
    configuration,
)
lazy from bazaar_contrib_config_versions import VERSIONS
lazy from bazaar_contrib_manager import CONTRIB_MANAGER, ContribManager
lazy from bazaar_core import (
    Composition,
    Context,
    Contrib,
    ContribEntry,
    ContribHub,
    ContribSpec,
    discover,
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
    defaults: Mapping[str, object] | None = None,
) -> tuple[Composition, ContribEntry]:
    resource = files(__package__)
    definitions = load(
        [
            ContribSpec(
                contrib=configer,
                config={"options": configer_options} if configer_options else {},
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
    entries = []
    for entry in definitions.entries:
        config = entry.definition.validate_config(entry.config, context=definitions)
        entries.append(
            EnabledContrib(
                entry=entry.name,
                config=config.model_dump(mode="json") if config is not None else {},
            )
        )
    if defaults is None:
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
                    entries=tuple(entries),
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

    if configer == "config-filesystem" and not any(
        source.name == configer for source in await asyncio.to_thread(discover)
    ):
        managers: list[ContribManager] = []

        @asynccontextmanager
        async def observe_manager(ctx: Context):
            managers.append(ctx.accept(CONTRIB_MANAGER))
            yield

        document = ConfigDocument(
            contribs=(
                ContribPackage(package="bazaar-contrib-config-filesystem~=0.3.0rc1"),
                ContribPackage(
                    enabled=(
                        EnabledContrib(entry="commands"),
                        EnabledContrib(entry="contrib-manager"),
                    )
                ),
            )
        )
        entries, loader = await asyncio.to_thread(
            bootstrap,
            configer="config-memory",
            defaults=document.model_dump(mode="json"),
        )
        async with (
            run(entries, loader) as hub,
            hub.enable(
                Composition(
                    entries=[
                        ContribEntry(
                            name="bootstrap-manager",
                            definition=Contrib(
                                accepts=[CONTRIB_MANAGER], lifespan=observe_manager
                            ),
                        )
                    ]
                )
            ),
        ):
            Configuration.check(hub)
            result = await managers[0].bootstrap()
            if result.error:
                raise ConfigFormatError(result.error)
        await asyncio.to_thread(importlib.invalidate_caches)

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
