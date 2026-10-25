"""Lifespan-owned SQLite implementation of the public storage contract."""

from contextlib import asynccontextmanager

from bazaar_contrib_storage import STORAGE, backend_key
from bazaar_contrib_timer import TIMER
from bazaar_core import Context, Contrib

from .database import SqliteConfig, SqliteDatabase, open_database

__all__ = ["SqliteConfig", "SqliteDatabase", "open_database", "sqlite"]


@asynccontextmanager
async def lifespan(ctx: Context, config: SqliteConfig):
    async with open_database(config, ctx.accept(TIMER)) as database:
        with ctx.accept(STORAGE).register("sqlite", database):
            ctx.offer(backend_key("sqlite"), database)
            yield


sqlite = Contrib(
    config=SqliteConfig,
    accepts=[STORAGE, TIMER],
    offers=[backend_key("sqlite")],
    lifespan=lifespan,
)
