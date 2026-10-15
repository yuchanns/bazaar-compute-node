"""Transport-independent server sessions and the offered client service."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import Protocol

from bazaar_core import ServiceKey

from .remote import Client
from .wire import Failure, Request, Result

type Response = Result | Failure
type Handler = Callable[[Request], Awaitable[Response]]
type Sessions = Callable[[], AbstractAsyncContextManager[Handler]]


class Server(Protocol):
    """Bind session handlers for the duration of their owning plugin lifespan."""

    def serve(self, sessions: Sessions) -> AbstractAsyncContextManager[None]: ...


SERVER = ServiceKey[Server]("sidecar.server")
CLIENT = ServiceKey[Client]("sidecar.client")
