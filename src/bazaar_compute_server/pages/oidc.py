"""Configuration of OIDC sign-in providers."""

from __future__ import annotations

import base64
import json
from typing import Annotated, Literal
from uuid import UUID, uuid7

from pydantic import (
    BaseModel,
    Field,
    HttpUrl,
    StringConstraints,
    ValidationError,
    ValidationInfo,
    model_validator,
)
from starlette.datastructures import UploadFile
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response

from ..access import Access, allowed
from ..clock import now_ms
from ..permissions import Permission
from ..refs import Refs, expanded
from ..rendering import Renderer
from ..storage import IStorage, OIDCProvider
from .settings import sections


class ProviderForm(BaseModel):
    id: UUID
    name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)
    ]
    description: Annotated[
        str, StringConstraints(strip_whitespace=True, max_length=200)
    ] = ""
    default_role_id: str = ""
    session_minutes: int = Field(default=10, ge=1)

    clear_logo: bool = False
    issuer: HttpUrl
    client_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    client_secret: str = ""
    redirect_uri: HttpUrl

    @model_validator(mode="after")
    def existing_role(self, info: ValidationInfo) -> ProviderForm:
        if self.default_role_id and self.default_role_id not in (info.context or ()):
            raise ValueError("Choose an existing role")
        return self


class LogoUpload(BaseModel):
    content_type: Literal["image/png", "image/jpeg", "image/webp", "image/gif"]
    content: Annotated[bytes, Field(min_length=1, max_length=256 * 1024)]


class OrderForm(BaseModel):
    order: list[str]

    @model_validator(mode="after")
    def complete(self, info: ValidationInfo) -> OrderForm:
        if len(self.order) != len(set(self.order)) or set(self.order) != info.context:
            raise ValueError("Login order must include every method once")
        return self


class OIDCPages:
    def __init__(self, storage: IStorage, renderer: Renderer, refs: Refs) -> None:
        self._storage = storage
        self._render = renderer
        self.refs = refs

    @allowed(Permission.SETTINGS_OIDC)
    async def list(self, request: Request) -> Response:
        return await self._page(request)

    @allowed(Permission.SETTINGS_OIDC)
    async def reorder(self, request: Request) -> Response:
        form = await request.form()
        providers = await self._storage.list_oidc_providers()
        try:
            parsed = OrderForm.model_validate_json(
                str(form.get("order", "")),
                context={"password", *(provider.id for provider in providers)},
            )
        except ValidationError:
            return await self._page(request, error="oidc.order_changed")
        await self._storage.save_login_order(parsed.order)
        response = await self._page(request)
        response.headers["HX-Push-Url"] = "/settings/oidc"
        return response

    @allowed(Permission.SETTINGS_OIDC)
    async def new(self, request: Request) -> Response:
        return await self._page(request, new=True)

    @expanded("provider_id")
    @allowed(Permission.SETTINGS_OIDC)
    async def show(self, request: Request) -> Response:
        return await self._page(request, provider_id=request.path_params["provider_id"])

    async def _page(
        self,
        request: Request,
        *,
        provider_id: str | None = None,
        new: bool = False,
        error: str | None = None,
        values: dict[str, str] | None = None,
    ) -> Response:
        providers = await self._storage.list_oidc_providers()
        selected = next(
            (provider for provider in providers if provider.id == provider_id), None
        )
        if provider_id is not None and selected is None:
            return HTMLResponse("", status_code=404)
        await self.refs.load(provider.id for provider in providers)
        # The generated ID is posted with the new form so its callback is known
        # before the provider is registered at the identity service.
        provider_id = provider_id or (values or {}).get("id") or str(uuid7())
        if values is None:
            values = {
                "id": provider_id,
                "name": selected.name if selected else "",
                "description": selected.description if selected else "",
                "default_role_id": (selected.default_role_id or "") if selected else "",
                "session_minutes": str(selected.session_minutes if selected else 10),
                "logo_url": selected.logo_url if selected else "",
                "issuer": selected.issuer if selected else "",
                "client_id": selected.client_id if selected else "",
                "redirect_uri": selected.redirect_uri
                if selected
                else str(request.url_for("oidc_callback", provider_id=provider_id)),
            }
        return self._render.page(
            request,
            "settings",
            "settings.html",
            sections=sections(Access.of(request)),
            section="oidc",
            view="entry" if new or selected else "entries",
            providers=providers,
            order=await self._storage.login_order(),
            selected=selected,
            new=new,
            values=values,
            default=await self._storage.default_role(),
            roles=await self._storage.list_roles(),
            error=error,
        )

    @allowed(Permission.SETTINGS_OIDC)
    async def create(self, request: Request) -> Response:
        return await self._save(request)

    @expanded("provider_id")
    @allowed(Permission.SETTINGS_OIDC)
    async def save(self, request: Request) -> Response:
        return await self._save(request, request.path_params["provider_id"])

    async def _save(self, request: Request, provider_id: str | None = None) -> Response:
        form = await request.form()
        selected = (
            await self._storage.get_oidc_provider(provider_id) if provider_id else None
        )
        if provider_id is not None and selected is None:
            return HTMLResponse("", status_code=404)
        values = {
            key: str(form.get(key, ""))
            for key in (
                "id",
                "name",
                "description",
                "default_role_id",
                "issuer",
                "client_id",
                "redirect_uri",
            )
        }
        values["session_minutes"] = str(form.get("session_minutes", "10"))
        values["logo_url"] = selected.logo_url if selected else ""
        logo = form.get("logo")
        logo_url = values["logo_url"]
        error = None
        try:
            parsed = ProviderForm.model_validate(
                {
                    **values,
                    "clear_logo": form.get("clear_logo", False),
                    "client_secret": form.get("client_secret", ""),
                },
                context={role.id for role in await self._storage.list_roles()},
            )
            if parsed.clear_logo:
                logo_url = ""
            if isinstance(logo, UploadFile) and logo.filename:
                try:
                    uploaded = LogoUpload.model_validate(
                        {
                            "content_type": logo.content_type,
                            "content": await logo.read(256 * 1024 + 1),
                        }
                    )
                except ValidationError:
                    error = "oidc.logo_invalid"
                else:
                    logo_url = f"data:{uploaded.content_type};base64,{base64.b64encode(uploaded.content).decode()}"
            # IDs supplied by the creation form are validated at this boundary.
            new_id = str(parsed.id) if selected is None else selected.id
            if (
                selected is None
                and await self._storage.get_oidc_provider(new_id) is not None
            ):
                error = "oidc.invalid"
            elif not parsed.client_secret and selected is None:
                error = "oidc.secret_required"
            elif await self._storage.default_role() is None:
                error = "oidc.default_required"
        except ValidationError, ValueError:
            error = "oidc.invalid"
        if error:
            return await self._page(
                request,
                provider_id=provider_id,
                new=selected is None,
                error=error,
                values=values,
            )
        provider = OIDCProvider(
            new_id,
            parsed.name,
            logo_url,
            # Preserve the issuer's exact spelling (including an optional final
            # slash): it must match discovery and the signed iss claim.
            values["issuer"].strip(),
            parsed.client_id,
            parsed.client_secret or (selected.client_secret if selected else ""),
            str(parsed.redirect_uri),
            selected.created_at_ms if selected else now_ms(),
            now_ms(),
            description=parsed.description,
            default_role_id=parsed.default_role_id or None,
            session_minutes=parsed.session_minutes,
        )
        await self._storage.save_oidc_provider(provider)
        await self.refs.load([provider.id])
        return Response(
            status_code=204,
            headers={
                "HX-Location": json.dumps(
                    {
                        "path": f"/settings/oidc/{self.refs.ref(provider.id)}",
                        "target": "#main",
                        "swap": "innerHTML",
                    }
                )
            },
        )
