"""Role grants on computers and individual agents."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, ValidationError
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response

from ..access import Access, allowed, sees
from ..permissions import SHARE_POINTS, Permission
from ..refs import Refs, expanded
from ..rendering import Renderer
from ..storage import IStorage


class ShareForm(BaseModel):
    role: str
    permissions: list[Permission] = Field(default_factory=list)


class ShareChanges(BaseModel):
    grants: dict[str, frozenset[Permission]]


class SharePages:
    def __init__(self, storage: IStorage, renderer: Renderer, refs: Refs) -> None:
        self._storage = storage
        self._render = renderer
        self.refs = refs

    @expanded("computer_id")
    @allowed(Permission.COMPUTERS_SHARE, "computer")
    @sees("computer", "computer_id")
    async def computer(self, request: Request) -> Response:
        return await self._handle(
            request, "computer", request.path_params["computer_id"]
        )

    @expanded("computer_id", "agent_id")
    @allowed(Permission.AGENTS_VIEW, "agent")
    @sees("computer", "computer_id")
    async def agent(self, request: Request) -> Response:
        access = Access.of(request)
        target_id = request.path_params["agent_id"]
        if not await access.can_share("agent", target_id):
            return HTMLResponse("", status_code=404)
        context = await self.agent_context(request)
        error = None
        if request.method == "POST":
            form = await request.form()
            try:
                values = ShareChanges.model_validate_json(str(form.get("changes", "")))
            except ValidationError:
                error = "shares.invalid"
            else:
                role_ids = {role["id"] for role in context["share_data"]["roles"]}
                points = frozenset(context["points"])
                if not values.grants.keys() <= role_ids or any(
                    not permissions <= points for permissions in values.grants.values()
                ):
                    error = "shares.invalid"
                else:
                    try:
                        await self._storage.save_role_shares(
                            "agent", target_id, values.grants, points
                        )
                    except LookupError:
                        error = "shares.invalid"
                    else:
                        context = await self.agent_context(request)
        context["error"] = error
        return self._render.fragment(request, "agent_profile_sharing.html", **context)

    async def agent_context(self, request: Request) -> dict[str, Any]:
        access = Access.of(request)
        target_id = request.path_params["agent_id"]
        points = [
            point
            for point in SHARE_POINTS["agent"]
            if await access.can("agent", target_id, point)
        ]
        roles = await self._storage.list_roles()
        shares = await self._storage.list_role_shares("agent", target_id)
        grants = {
            share.role_id: sorted(share.permissions & set(points)) for share in shares
        }
        return {
            "points": points,
            "share_data": {
                "roles": [{"id": role.id, "name": role.name} for role in roles],
                "grants": grants,
            },
            "share_url": f"/agents/{self.refs.ref(request.path_params['computer_id'])}/{self.refs.ref(target_id)}/shares",
            "error": None,
        }

    async def _handle(self, request: Request, kind: str, target_id: str) -> Response:
        access = Access.of(request)
        roles = await self._storage.list_roles()
        points = [
            point
            for point in SHARE_POINTS[kind]
            if await access.can(kind, target_id, point)
        ]
        error = None
        saved = False
        selected = request.query_params.get("role")
        if request.method == "POST":
            form = await request.form()
            try:
                values = ShareForm.model_validate(
                    {
                        "role": form.get("role"),
                        "permissions": form.getlist("permissions"),
                    }
                )
            except ValidationError:
                error = "shares.invalid"
            else:
                selected = values.role
                if selected not in {role.id for role in roles} or not set(
                    values.permissions
                ) <= set(points):
                    error = "shares.invalid"
                else:
                    try:
                        await self._storage.save_role_shares(
                            kind,
                            target_id,
                            {selected: frozenset(values.permissions)},
                            frozenset(points),
                        )
                    except LookupError:
                        error = "shares.invalid"
                    else:
                        saved = True
        if selected is None and roles:
            selected = roles[0].id
        if selected is not None and selected not in {role.id for role in roles}:
            return HTMLResponse("", status_code=404)
        shares = await self._storage.list_role_shares(kind, target_id)
        return self._render.fragment(
            request,
            "shares.html",
            kind=kind,
            roles=roles,
            selected=selected,
            points=points,
            grants={share.role_id: share.permissions for share in shares},
            url=request.url.path,
            error=error,
            saved=saved,
        )
