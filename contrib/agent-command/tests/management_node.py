"""Run an installed application until the controlling process closes its pipe."""

import asyncio
import sys
from contextlib import asynccontextmanager

from bazaar_compute_node.boot import node
from bazaar_contrib_agent import AGENTS
from bazaar_contrib_config import Configuration
from bazaar_core import Composition, Context, Contrib, ContribEntry


async def main():
    @asynccontextmanager
    async def ready(ctx: Context):
        await ctx.accept(AGENTS).wait_applied()
        yield

    async with node(configer_options=(sys.argv[1],)) as hub:
        Configuration.check(hub)
        async with hub.enable(
            Composition(
                entries=[
                    ContribEntry(
                        name="management-test",
                        definition=Contrib(accepts=[AGENTS], lifespan=ready),
                    )
                ]
            )
        ):
            await asyncio.to_thread(print, "ready", flush=True)
            await asyncio.to_thread(sys.stdin.readline)


if __name__ == "__main__":
    asyncio.run(main())
