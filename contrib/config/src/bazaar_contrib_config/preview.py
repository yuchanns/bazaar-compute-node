"""Validate a document with target wheels and the selected bootstrap services."""

from __future__ import annotations

from contextlib import asynccontextmanager
lazy import asyncio
lazy import importlib
lazy import json
lazy import sys
lazy from collections.abc import Callable
lazy from pathlib import Path
lazy from typing import Any, cast

from bazaar_core import Contrib
lazy from bazaar_core import (
    Composition,
    Context,
    ContribEntry,
    ContribSpec,
    load,
)

from .loading import CONFIGURATION
lazy from .contracts import CONFIG_CURRENT, CONFIG_STORE, ConfigConflict, ConfigDocument
lazy from .loading import Configuration, ConfigurationEnvironment, ConfigurationPreview


async def preview(request: dict[str, Any]) -> ConfigurationPreview:
    environment = ConfigurationEnvironment.model_validate(request["environment"])
    module, name = environment.hub.split(":", 1)
    factory = await asyncio.to_thread(
        lambda: getattr(importlib.import_module(module), name)
    )
    module, name = request["build"].split(":", 1)
    build = cast(
        Callable[[ConfigDocument], Composition],
        await asyncio.to_thread(lambda: getattr(importlib.import_module(module), name)),
    )
    entries = await asyncio.to_thread(
        load,
        [
            ContribSpec(contrib=entry.entry, config=entry.config)
            for entry in environment.entries
        ],
    )
    results: list[ConfigurationPreview] = []

    @asynccontextmanager
    async def lifespan(ctx: Context):
        store = ctx.accept(CONFIG_STORE)
        snapshot = await store.read()
        if (snapshot.revision if snapshot is not None else None) != request["revision"]:
            raise ConfigConflict("configuration changed during environment preparation")
        converter = ctx.accept(CONFIG_CURRENT)
        candidate = await asyncio.to_thread(converter.convert, request["payload"])
        service = Configuration(ctx, store, converter, request["defaults"], build)
        try:
            _, composition = await service.prepare(candidate.payload)
            baseline = (
                (await service.prepare(snapshot.payload))[1]
                if snapshot is not None
                else None
            )
            service.check(composition, current=baseline)
            error = None
        except Exception as cause:  # noqa: BLE001 - Return target plugin diagnostics without saving the candidate.
            error = str(cause)
        result = ConfigurationPreview(
            candidate=candidate, revision=request["revision"], error=error
        )
        results.append(result)
        ctx.offer(CONFIGURATION, service)
        yield

    async with (
        factory() as hub,
        hub.enable(entries),
        hub.enable(
            Composition(
                entries=[
                    ContribEntry(
                        name="configuration-preview",
                        definition=Contrib(
                            accepts=[CONFIG_STORE, CONFIG_CURRENT],
                            offers=[CONFIGURATION],
                            lifespan=lifespan,
                        ),
                    )
                ]
            )
        ),
    ):
        if not results:
            errors = [str(record.state) for record in hub.snapshots]
            raise RuntimeError("; ".join(errors))
        return results[0]


def main() -> None:
    request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    print(asyncio.run(preview(request)).model_dump_json())


if __name__ == "__main__":
    main()
