"""OIDC protocol calls with single-use, browser-bound transactions in BCS storage."""

from __future__ import annotations

from hashlib import sha256
from secrets import token_urlsafe

from authlib.integrations.base_client import OAuthError
from authlib.integrations.starlette_client import StarletteOAuth2App
from httpx2 import HTTPError
from joserfc.errors import JoseError
from pydantic import BaseModel, Field, ValidationError
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from .. import reauth
from ..clock import now_ms
from ..gate import set_session
from ..rendering import Renderer
from ..sessions import Sessions
from ..storage import IStorage, OIDCProvider, OIDCTransaction
from .login import HOME

LIFETIME = 600
COOKIE = "bcs_oidc"


class Callback(BaseModel):
    state: str = Field(min_length=1, max_length=256)
    code: str = Field(default="", max_length=8192)
    error: str = Field(default="", max_length=256)


class Identity(BaseModel):
    iss: str = Field(min_length=1)
    sub: str = Field(min_length=1)
    name: str = ""
    email: str = ""


def client(provider: OIDCProvider) -> StarletteOAuth2App:
    # BCS stores/consumes state itself; these Authlib protocol methods do not
    # use the framework session adapter or expose protocol data in cookies.
    return StarletteOAuth2App(
        framework=None,
        client_id=provider.client_id,
        client_secret=provider.client_secret,
        server_metadata_url=provider.issuer.rstrip("/")
        + "/.well-known/openid-configuration",
        client_kwargs={
            "scope": "openid profile email",
            "code_challenge_method": "S256",
            "timeout": 15,
        },
    )


class OIDCLoginPages:
    def __init__(
        self, storage: IStorage, renderer: Renderer, sessions: Sessions
    ) -> None:
        self._storage = storage
        self._render = renderer
        self._sessions = sessions

    async def failure(self, request: Request, reason: str) -> Response:
        response = self._render.standalone(
            request,
            "login.html",
            failed=False,
            oidc_error=reason,
            providers=await self._storage.list_oidc_providers(),
            order=await self._storage.login_order(),
        )
        response.delete_cookie(COOKIE, path="/login/oidc")
        response.delete_cookie(reauth.COOKIE, path="/")
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    async def start(self, request: Request) -> Response:
        provider = await self._storage.get_oidc_provider(
            request.path_params["provider_id"]
        )
        if provider is None:
            return await self.failure(request, "oidc.unavailable")
        return await self._start(request, provider)

    async def resume(self, request: Request) -> Response:
        hint = reauth.Hint.read(
            request.cookies.get(reauth.COOKIE, ""), self._sessions.key
        )
        if hint is None:
            return await self.failure(request, "oidc.expired")
        account = await self._storage.get_account(hint.account_id)
        provider = await self._storage.get_oidc_provider(hint.provider_id)
        if (
            account is None
            or account.auth_type != "oidc"
            or account.session_version != hint.session_version
            or provider is None
            or provider.issuer != hint.issuer
        ):
            return await self.failure(request, "oidc.expired")
        return await self._start(request, provider, hint)

    async def _start(
        self, request: Request, provider: OIDCProvider, hint: reauth.Hint | None = None
    ) -> Response:
        state, nonce, verifier, browser = (token_urlsafe(32) for _ in range(4))
        oauth = client(provider)
        try:
            metadata = await oauth.load_server_metadata()
            if metadata.get("issuer") != provider.issuer:
                return await self.failure(request, "oidc.unavailable")
            authorization = await oauth.create_authorization_url(
                provider.redirect_uri,
                state=state,
                nonce=nonce,
                code_verifier=verifier,
                **({"prompt": "none"} if hint else {}),
            )
        except HTTPError, OAuthError, ValueError, KeyError, RuntimeError:
            return await self.failure(request, "oidc.unavailable")
        await self._storage.save_oidc_transaction(
            OIDCTransaction(
                state,
                provider.id,
                sha256(browser.encode()).hexdigest(),
                nonce,
                verifier,
                provider.redirect_uri,
                now_ms() + LIFETIME * 1000,
                hint.issuer if hint else "",
                hint.subject if hint else "",
            )
        )
        response = RedirectResponse(authorization["url"], status_code=302)
        response.delete_cookie(reauth.COOKIE, path="/")
        response.set_cookie(
            COOKIE,
            browser,
            max_age=LIFETIME,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="lax",
            path="/login/oidc",
        )
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    async def callback(self, request: Request) -> Response:
        try:
            values = Callback.model_validate(dict(request.query_params))
        except ValidationError:
            return await self.failure(request, "oidc.expired")
        transaction = await self._storage.consume_oidc_transaction(
            values.state,
            sha256(request.cookies.get(COOKIE, "").encode()).hexdigest(),
            request.path_params["provider_id"],
        )
        if transaction is None:
            return await self.failure(request, "oidc.expired")
        if values.error or not values.code:
            return await self.failure(request, "oidc.failed")
        provider = await self._storage.get_oidc_provider(transaction.provider_id)
        if provider is None:
            return await self.failure(request, "oidc.unavailable")
        oauth = client(provider)
        try:
            metadata = await oauth.load_server_metadata()
            if metadata.get("issuer") != provider.issuer:
                return await self.failure(request, "oidc.unavailable")
            token = await oauth.fetch_access_token(
                code=values.code,
                redirect_uri=transaction.redirect_uri,
                code_verifier=transaction.code_verifier,
            )
            claims = await oauth.parse_id_token(
                token,
                nonce=transaction.nonce,
                claims_options={
                    "iss": {"essential": True, "value": provider.issuer},
                    "aud": {"essential": True, "value": provider.client_id},
                    "nonce": {"essential": True, "value": transaction.nonce},
                },
            )
            identity = Identity.model_validate(dict(claims))
            if transaction.subject and (
                identity.iss != transaction.issuer
                or identity.sub != transaction.subject
            ):
                return await self.failure(request, "oidc.failed")
        except HTTPError, OAuthError, JoseError, ValueError, KeyError, RuntimeError:
            return await self.failure(request, "oidc.failed")
        try:
            account = await self._storage.oidc_account(
                provider.id,
                identity.iss,
                identity.sub,
                identity.name,
                identity.email,
            )
        except ValueError:
            return await self.failure(request, "oidc.default_required")
        response = RedirectResponse(HOME, status_code=302)
        set_session(
            response,
            self._sessions.issue(account),
            request=request,
            max_age=self._sessions.max_age,
        )
        response.delete_cookie(COOKIE, path="/login/oidc")
        hint = reauth.Hint(
            account_id=account.id,
            session_version=account.session_version,
            provider_id=provider.id,
            issuer=identity.iss,
            subject=identity.sub,
            expires_at_ms=now_ms() + reauth.MAX_AGE * 1000,
        )
        response.set_cookie(
            reauth.COOKIE,
            hint.sign(self._sessions.key),
            max_age=reauth.MAX_AGE,
            httponly=True,
            samesite="lax",
            secure=request.url.scheme == "https",
            path="/",
        )
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response
