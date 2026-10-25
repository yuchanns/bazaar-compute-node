"""Task timeout scopes driven by the public timer contract."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING
lazy import asyncio

if TYPE_CHECKING:
    from . import Timer


@asynccontextmanager
async def timeout(timer: Timer, delay_ms: int | None) -> AsyncIterator[None]:
    """Bound a scope with the timer provider and asyncio cancellation semantics.

    Positive delays use the provider's milliseconds and scheduling resolution.
    None disables the timeout. Non-positive delays follow asyncio's fast path.
    """
    if delay_ms is None or delay_ms <= 0:
        async with asyncio.timeout(None if delay_ms is None else 0):
            yield
        return

    loop = asyncio.get_running_loop()
    scope = asyncio.timeout(None)
    handle = timer.create(delay_ms)
    waiter: asyncio.Task[Exception | None] | None = None

    async def expire() -> Exception | None:
        failure: Exception | None = None
        try:
            await handle.wait()
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 -- Propagate the provider's failure to the scope.
            failure = error
        scope.reschedule(loop.time())
        return failure

    try:
        try:
            async with scope:
                waiter = asyncio.create_task(expire())
                yield
        except TimeoutError as error:
            if scope.expired() and waiter is not None:
                failure = waiter.result()
                if failure is not None:
                    raise failure from error
            raise
    finally:
        if waiter is not None:
            waiter.cancel()
        handle.cancel()
        if waiter is not None:
            await asyncio.gather(waiter, return_exceptions=True)


async def wait_for[T](timer: Timer, awaitable: Awaitable[T], delay_ms: int | None) -> T:
    """Return an awaited result with asyncio's non-positive deadline behavior."""
    if delay_ms is not None and delay_ms <= 0:
        return await asyncio.wait_for(awaitable, 0)
    async with timeout(timer, delay_ms):
        return await awaitable
