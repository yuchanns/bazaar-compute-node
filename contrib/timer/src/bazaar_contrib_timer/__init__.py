"""Shared timer contracts for consumers and independently installed providers."""

from typing import Protocol

from bazaar_core import ServiceKey

__all__ = ["TIMER", "ITimer", "Timer", "TimerCancelledError", "TimerClosedError"]


class TimerCancelledError(RuntimeError):
    """A pending timer was cancelled by its owner."""


class TimerClosedError(RuntimeError):
    """The provider closed before a timer expired."""


class Timer(Protocol):
    """A resettable handle with one waiter and one consumable expiry."""

    @property
    def id(self) -> int: ...

    @property
    def active(self) -> bool: ...

    @property
    def deadline(self) -> int:
        """Absolute deadline in the provider's monotonic milliseconds."""
        ...

    @property
    def generation(self) -> int: ...

    @property
    def expired_generation(self) -> int | None: ...

    def reset(self, delay_ms: int) -> None:
        """Replace an active timer's deadline and increment its generation."""
        ...

    def cancel(self) -> None:
        """Cancel a pending timer; repeated cancellation has no effect."""
        ...

    async def wait(self) -> None:
        """Consume expiry, or raise the shared cancellation/closure error.

        Cancelling this wait also cancels its active timer.
        """
        ...


class ITimer(Protocol):
    """A lifecycle-owned timer service shared through its composition binding."""

    @property
    def tick_ms(self) -> int:
        """Scheduling resolution; actual delivery follows the event loop."""
        ...

    @property
    def maximum_delay_ms(self) -> int: ...

    @property
    def now_ms(self) -> int: ...

    def create(self, delay_ms: int) -> Timer:
        """Schedule a non-negative delay within this provider's horizon.

        Expiry is rounded up to the provider's scheduling resolution.
        A zero delay expires on a later driver iteration.
        """
        ...


TIMER = ServiceKey[ITimer]("timer")
