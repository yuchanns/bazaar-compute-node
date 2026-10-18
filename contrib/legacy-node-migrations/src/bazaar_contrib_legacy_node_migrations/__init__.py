"""Complete published Node history before business schemas register."""

from __future__ import annotations

from contextlib import asynccontextmanager

from bazaar_contrib_schema import SCHEMA, ModuleSchema
from bazaar_core import Context, Contrib, ServiceKey

from .migrations import MODULE

__all__ = ["LEGACY_NODE_MIGRATIONS", "MODULE", "migrations"]
LEGACY_NODE_MIGRATIONS = ServiceKey[ModuleSchema]("node.legacy_migrations")


@asynccontextmanager
async def lifespan(ctx: Context):
    async with ctx.accept(SCHEMA).register(MODULE, upgrade_only=True):
        ctx.offer(LEGACY_NODE_MIGRATIONS, MODULE)
        yield


migrations = Contrib(
    accepts=[SCHEMA], offers=[LEGACY_NODE_MIGRATIONS], lifespan=lifespan
)
