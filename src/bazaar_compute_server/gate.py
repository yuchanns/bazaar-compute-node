"""The door every page is behind: a valid session, or the login page."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .sessions import COOKIE, SESSION_DAYS, Sessions
from .storage import Account, IStorage

LOGIN_PATH = "/login"
# the most any one request may carry, whoever sends it: a node's report of
# events, or a form from a browser; nothing legitimate comes close
MAX_BODY_BYTES = 1024 * 1024
# what is reachable without a session: the login page itself, the assets it
# needs, and the node endpoints, which prove themselves with a token
_OPEN = ("/login", "/static/", "/node/")


class Gate:
    def __init__(self, app: ASGIApp, *, storage: IStorage, sessions: Sessions) -> None:
        self._app = app
        self._storage = storage
        self._sessions = sessions

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        # the body is read through a limit before anything parses it; the
        # node endpoints answer over the limit in their own words
        bounded = receive if scope["path"].startswith("/node/") else _bounded(receive)
        try:
            if _is_open(scope["path"]):
                await self._app(scope, bounded, send)
                return
            request = Request(scope)
            account = await self.account(request)
            if account is None:
                await _to_login(request)(scope, bounded, send)
                return
            if not _same_origin(request):
                # a session's cookie proves the browser, not the page that
                # made the browser send it; a write comes from our own page
                await Response(status_code=403)(scope, bounded, send)
                return
            scope.setdefault("state", {})["account"] = account
            await self._app(scope, bounded, send)
        except _TooLarge:
            await Response(status_code=413)(scope, receive, send)

    async def account(self, request: Request) -> Account | None:
        """Who the request's cookie says, if the cookie holds up."""

        claim = self._sessions.read(request.cookies.get(COOKIE))
        if claim is None:
            return None
        account = await self._storage.get_account(claim.account_id)
        if account is None or not claim.matches(account):
            return None
        return account


class _TooLarge(Exception):
    pass


def _bounded(receive: Receive) -> Receive:
    """The request's own receive, counting what comes through it."""

    size = 0

    async def receiving() -> Message:
        nonlocal size
        message = await receive()
        size += len(message.get("body", b""))
        if size > MAX_BODY_BYTES:
            raise _TooLarge
        return message

    return receiving


def _same_origin(request: Request) -> bool:
    """Whether a request that changes something came from this site: what
    the browser says of where it was sent from, when it says anything."""

    if request.method in ("GET", "HEAD", "OPTIONS"):
        return True
    site = request.headers.get("Sec-Fetch-Site")
    if site is not None:
        return site in ("same-origin", "none")
    origin = request.headers.get("Origin")
    if origin is None:
        return True
    return origin.partition("://")[2] == request.headers.get("Host", "")


def _is_open(path: str) -> bool:
    return any(path == item or path.startswith(item) for item in _OPEN)


def _to_login(request: Request) -> Response:
    # htmx cannot follow a redirect into the shell; it is told to go there whole
    if request.headers.get("HX-Request") == "true":
        return Response(status_code=401, headers={"HX-Redirect": LOGIN_PATH})
    return RedirectResponse(LOGIN_PATH, status_code=303)


def set_session(response: Response, cookie: str, *, request: Request) -> None:
    response.set_cookie(
        COOKIE,
        cookie,
        max_age=SESSION_DAYS * 24 * 60 * 60,
        path="/",
        httponly=True,
        samesite="lax",
        # a cookie issued over https never travels over plain http
        secure=request.url.scheme == "https",
    )


def clear_session(response: Response) -> None:
    response.delete_cookie(COOKIE, path="/")


__all__ = ["LOGIN_PATH", "MAX_BODY_BYTES", "Gate", "clear_session", "set_session"]
