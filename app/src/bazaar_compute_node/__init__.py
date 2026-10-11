"""Application composition using the generic contrib hub."""

from contextlib import asynccontextmanager
lazy from collections.abc import AsyncIterator

lazy from bazaar_core import Composition, ContribEntry, ContribHub

__all__ = ["run"]


@asynccontextmanager
async def run(bootstrap: Composition, load: ContribEntry) -> AsyncIterator[ContribHub]:
    async with ContribHub().run(bootstrap, load) as hub:
        yield hub
