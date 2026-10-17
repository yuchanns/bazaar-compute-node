"""Keep a real file open for the lifetime of the agent that owns it."""

from contextlib import asynccontextmanager

import aiofiles
from aiofiles.threadpool.text import AsyncTextIOWrapper
from bazaar_core import Context, Contrib, ServiceKey

from bazaar_contrib_agent import AGENT

JOURNAL = ServiceKey[AsyncTextIOWrapper]("example.agent-journal")


@asynccontextmanager
async def lifespan(ctx: Context):
    agent = ctx.accept(AGENT)
    async with aiofiles.open(agent.workspace / "journal.txt", "a") as stream:
        await stream.write(f"started {agent.name}\n")
        await stream.flush()
        ctx.offer(JOURNAL, stream)
        try:
            yield
        finally:
            await stream.write(f"released {agent.name}\n")


journal = Contrib(accepts=[AGENT], offers=[JOURNAL], lifespan=lifespan)
