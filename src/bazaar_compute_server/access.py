"""What the signed-in account may do and may see: the one place the pages ask."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from functools import wraps

from starlette.requests import Request
from starlette.responses import HTMLResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from .storage import Account, IStorage

type Handler[S] = Callable[[S, Request], Awaitable[Response]]


class Access:
    """Two kinds of permission point. A functional point says whether a
    feature may be used at all (`computers.create`, `agents.chat`, ...); a
    data point says which objects of a kind may be touched, read from the
    relations the account stands in. Roles, shares and the rights a share
    carries all arrive here; the pages keep asking the same few things."""

    def __init__(self, account: Account, storage: IStorage) -> None:
        self._account = account
        self._storage = storage

    @classmethod
    def of(cls, request: Request) -> Access:
        """The one the AccessGate gave this request."""

        return request.state.access

    @property
    def account(self) -> Account:
        return self._account

    @property
    def subject_id(self) -> str:
        """Whose relations a list query is scoped to."""

        return self._account.id

    def allows(self, point: str) -> bool:
        """Whether the feature behind a functional point may be used."""

        # the only account is root, which holds every point
        del point
        return True

    async def can_see(self, kind: str, target_id: str) -> bool:
        """Whether one object of a kind is the account's to look at."""

        return await self._storage.has_relation(
            self.subject_id, f"{kind}_owner", target_id
        )

    async def visible(self, kind: str, target_ids: Sequence[str]) -> set[str]:
        """Which of these objects of a kind the account may look at."""

        return await self._storage.related(self.subject_id, f"{kind}_owner", target_ids)


def allowed[S](point: str) -> Callable[[Handler[S]], Handler[S]]:
    """The handler is behind a functional point: without it, there is no
    such page."""

    def guard(handler: Handler[S]) -> Handler[S]:
        @wraps(handler)
        async def guarded(self: S, request: Request) -> Response:
            if not Access.of(request).allows(point):
                return HTMLResponse("", status_code=404)
            return await handler(self, request)

        return guarded

    return guard


def sees[S](kind: str, param: str) -> Callable[[Handler[S]], Handler[S]]:
    """The handler is about one object of a kind, named by a path parameter:
    one the account may not see is not there."""

    def guard(handler: Handler[S]) -> Handler[S]:
        @wraps(handler)
        async def guarded(self: S, request: Request) -> Response:
            if not await Access.of(request).can_see(kind, request.path_params[param]):
                return HTMLResponse("", status_code=404)
            return await handler(self, request)

        return guarded

    return guard


class AccessGate:
    """Behind the Gate: gives every request that has an account its Access."""

    def __init__(self, app: ASGIApp, *, storage: IStorage) -> None:
        self._app = app
        self._storage = storage

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        state = scope.get("state")
        if state is not None and "account" in state:
            state["access"] = Access(state["account"], self._storage)
        await self._app(scope, receive, send)


__all__ = ["Access", "AccessGate", "allowed", "sees"]
