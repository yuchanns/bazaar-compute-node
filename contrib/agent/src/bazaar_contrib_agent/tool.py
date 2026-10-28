"""Fix the Agent's bcc client to the sidecar the node runs."""

from __future__ import annotations

import asyncio
import importlib
from collections.abc import Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast

from bazaar_contrib_config import (
    ConfigCandidate,
    ConfigDocument,
    Configuration,
    ConfigurationEnvironment,
)
from bazaar_contrib_sidecar import CLIENT, Client
from bazaar_core import (
    Context,
    Contrib,
    ContribEntry,
    ContribHub,
    ContribSpec,
    ServiceKey,
    load,
)
from pydantic import BaseModel, ConfigDict


class Config(BaseModel):
    model_config = ConfigDict(frozen=True)
    hub: str
    entries: tuple[ContribSpec, ...]


class Tool:
    """Record the client entries a bcc process loads instead of the node's configuration."""

    def __init__(self, configuration: Configuration):
        self.configuration = configuration

    async def __aenter__(self):
        configuration = self.configuration
        document = ConfigDocument.model_validate(
            cast(ConfigCandidate, configuration.candidate).payload
        )
        composition = await asyncio.to_thread(configuration.build, document)
        chosen: list[ContribEntry] = []
        pending: list[ServiceKey[Any]] = [CLIENT]
        while pending:
            key = pending.pop()
            for entry in composition.entries:
                if key in entry.definition.offers and entry not in chosen:
                    chosen.append(entry)
                    pending.extend(
                        entry.definition.dependencies(
                            entry.definition.validate_config(
                                entry.config, context=composition
                            )
                        )
                    )
        config = Config(
            hub=cast(ConfigurationEnvironment, configuration.environment).hub,
            entries=tuple(
                ContribSpec(contrib=entry.source.name, config=entry.config)
                for entry in reversed(chosen)
                if entry.source is not None
            ),
        )
        self.directory = await asyncio.to_thread(TemporaryDirectory, prefix="bcn-bcc-")
        self.path = Path(self.directory.name) / "config.json"
        try:
            await asyncio.to_thread(
                self.path.write_text, config.model_dump_json(), encoding="utf-8"
            )
        except BaseException:
            await asyncio.to_thread(self.directory.cleanup)
            raise
        return self

    async def __aexit__(self, *_: object) -> None:
        await asyncio.to_thread(self.directory.cleanup)


async def request(
    path: Path, service: str, payload: Mapping[str, object]
) -> Mapping[str, object]:
    config = Config.model_validate_json(
        await asyncio.to_thread(path.read_text, encoding="utf-8")
    )
    module, name = config.hub.split(":", 1)
    factory = cast(
        type[ContribHub],
        await asyncio.to_thread(lambda: getattr(importlib.import_module(module), name)),
    )
    composition = await asyncio.to_thread(load, list(config.entries))
    clients: list[Client] = []

    @asynccontextmanager
    async def consume(ctx: Context):
        clients.append(ctx.accept(CLIENT))
        yield

    composition = composition.model_copy(
        update={
            "entries": (
                *composition.entries,
                ContribEntry(
                    name="bcc-client",
                    definition=Contrib(accepts=[CLIENT], lifespan=consume),
                ),
            )
        }
    )
    async with factory() as hub, hub.enable(composition):
        # The dispatcher owns this command's wait budget.
        return cast(
            Mapping[str, object],
            await clients[0].call(service, "request", dict(payload), timeout=None),
        )
