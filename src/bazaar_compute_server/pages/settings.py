"""Settings: for now, the password of whoever is logged in."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import HTMLResponse, Response

from ..gate import set_session
from ..i18n import LANGUAGES
from ..rendering import THEMES, Renderer
from ..sessions import Sessions
from ..storage import Account, IStorage

# the pages of the settings module, each a list entry on the left
# the floor NIST SP 800-63B sets for a chosen password
MIN_PASSWORD_CHARS = 8
SECTIONS = ("appearance", "security")


class SettingsPages:
    def __init__(
        self, storage: IStorage, renderer: Renderer, sessions: Sessions
    ) -> None:
        self._storage = storage
        self._render = renderer
        self._sessions = sessions

    async def page(self, request: Request) -> Response:
        account = _account(request)
        section = request.path_params.get("section", SECTIONS[0])
        if section not in SECTIONS:
            return HTMLResponse("", status_code=404)
        return self._render.page(
            request,
            "settings",
            "settings.html",
            account=account,
            sections=SECTIONS,
            section=section,
            outcome=None,
        )

    async def change_preferences(self, request: Request) -> Response:
        form = await request.form()
        language = str(form.get("language", ""))
        theme = str(form.get("theme", ""))
        if (language and language not in LANGUAGES) or (theme and theme not in THEMES):
            return HTMLResponse("", status_code=422)
        await self._storage.set_preferences(
            _account(request).id, language=language or None, theme=theme or None
        )
        # the whole page reads in the chosen language and theme, so it reloads
        return Response(status_code=204, headers={"HX-Refresh": "true"})

    async def change_password(self, request: Request) -> Response:
        account = _account(request)
        form = await request.form()
        current = str(form.get("current", ""))
        new = str(form.get("new", ""))
        if len(new) < MIN_PASSWORD_CHARS:
            return self._render.fragment(
                request, "password_form.html", outcome="short", status_code=422
            )
        verified = await self._storage.verify_login(account.name, current)
        changed = None
        if verified is not None:
            # replaced only while the hash is still the one just verified: a
            # change that lands in between makes this current password wrong
            changed = await self._storage.change_password(
                account.id, new, expected_hash=verified.password_hash
            )
        if changed is None:
            return self._render.fragment(
                request, "password_form.html", outcome="wrong", status_code=401
            )
        # every other session was issued under the old password and is now
        # refused; this one continues under the new
        response = self._render.fragment(
            request, "password_form.html", outcome="changed"
        )
        set_session(response, self._sessions.issue(changed), request=request)
        return response


def _account(request: Request) -> Account:
    return request.state.account


__all__ = ["SettingsPages"]
