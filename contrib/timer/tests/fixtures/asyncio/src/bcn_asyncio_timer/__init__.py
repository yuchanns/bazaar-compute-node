"""An independently packaged timer provider using real asyncio callbacks."""

from __future__ import annotations

import asyncio
import math
from contextlib import asynccontextmanager
from typing import Annotated

from bazaar_core import Context, Contrib
from pydantic import Field, TypeAdapter

from bazaar_contrib_timer import TIMER, TimerCancelledError, TimerClosedError

_MAX_DELAY_MS = (1 << 32) - 1
_DELAY = TypeAdapter(Annotated[int, Field(strict=True, ge=0, le=_MAX_DELAY_MS)])


class Handle:
    def __init__(self, service: Service, id: int, delay_ms: int):
        self._service = service
        self.id = id
        self.active = True
        self.deadline = 0
        self.generation = 0
        self.expired_generation: int | None = None
        self._callback: asyncio.TimerHandle | None = None
        self._waiter: asyncio.Future[None] | None = None
        self._consumed = False
        self._error: type[TimerCancelledError | TimerClosedError] | None = None
        self.reset(delay_ms)

    def reset(self, delay_ms: int) -> None:
        delay_ms = _DELAY.validate_python(delay_ms)
        if self._service.closed:
            raise TimerClosedError("asyncio timer provider is closed")
        if not self.active:
            raise RuntimeError("only an active timer can be reset")
        if self._callback:
            self._callback.cancel()
        loop = asyncio.get_running_loop()
        self.deadline = math.ceil(loop.time() * 1_000) + delay_ms
        self.generation += 1
        self._callback = loop.call_at(self.deadline / 1_000, self._finish)

    def _finish(
        self, error: type[TimerCancelledError | TimerClosedError] | None = None
    ) -> None:
        if not self.active:
            return
        self.active = False
        self._service.handles.pop(self.id)
        if self._callback:
            self._callback.cancel()
        self._error = error
        if error is None:
            self.expired_generation = self.generation
        if self._waiter is not None and not self._waiter.done():
            if error:
                self._waiter.set_exception(error("asyncio timer ended"))
            else:
                self._waiter.set_result(None)

    def cancel(self) -> None:
        self._finish(TimerCancelledError)

    async def wait(self) -> None:
        if self._waiter is not None:
            raise RuntimeError("timer already has a waiter")
        if self._error:
            raise self._error("asyncio timer ended")
        if self._consumed:
            raise RuntimeError("timer expiry was already consumed")
        if self.active:
            self._waiter = asyncio.get_running_loop().create_future()
            try:
                await self._waiter
            except asyncio.CancelledError:
                self.cancel()
                raise
            finally:
                self._waiter = None
        self._consumed = True


class Service:
    tick_ms = 1
    maximum_delay_ms = _MAX_DELAY_MS

    def __init__(self):
        self.closed = False
        self.handles: dict[int, Handle] = {}
        self._next_id = 1

    @property
    def now_ms(self) -> int:
        return int(asyncio.get_running_loop().time() * 1_000)

    def create(self, delay_ms: int) -> Handle:
        handle = Handle(self, self._next_id, delay_ms)
        self._next_id += 1
        self.handles[handle.id] = handle
        return handle

    def close(self) -> None:
        self.closed = True
        for handle in tuple(self.handles.values()):
            handle._finish(TimerClosedError)


@asynccontextmanager
async def lifespan(ctx: Context):
    service = Service()
    try:
        ctx.offer(TIMER, service)
        yield
    finally:
        service.close()


timer = Contrib(offers=[TIMER], lifespan=lifespan)
