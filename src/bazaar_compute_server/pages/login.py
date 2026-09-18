"""Logging in and out."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import Response

from ..gate import LOGIN_PATH, clear_session, set_session
from ..rendering import Renderer
from ..sessions import Sessions
from ..storage import IStorage

HOME = "/agents"


class LoginPages:
    def __init__(
        self, storage: IStorage, renderer: Renderer, sessions: Sessions
    ) -> None:
        self._storage = storage
        self._render = renderer
        self._sessions = sessions

    async def form(self, request: Request) -> Response:
        return self._render.standalone(request, "login.html", failed=False)

    async def login(self, request: Request) -> Response:
        form = await request.form()
        account = await self._storage.verify_login(
            str(form.get("name", "")).strip(), str(form.get("password", ""))
        )
        if account is None:
            # a failed login is a fragment too: htmx swaps it into the form
            return self._render.fragment(
                request, "login_form.html", failed=True, status_code=401
            )
        response = Response(status_code=204, headers={"HX-Redirect": HOME})
        set_session(response, self._sessions.issue(account), request=request)
        return response

    async def logout(self, request: Request) -> Response:
        del request
        response = Response(status_code=204, headers={"HX-Redirect": LOGIN_PATH})
        clear_session(response)
        return response


__all__ = ["HOME", "LoginPages"]
