"""Failure notifications shared by contrib resources and their lifecycle owner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable
lazy import asyncio
lazy from collections.abc import Awaitable


@dataclass(frozen=True, slots=True)
class _Disabled:
    error: BaseException | None = None


@dataclass(frozen=True, slots=True)
class _Waiting:
    pass


@dataclass(frozen=True, slots=True)
class _Failed:
    error: BaseException


class TaskFailureSignal:
    """Expose unexpected long-lived task termination to its lifecycle owner."""

    def __init__(self) -> None:
        self._event = asyncio.Event()
        self._state: _Disabled | _Waiting | _Failed = _Disabled()

    def reset(self) -> None:
        self._event = asyncio.Event()
        self._state = _Waiting()

    def disable(self) -> None:
        state = self._state
        self._state = _Disabled(
            state.error if isinstance(state, (_Disabled, _Failed)) else None
        )

    async def observe(self, work: Awaitable[None], *, component: str) -> None:
        """Run an owned worker and report its termination to the supervisor."""
        try:
            await work
        except asyncio.CancelledError:
            self.fail(RuntimeError(f"{component} was canceled unexpectedly"))
            raise
        except BaseException as error:  # noqa: BLE001 - The supervisor owns failure propagation.
            self.fail(error)
        else:
            self.fail(RuntimeError(f"{component} stopped unexpectedly"))

    def fail(self, error: BaseException) -> None:
        if not isinstance(self._state, _Waiting):
            return
        self._state = _Failed(error)
        self._event.set()

    async def wait(self) -> None:
        await self._event.wait()
        state = self._state
        error = state.error if isinstance(state, (_Disabled, _Failed)) else None
        if error is None:
            raise RuntimeError("task failure notification has no associated exception")
        raise error


@runtime_checkable
class TaskFailureSource(Protocol):
    """A resource reporting unexpected task termination to its owning contrib."""

    async def wait_failure(self) -> None: ...
