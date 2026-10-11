"""Discover and use the status service from an installed distribution."""

from __future__ import annotations

import asyncio
import sys
from importlib.metadata import version
from importlib.resources import files
lazy from contextlib import asynccontextmanager
lazy from importlib.metadata import entry_points

from bazaar_core import (
    Composition,
    Context,
    Contrib,
    ContribEntry,
    ContribHub,
    Running,
)

from bazaar_contrib_status import CONTRIB_STATUS, ContribStatusQuery


async def use():
    entries = await asyncio.to_thread(
        entry_points, group="bazaar_compute_node.contribs", name="status"
    )
    definition = await asyncio.to_thread(next(iter(entries)).load)
    services: list[ContribStatusQuery] = []

    @asynccontextmanager
    async def inspect(ctx: Context):
        services.append(ctx.accept(CONTRIB_STATUS))
        yield

    async with (
        ContribHub() as hub,
        hub.enable(
            Composition(
                entries=[
                    ContribEntry(name="status", definition=definition),
                    ContribEntry(
                        name="inspect",
                        definition=Contrib(accepts=[CONTRIB_STATUS], lifespan=inspect),
                    ),
                ]
            )
        ),
    ):
        service = services[0]
        if not any(
            item.name == "status" and isinstance(item.state, Running)
            for item in service.snapshot()
        ):
            raise RuntimeError("installed status entry did not expose its snapshot")


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: package_smoke.py VERSION")
    if version("bazaar-contrib-status") != sys.argv[1]:
        raise RuntimeError("installed status distribution has a different version")
    if not files("bazaar_contrib_status").joinpath("py.typed").is_file():
        raise RuntimeError("status wheel is missing its typing marker")
    asyncio.run(use())


if __name__ == "__main__":
    main()
