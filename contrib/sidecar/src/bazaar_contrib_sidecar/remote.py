"""The offered client and its connection-scoped remote objects."""

from __future__ import annotations

from contextlib import asynccontextmanager
from enum import Enum, auto
from math import ceil
from types import EllipsisType
lazy from collections.abc import Awaitable, Callable
lazy from typing import Any

from bazaar_contrib_timer import Timer, wait_for

from .wire import Failure, Reference, Request, Result, decode, encode


class _State(Enum):
    OPEN = auto()
    CLOSED = auto()


class RemoteError(RuntimeError):
    """A public server method failed; its type and message are preserved."""

    def __init__(self, type: str, message: str) -> None:
        self.type = type
        super().__init__(f"{type}: {message}")


class Remote:
    """A server object retained by one client session."""

    def __init__(self, client: Client, id: int) -> None:
        self.client = client
        self.id = id

    async def get(self, name: str) -> Any:
        return await self.client.request(
            Request(operation="get", target=self.id, method=name)
        )

    async def call(self, name: str, *args: object, **kwargs: object) -> Any:
        return await self.client.request(
            Request(
                operation="call",
                target=self.id,
                method=name,
                args=tuple(encode(value, self.client.reference) for value in args),
                kwargs={
                    key: encode(value, self.client.reference)
                    for key, value in kwargs.items()
                },
            )
        )

    @asynccontextmanager
    async def enter(self):
        value = await self.client.request(Request(operation="enter", target=self.id))
        try:
            yield value
        finally:
            if self.client._state is _State.OPEN:
                await self.client.request(Request(operation="exit", target=self.id))

    async def release(self) -> None:
        await self.client.request(Request(operation="release", target=self.id))


class Client:
    """Expose remote objects over a provider's request and close operations."""

    def __init__(
        self,
        send: Callable[[Request], Awaitable[Result | Failure]],
        close: Callable[[], Awaitable[None]],
        timer: Timer,
        *,
        timeout: float | None = 30,
    ) -> None:
        self.context = Remote(self, 0)
        self.send = send
        self.close = close
        self.timer = timer
        self.timeout = timeout
        self._state = _State.OPEN

    def reference(self, value: object) -> Reference:
        if not isinstance(value, Remote) or value.client is not self:
            raise TypeError("use a Symbol, Construct, or object from this connection")
        return Reference(id=value.id)

    async def request(
        self,
        request: Request,
        *,
        timeout: float | None | EllipsisType = ...,  # noqa: ASYNC109 - Override one RPC budget without mutating the shared client.
    ) -> Any:
        if self._state is _State.CLOSED:
            raise ConnectionError("the sidecar connection has closed")
        timeout = self.timeout if timeout is ... else timeout
        response = await wait_for(
            self.timer,
            self.send(request),
            None if timeout is None else ceil(timeout * 1000),
        )
        if isinstance(response, Failure):
            raise RemoteError(response.type, response.message)
        return decode(response.value, lambda value: Remote(self, value.id))

    async def aclose(self) -> None:
        self._state = _State.CLOSED
        await self.close()
