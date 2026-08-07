from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Protocol


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
