from __future__ import annotations

import asyncio
from dataclasses import dataclass
from math import isfinite
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class TimeoutBudget:
    """Positive timeout boundaries supplied by the composition root."""

    startup_seconds: float
    provider_call_seconds: float
    command_seconds: float
    shutdown_seconds: float

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.startup_seconds, "startup_seconds"),
            (self.provider_call_seconds, "provider_call_seconds"),
            (self.command_seconds, "command_seconds"),
            (self.shutdown_seconds, "shutdown_seconds"),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"{field_name} must be a finite positive number")


class IAsyncLifecycle(Protocol):
    """A cancellable component lifecycle owned by the application boundary.

    Implementations must make ``start`` and ``stop`` idempotent, keep all I/O
    awaitable, propagate caller cancellation, and leave recoverable state when
    a bounded timeout expires. ``stop`` must not close shared dependencies
    owned by the composition root.
    """

    async def start(self, *, timeout: float) -> None:
        """Start accepting work within the supplied timeout."""
        ...

    async def stop(self, *, timeout: float) -> None:
        """Stop new work and release only resources owned by this component."""
        ...


class TaskFailureSignal:
    """Expose unexpected long-lived task termination to its lifecycle owner."""

    def __init__(self) -> None:
        self._event = asyncio.Event()
        self._error: BaseException | None = None
        self._enabled = False

    def reset(self) -> None:
        self._event = asyncio.Event()
        self._error = None
        self._enabled = True

    def disable(self) -> None:
        self._enabled = False

    def observe(self, task: asyncio.Task[None], *, component: str) -> None:
        def complete(completed: asyncio.Task[None]) -> None:
            if not self._enabled:
                return
            error = (
                RuntimeError(f"{component} was canceled unexpectedly")
                if completed.cancelled()
                else completed.exception()
            )
            self._error = error or RuntimeError(f"{component} stopped unexpectedly")
            self._event.set()

        task.add_done_callback(complete)

    def fail(self, error: BaseException) -> None:
        if not self._enabled or self._event.is_set():
            return
        self._error = error
        self._event.set()

    async def wait(self) -> None:
        await self._event.wait()
        error = self._error
        if error is None:
            raise RuntimeError("critical task failure has no error")
        raise error


@runtime_checkable
class ITaskFailureSource(Protocol):
    """A shared component whose runtime failure must stop the node."""

    async def wait_failure(self) -> None: ...
