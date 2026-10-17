"""Keep concurrent reads inside their owning database operation."""

import asyncio
from collections.abc import Awaitable


async def concurrent_reads[T](*operations: Awaitable[T]) -> tuple[T, ...]:
    # Drain every read before its session closes, retaining the original error
    # instead of exposing TaskGroup's exception wrapper through command results.
    results = await asyncio.gather(*operations, return_exceptions=True)
    values: list[T] = []
    for result in results:
        if isinstance(result, BaseException):
            raise result
        values.append(result)
    return tuple(values)
