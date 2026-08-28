"""Hold one Agent's runtime instances and decide which one serves a session."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from ..runtime import IRuntime

_BAN_MS = 3_600_000


class RuntimePool:
    """Address one Agent's runtime instances by their configuration index."""

    def __init__(
        self,
        runtimes: Sequence[IRuntime],
        *,
        clock: Callable[[], int],
    ) -> None:
        if not runtimes:
            raise ValueError("runtimes must not be empty")
        for runtime in runtimes:
            if not isinstance(runtime.name, str) or not runtime.name:
                raise ValueError("runtime.name must be a non-empty string")
        self._runtimes = tuple(runtimes)
        self._clock = clock
        self._ban_until_ms: dict[int, int] = {}

    def all(self) -> tuple[IRuntime, ...]:
        return self._runtimes

    def get(self, index: int) -> IRuntime:
        return self._runtimes[index]

    def select(self) -> int:
        # configuration order is priority order: a runtime keeps serving until
        # it fails, and only its ban moves selection on to the next one
        now_ms = self._clock()
        for index in range(len(self._runtimes)):
            ban_until_ms = self._ban_until_ms.get(index)
            if ban_until_ms is None or ban_until_ms <= now_ms:
                self._ban_until_ms.pop(index, None)
                return index
        # everything is banned, so half-open the one banned longest ago and
        # leave its ban standing: a failed probe extends it and moves the slot
        # on to the next runtime, a completed turn lifts it
        return min(self._ban_until_ms, key=self._ban_until_ms.__getitem__)

    def record_failure(self, index: int) -> int:
        ban_until_ms = self._clock() + _BAN_MS
        self._ban_until_ms[index] = ban_until_ms
        return ban_until_ms

    def record_success(self, index: int) -> int | None:
        return self._ban_until_ms.pop(index, None)
