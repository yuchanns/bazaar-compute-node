"""Write the node's health into the audit stream on a fixed beat."""

from __future__ import annotations

import asyncio
import platform
import sys
from collections.abc import Callable, Mapping

from ..core.audit import AuditRecorder
from ..core.correlation import CorrelationContext
from ..core.models import RuntimeEventState
from ..core.timerwheel import TimerWheel, TimerWheelClosedError

# the telegram long-poll timeout, so one beat is one poll: a consumer that
# sees no event for two beats knows the node is gone rather than quiet
HEALTH_INTERVAL_MS = 50_000


class HealthReporter:
    """Append a `node.health` event now and then every interval."""

    def __init__(
        self,
        *,
        timer_wheel: TimerWheel,
        audit: AuditRecorder,
        health: Callable[[], Mapping[str, object]],
        version: str,
    ) -> None:
        self._timer_wheel = timer_wheel
        self._audit = audit
        self._health = health
        self._version = version
        self._task: asyncio.Task[None] | None = None

    async def start(self, *, timeout: float) -> None:
        del timeout
        if self._task is not None:
            return
        # the first beat is part of starting, so a consumer sees the node the
        # moment it is ready rather than one interval later
        await self.report()
        self._task = asyncio.create_task(self._run(), name="bcn-health-reporter")

    async def stop(self, *, timeout: float) -> None:
        del timeout
        task = self._task
        if task is None:
            return
        self._task = None
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def report(self) -> None:
        await self._audit.append(
            event_name="node.health",
            state=RuntimeEventState.COMPLETED,
            correlation=CorrelationContext(),
            metadata={
                **self._health(),
                "version": self._version,
                "python": sys.version.split()[0],
                "system": platform.system(),
                "gil_enabled": _gil_enabled(),
                # a consumer that sees two of these go by without a beat may
                # treat the node as gone
                "interval_ms": HEALTH_INTERVAL_MS,
            },
        )

    async def _run(self) -> None:
        while True:
            try:
                await self._timer_wheel.create(HEALTH_INTERVAL_MS).wait()
            except TimerWheelClosedError:
                return
            await self.report()


def _gil_enabled() -> bool:
    is_gil_enabled = getattr(sys, "_is_gil_enabled", None)
    return True if is_gil_enabled is None else bool(is_gil_enabled())


__all__ = ["HEALTH_INTERVAL_MS", "HealthReporter"]
