"""Offer the sidecar server and client using this platform's native transport."""

from __future__ import annotations

from contextlib import asynccontextmanager
lazy import asyncio
lazy import os
lazy from pathlib import Path
lazy from uuid import NAMESPACE_URL, uuid5

from bazaar_contrib_sidecar import CLIENT, SERVER, Client
from bazaar_core import Context, Contrib
from pydantic import BaseModel, ConfigDict, Field

lazy from .transport import Channel, Listener

__all__ = ["Config", "sidecar"]


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    endpoint: str | None = Field(default=None, min_length=1)
    timeout: float = Field(default=30, gt=0, allow_inf_nan=False)


@asynccontextmanager
async def lifespan(ctx: Context, config: Config):
    endpoint = config.endpoint
    if endpoint is None:
        directory = await asyncio.to_thread(
            lambda: str(
                (Path(os.environ.get("BCN_HOME") or Path.home()) / ".bcn").resolve()
            ).casefold()
        )
        endpoint = rf"\\.\pipe\bcn-sidecar-{uuid5(NAMESPACE_URL, directory).hex}"
    channel = Channel(endpoint)
    client = Client(channel.send, channel.aclose, timeout=config.timeout)
    try:
        ctx.offer(SERVER, Listener(endpoint))
        ctx.offer(CLIENT, client)
        yield
    finally:
        await client.aclose()


sidecar = Contrib(config=Config, offers=[SERVER, CLIENT], lifespan=lifespan)
