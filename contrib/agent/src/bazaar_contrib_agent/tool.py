"""Start the Agent's bcc client from the application's selected configuration."""

from __future__ import annotations

import asyncio
import importlib
from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast

from bazaar_contrib_config import (
    ConfigDocument,
    Configuration,
    ConfigurationEnvironment,
    configuration,
)
from bazaar_contrib_sidecar import CLIENT, Client, Remote, Symbol
from bazaar_contrib_sidecar.wire import Request, encode
from bazaar_core import Composition, Context, Contrib, ContribEntry, ContribSpec, load
from pydantic import BaseModel, ConfigDict


class Config(BaseModel):
    model_config = ConfigDict(frozen=True)
    environment: ConfigurationEnvironment
    defaults: dict[str, Any]
    build: str


class Tool:
    """Keep the selected bootstrap available for the Agent's tool processes."""

    def __init__(self, configuration: Configuration):
        self.config = Config.model_validate(
            {
                "environment": configuration.environment,
                "defaults": configuration.defaults,
                "build": f"{configuration.build.__module__}:{configuration.build.__qualname__}",
            }
        )

    async def __aenter__(self):
        self.directory = await asyncio.to_thread(TemporaryDirectory, prefix="bcn-bcc-")
        self.path = Path(self.directory.name) / "config.json"
        try:
            await asyncio.to_thread(
                self.path.write_text, self.config.model_dump_json(), encoding="utf-8"
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
    module, name = config.environment.hub.split(":", 1)
    factory = await asyncio.to_thread(
        lambda: getattr(importlib.import_module(module), name)
    )
    module, name = config.build.split(":", 1)
    build = cast(
        Callable[[ConfigDocument], Composition],
        await asyncio.to_thread(lambda: getattr(importlib.import_module(module), name)),
    )
    bootstrap = await asyncio.to_thread(
        load,
        [
            ContribSpec(contrib=entry.entry, config=entry.config)
            for entry in config.environment.entries
        ],
    )
    clients: list[Client] = []

    @asynccontextmanager
    async def consume(ctx: Context):
        clients.append(ctx.accept(CLIENT))
        yield

    def start(document: ConfigDocument) -> Composition:
        composition = build(document)
        entry = ContribEntry(
            name="bcc-client",
            definition=Contrib(accepts=[CLIENT], lifespan=consume),
        )
        return composition.model_copy(
            update={
                "entries": (*composition.entries, entry),
                "roots": (entry.name,),
            }
        )

    async with factory().run(
        bootstrap,
        ContribEntry(
            name="bcc-configuration",
            definition=configuration(config.defaults, build, start=start),
        ),
    ) as hub:
        Configuration.check(hub)
        client = clients[0]
        remote = cast(
            Remote,
            await client.request(
                Request(
                    operation="call",
                    method="accept",
                    args=(encode(Symbol(service), client.reference),),
                ),
                timeout=None,
            ),
        )
        # The dispatcher owns this command's wait budget.
        return await client.request(
            Request(
                operation="call",
                target=remote.id,
                method="request",
                args=(encode(dict(payload), client.reference),),
            ),
            timeout=None,
        )
