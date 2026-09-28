"""Role grants on computers and individual agents."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response

from ..access import Access, allowed, sees
from ..permissions import SHARE_POINTS, Permission
from ..refs import Refs, expanded
from ..rendering import Renderer
from ..storage import IStorage


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
        return await self._handle(request, "agent", target_id)

    async def _handle(self, request: Request, kind: str, target_id: str) -> Response:
        context = await self.context(request, kind, target_id)
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
                            kind, target_id, values.grants, points
                        )
                    except LookupError:
                        error = "shares.invalid"
                    else:
                        context = await self.context(request, kind, target_id)
        context["error"] = error
        return self._render.fragment(request, "shares.html", **context)

    async def context(
        self, request: Request, kind: str, target_id: str
    ) -> dict[str, Any]:
        access = Access.of(request)
        points = [
            point
            for point in SHARE_POINTS[kind]
            if await access.can(kind, target_id, point)
        ]
        roles = await self._storage.list_roles()
        shares = await self._storage.list_role_shares(kind, target_id)
        grants = {
            share.role_id: sorted(share.permissions & set(points)) for share in shares
        }
        key = self.refs.ref(target_id)
        url = (
            f"/computers/{key}/shares"
            if kind == "computer"
            else f"/agents/{self.refs.ref(request.path_params['computer_id'])}/{key}/shares"
        )
        return {
            "points": points,
            "share_kind": kind,
            "share_id": f"computer-sharing-{key}"
            if kind == "computer"
            else "agent-sharing",
            "share_data": {
                "roles": [{"id": role.id, "name": role.name} for role in roles],
                "grants": grants,
                "default": f"{kind}s.view",
            },
            "share_url": url,
            "error": None,
        }
