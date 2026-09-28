"""Role management: permissions and default enrolment role."""

from __future__ import annotations

import json
from typing import Annotated
from uuid import uuid7

from pydantic import BaseModel, Field, StringConstraints, ValidationError
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response

from ..access import Access, allowed
from ..clock import now_ms
from ..permissions import Permission
from ..refs import Refs, expanded
from ..rendering import Renderer
from ..storage import IStorage, Role
from .settings import sections


class RoleForm(BaseModel):
    name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)
    ]
    permissions: list[Permission] = Field(default_factory=list)


class RoleRemoval(BaseModel):
    replacement: str = ""


class RolePages:
    def __init__(self, storage: IStorage, renderer: Renderer, refs: Refs) -> None:
        self._storage = storage
        self._render = renderer
        self.refs = refs

    @allowed(Permission.SETTINGS_ROLES)
    async def list(self, request: Request) -> Response:
        return await self._page(request)

    @allowed(Permission.SETTINGS_ROLES)
    async def new(self, request: Request) -> Response:
        return await self._page(request, new=True)

    @expanded("role_id")
    @allowed(Permission.SETTINGS_ROLES)
    async def show(self, request: Request) -> Response:
        return await self._page(request, role_id=request.path_params["role_id"])

    async def _page(
        self,
        request: Request,
        *,
        role_id: str | None = None,
        new: bool = False,
        error: str | None = None,
    ) -> Response:
        roles = await self._storage.list_roles()
        selected = next((role for role in roles if role.id == role_id), None)
        if role_id is not None and selected is None:
            return HTMLResponse("", status_code=404)
        await self.refs.load([role.id for role in roles])
        groups: dict[str, list[Permission]] = {}
        for point in Permission:
            groups.setdefault(point.module, []).append(point)
        return self._render.page(
            request,
            "settings",
            "settings.html",
            sections=sections(Access.of(request)),
            section="roles",
            view="entry" if new or selected is not None else "entries",
            roles=roles,
            selected=selected,
            new=new,
            groups=groups,
            default=await self._storage.default_role(),
            error=error,
        )

    @allowed(Permission.SETTINGS_ROLES)
    async def create(self, request: Request) -> Response:
        return await self._save(request)

    @expanded("role_id")
    @allowed(Permission.SETTINGS_ROLES)
    async def save(self, request: Request) -> Response:
        return await self._save(request, request.path_params["role_id"])

    async def _save(self, request: Request, role_id: str | None = None) -> Response:
        form = await request.form()
        roles = await self._storage.list_roles()
        selected = next((role for role in roles if role.id == role_id), None)
        if role_id is not None and selected is None:
            return HTMLResponse("", status_code=404)
        error = None
        try:
            values = RoleForm.model_validate(
                {
                    "name": form.get("name"),
                    "permissions": form.getlist("permissions"),
                }
            )
        except ValidationError:
            error = "roles.invalid"
        else:
            if any(role.name == values.name and role.id != role_id for role in roles):
                error = "roles.duplicate"
        if error is not None:
            return await self._page(
                request, role_id=role_id, new=role_id is None, error=error
            )
        role = Role(
            role_id or str(uuid7()),
            values.name,
            frozenset(values.permissions),
            selected.created_at_ms if selected else now_ms(),
            now_ms(),
        )
        await self._storage.save_role(role)
        await self.refs.load([role.id])
        return Response(
            status_code=204,
            headers={
                "HX-Location": json.dumps(
                    {
                        "path": f"/settings/roles/{self.refs.ref(role.id)}",
                        "target": "#main",
                    }
                )
            },
        )

    @expanded("role_id")
    @allowed(Permission.SETTINGS_ROLES)
    async def default(self, request: Request) -> Response:
        role_id = request.path_params["role_id"]
        if not any(role.id == role_id for role in await self._storage.list_roles()):
            return HTMLResponse("", status_code=404)
        await self._storage.set_default_role(role_id)
        return await self._page(request, role_id=role_id)

    @expanded("role_id")
    @allowed(Permission.SETTINGS_ROLES)
    async def remove(self, request: Request) -> Response:
        role_id = request.path_params["role_id"]
        roles = await self._storage.list_roles()
        if not any(role.id == role_id for role in roles):
            return HTMLResponse("", status_code=404)
        try:
            values = RoleRemoval.model_validate(dict(request.query_params))
        except ValidationError:
            return await self._page(request, role_id=role_id, error="roles.invalid")
        replacement = values.replacement or None
        default = await self._storage.default_role()
        if (
            default is not None
            and default.id == role_id
            and replacement not in {role.id for role in roles if role.id != role_id}
        ):
            return await self._page(
                request, role_id=role_id, error="roles.replacement_required"
            )
        if any(
            provider.default_role_id == role_id
            for provider in await self._storage.list_oidc_providers()
        ):
            return await self._page(
                request, role_id=role_id, error="roles.provider_default"
            )
        await self._storage.remove_role(role_id, replacement_id=replacement)
        return Response(
            status_code=204,
            headers={
                "HX-Location": json.dumps(
                    {"path": "/settings/roles", "target": "#main"}
                )
            },
        )
