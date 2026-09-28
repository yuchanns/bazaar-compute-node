"""What the signed-in account may do and may see: the one place the pages ask."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from functools import wraps

from starlette.requests import Request
from starlette.responses import HTMLResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from .permissions import Permission, PermissionHandler
from .storage import Account, IStorage, Role

type Handler[S] = Callable[[S, Request], Awaitable[Response]]


class Access:
    """Two kinds of permission point. A functional point says whether a
    feature may be used at all (`computers.create`, `agents.chat`, ...); a
    data point says which objects of a kind may be touched, read from the
    relations the account stands in. Roles, shares and the rights a share
    carries all arrive here; the pages keep asking the same few things."""

    def __init__(
        self, account: Account, storage: IStorage, roles: Sequence[Role] = ()
    ) -> None:
        self._account = account
        self._storage = storage
        self._permissions = frozenset(
            point for role in roles for point in role.permissions
        )

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

    @property
    def is_admin(self) -> bool:
        return self._account.auth_type == "local" and self._account.name == "admin"

    def allows(self, point: Permission) -> bool:
        return self.is_admin or point in self._permissions

    async def can(self, kind: str, target_id: str, point: Permission) -> bool:
        if not self.allows(point):
            return False
        return target_id in await self._storage.allowed_targets(
            self.subject_id, kind, point
        )

    async def can_see(self, kind: str, target_id: str) -> bool:
        point = (
            Permission.COMPUTERS_VIEW if kind == "computer" else Permission.AGENTS_VIEW
        )
        return await self.can(kind, target_id, point)

    async def visible(self, kind: str, target_ids: Sequence[str]) -> set[str]:
        point = (
            Permission.COMPUTERS_VIEW if kind == "computer" else Permission.AGENTS_VIEW
        )
        if not self.allows(point):
            return set()
        return set(target_ids) & await self._storage.allowed_targets(
            self.subject_id, kind, point
        )


def allowed[S](
    point: Permission, kind: str | None = None
) -> Callable[[Handler[S]], Handler[S]]:
    """The handler is behind a functional point: without it, there is no
    such page."""

    permission = PermissionHandler(point, kind)

    def guard(handler: Handler[S]) -> Handler[S]:
        @wraps(handler)
        async def guarded(self: S, request: Request) -> Response:
            if not await permission(Access.of(request), request.path_params):
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
            access = Access.of(request)
            target_id = request.path_params[param]
            if kind == "computer" and "agent_id" in request.path_params:
                agent_id = request.path_params["agent_id"]
                visible = await access.can_see("agent", agent_id)
                computer_id = await access._storage.agent_computer(agent_id)
                if not visible or computer_id != target_id:
                    return HTMLResponse("", status_code=404)
            elif not await access.can_see(kind, target_id):
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
            account = state["account"]
            roles = await self._storage.list_roles(account.id)
            state["access"] = Access(account, self._storage, roles)
        await self._app(scope, receive, send)


__all__ = ["Access", "AccessGate", "allowed", "sees"]
