"""Sidecar contracts and an ordinary plugin exposing its Context handlers."""

from __future__ import annotations

from contextlib import asynccontextmanager

from bazaar_core import Context, Contrib

from .contracts import CLIENT, SERVER, Handler, Server, Sessions
lazy from .handlers import Session
lazy from .remote import Client, Remote, RemoteError
lazy from .wire import Construct, Symbol

__all__ = [
    "CLIENT",
    "SERVER",
    "Client",
    "Construct",
    "Handler",
    "Remote",
    "RemoteError",
    "Server",
    "Sessions",
    "Symbol",
    "sidecar",
]


@asynccontextmanager
async def lifespan(ctx: Context):
    async with ctx.accept(SERVER).serve(lambda: Session(ctx)):
        yield


sidecar = Contrib(accepts=[SERVER], lifespan=lifespan)
