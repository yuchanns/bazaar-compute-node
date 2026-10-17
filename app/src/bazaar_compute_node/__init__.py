"""Application entry into the shared contrib composition framework."""

from __future__ import annotations

from contextlib import asynccontextmanager
lazy from collections.abc import AsyncIterator, Awaitable, Callable

lazy from bazaar_core import Composition, ContribEntry, ContribHub

__all__ = ["run"]


@asynccontextmanager
async def run(
    bootstrap: Composition,
    load: ContribEntry,
    *,
    prepare: Callable[[ContribHub], Awaitable[None]] | None = None,
) -> AsyncIterator[ContribHub]:
    """Run a caller's bootstrap and loader in one concrete instance graph."""
    async with ContribHub().run(bootstrap, load) as hub:
        if prepare is not None:
            await prepare(hub)
        yield hub
