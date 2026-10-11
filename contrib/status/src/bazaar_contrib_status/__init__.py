"""Expose hub-owned lifecycle status through an ordinary contrib service."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Protocol
lazy from collections.abc import AsyncIterator

from bazaar_core import Contrib, ServiceKey
lazy from bazaar_core import Context, ContribStatus

__all__ = ["CONTRIB_STATUS", "ContribStatusQuery", "status"]


class ContribStatusQuery(Protocol):
    def snapshot(self) -> tuple[ContribStatus, ...]: ...


CONTRIB_STATUS = ServiceKey[ContribStatusQuery]("contrib.status")


class _ContribStatus:
    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    def snapshot(self) -> tuple[ContribStatus, ...]:
        return self._ctx.snapshots


@asynccontextmanager
async def _status(ctx: Context) -> AsyncIterator[None]:
    ctx.offer(CONTRIB_STATUS, _ContribStatus(ctx))
    yield


status = Contrib(offers=[CONTRIB_STATUS], lifespan=_status)
