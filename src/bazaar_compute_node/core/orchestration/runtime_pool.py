"""Hold one Agent's runtime instances and decide which one serves a session."""

from __future__ import annotations

from collections.abc import Sequence

from ..runtime import IRuntime


class RuntimePool:
    """Address one Agent's runtime instances by their configuration index."""

    def __init__(self, runtimes: Sequence[IRuntime]) -> None:
        if not runtimes:
            raise ValueError("runtimes must not be empty")
        for runtime in runtimes:
            if not isinstance(runtime.name, str) or not runtime.name:
                raise ValueError("runtime.name must be a non-empty string")
        self._runtimes = tuple(runtimes)
        self._cursor = 0

    def all(self) -> tuple[IRuntime, ...]:
        return self._runtimes

    def get(self, index: int) -> IRuntime:
        return self._runtimes[index]

    def select(self) -> int:
        index = self._cursor
        self._cursor = (self._cursor + 1) % len(self._runtimes)
        return index
