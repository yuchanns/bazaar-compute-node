"""Sidecar contracts and an ordinary plugin serving installed remote entries."""

from __future__ import annotations

from contextlib import asynccontextmanager
lazy import asyncio

from bazaar_core import Context, Contrib

from .contracts import CLIENT, SERVER, Handler, Server, Sessions
from .handlers import RemoteEntry
lazy from .handlers import Session, remotes
lazy from .remote import Client, RemoteError

__all__ = [
    "CLIENT",
    "SERVER",
    "Client",
    "Handler",
    "RemoteEntry",
    "RemoteError",
    "Server",
    "Sessions",
    "sidecar",
]


@asynccontextmanager
async def lifespan(ctx: Context):
    entries = await asyncio.to_thread(remotes)
    async with ctx.accept(SERVER).serve(lambda: Session(ctx, entries)):
        yield


sidecar = Contrib(accepts=[SERVER], lifespan=lifespan)
