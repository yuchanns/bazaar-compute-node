"""Account identities and their assigned roles."""

from __future__ import annotations

import json

from pydantic import BaseModel, Field, ValidationError
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response

from ..access import Access, allowed
from ..permissions import Permission
from ..refs import Refs, expanded
from ..rendering import Renderer
from ..storage import IStorage
from .settings import sections


class AccountRoles(BaseModel):
    roles: list[str] = Field(default_factory=list)


class AccountPages:
    def __init__(self, storage: IStorage, renderer: Renderer, refs: Refs) -> None:
        self._storage = storage
        self._render = renderer
        self.refs = refs

    @allowed(Permission.SETTINGS_ACCOUNTS)
    async def list(self, request: Request) -> Response:
        return await self._page(request)

    @expanded("account_id")
    @allowed(Permission.SETTINGS_ACCOUNTS)
    async def show(self, request: Request) -> Response:
        return await self._page(request, request.path_params["account_id"])

    async def _page(
        self,
        request: Request,
        account_id: str | None = None,
        *,
        error: str | None = None,
    ) -> Response:
        accounts = await self._storage.list_accounts()
        selected = next(
            (account for account in accounts if account.id == account_id), None
        )
        if account_id is not None and selected is None:
            return HTMLResponse("", status_code=404)
        await self.refs.load([account.id for account in accounts])
        assigned = await self._storage.list_roles(account_id) if account_id else []
        return self._render.page(
            request,
            "settings",
            "settings.html",
            sections=sections(Access.of(request)),
            section="accounts",
            view="entry" if selected is not None else "entries",
            accounts=accounts,
            selected=selected,
            roles=await self._storage.list_roles(),
            assigned={role.id for role in assigned},
            error=error,
        )

    @expanded("account_id")
    @allowed(Permission.SETTINGS_ACCOUNTS)
    async def save(self, request: Request) -> Response:
        account_id = request.path_params["account_id"]
        if await self._storage.get_account(account_id) is None:
            return HTMLResponse("", status_code=404)
        form = await request.form()
        try:
            values = AccountRoles.model_validate({"roles": form.getlist("roles")})
        except ValidationError:
            return await self._page(request, account_id, error="accounts.invalid")
        roles = await self._storage.list_roles()
        if not set(values.roles) <= {role.id for role in roles}:
            return await self._page(request, account_id, error="accounts.invalid")
        await self._storage.set_account_roles(account_id, values.roles)
        return Response(
            status_code=204,
            headers={
                "HX-Location": json.dumps(
                    {
                        "path": f"/settings/accounts/{self.refs.ref(account_id)}",
                        "target": "#main",
                    }
                )
            },
        )
