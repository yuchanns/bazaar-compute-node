"""Register Node migrations before its business data service starts."""

from __future__ import annotations

from contextlib import asynccontextmanager

from bazaar_contrib_schema import SCHEMA, ModuleSchema
from bazaar_core import Context, Contrib, ServiceKey

from .migrations import MODULE

__all__ = ["MODULE", "NODE_MIGRATIONS", "migrations"]
NODE_MIGRATIONS = ServiceKey[ModuleSchema]("node.migrations")


@asynccontextmanager
async def lifespan(ctx: Context):
    async with ctx.accept(SCHEMA).register(MODULE):
        ctx.offer(NODE_MIGRATIONS, MODULE)
        yield


migrations = Contrib(accepts=[SCHEMA], offers=[NODE_MIGRATIONS], lifespan=lifespan)
