from __future__ import annotations

import asyncio
from dataclasses import dataclass


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

    def observe(self, task: asyncio.Task[None], *, component: str) -> None:
        def complete(completed: asyncio.Task[None]) -> None:
            if isinstance(self._state, _Disabled):
                return
            error = (
                RuntimeError(f"{component} was canceled unexpectedly")
                if completed.cancelled()
                else completed.exception()
            )
            self._state = _Failed(
                error or RuntimeError(f"{component} stopped unexpectedly")
            )
            self._event.set()

        task.add_done_callback(complete)

    def fail(self, error: BaseException) -> None:
        if isinstance(self._state, _Disabled) or self._event.is_set():
            return
        self._state = _Failed(error)
        self._event.set()

    async def wait(self) -> None:
        await self._event.wait()
        state = self._state
        error = state.error if isinstance(state, (_Disabled, _Failed)) else None
        if error is None:
            raise RuntimeError("critical task failure has no error")
        raise error
