from __future__ import annotations

import asyncio


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
