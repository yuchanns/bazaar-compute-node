"""Offer the sidecar server and client using this platform's native transport."""

from __future__ import annotations

from contextlib import asynccontextmanager
from functools import partial
lazy import asyncio
lazy import os
lazy from pathlib import Path

from bazaar_contrib_sidecar import CLIENT, SERVER, Client
from bazaar_contrib_sidecar.streams import Connection
from bazaar_contrib_timer import TIMER
from bazaar_core import Context, Contrib
from pydantic import BaseModel, ConfigDict, Field

lazy from .transport import Listener, connect

__all__ = ["Config", "sidecar"]


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    endpoint: str | None = Field(default=None, min_length=1)
    timeout: float = Field(default=30, gt=0, allow_inf_nan=False)


@asynccontextmanager
async def lifespan(ctx: Context, config: Config):
    endpoint = await asyncio.to_thread(
        lambda: str(
            (
                Path(config.endpoint)
                if config.endpoint
                else Path(os.environ.get("BCN_HOME") or Path.home())
                / ".bcn"
                / "sidecar.sock"
            )
            .expanduser()
            .absolute()
        )
    )

    connection = Connection(partial(connect, endpoint))
    client = Client(
        connection.send, connection.aclose, ctx.accept(TIMER), timeout=config.timeout
    )
    try:
        ctx.offer(SERVER, Listener(endpoint))
        ctx.offer(CLIENT, client)
        yield
    finally:
        await client.aclose()


sidecar = Contrib(
    config=Config, accepts=[TIMER], offers=[SERVER, CLIENT], lifespan=lifespan
)
