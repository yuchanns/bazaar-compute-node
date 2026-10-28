"""The offered client calling services exposed by another process."""

from __future__ import annotations

from enum import Enum, auto
from math import ceil
from types import EllipsisType
lazy from collections.abc import Awaitable, Callable

from bazaar_contrib_timer import Timer, wait_for
from pydantic import JsonValue
from pydantic_core import to_jsonable_python

from .wire import Failure, Request, Result


class _State(Enum):
    OPEN = auto()
    CLOSED = auto()


class RemoteError(RuntimeError):
    """An exposed member failed; its type and message are preserved."""

    def __init__(self, type: str, message: str) -> None:
        self.type = type
        super().__init__(f"{type}: {message}")


class Client:
    """Call exposed service members over a provider's request and close operations."""

    def __init__(
        self,
        send: Callable[[Request], Awaitable[Result | Failure]],
        close: Callable[[], Awaitable[None]],
        timer: Timer,
        *,
        timeout: float | None = 30,
    ) -> None:
        self.send = send
        self.close = close
        self.timer = timer
        self.timeout = timeout
        self._state = _State.OPEN

    async def call(
        self,
        service: str,
        method: str,
        *args: object,
        timeout: float | None | EllipsisType = ...,  # noqa: ASYNC109 - Override one RPC budget without mutating the shared client.
        **kwargs: object,
    ) -> JsonValue:
        if self._state is _State.CLOSED:
            raise ConnectionError("the sidecar connection has closed")
        timeout = self.timeout if timeout is ... else timeout
        response = await wait_for(
            self.timer,
            self.send(
                Request(
                    service=service,
                    method=method,
                    args=to_jsonable_python(args),
                    kwargs=to_jsonable_python(kwargs),
                )
            ),
            None if timeout is None else ceil(timeout * 1000),
        )
        if isinstance(response, Failure):
            raise RemoteError(response.type, response.message)
        return response.value

    async def aclose(self) -> None:
        self._state = _State.CLOSED
        await self.close()
