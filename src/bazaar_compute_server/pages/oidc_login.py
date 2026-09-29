"""OIDC protocol calls with short-lived, single-use in-memory login state."""

from __future__ import annotations

from collections import OrderedDict
from hashlib import sha256
from pathlib import PurePosixPath
from secrets import token_urlsafe

from authlib.integrations.base_client import OAuthError
from authlib.integrations.starlette_client import StarletteOAuth2App
from httpx2 import HTTPError
from joserfc.errors import JoseError
from pydantic import BaseModel, Field, ValidationError, field_validator
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from yarl import URL

from .. import reauth
from ..clock import now_ms
from ..gate import set_session
from ..rendering import Renderer
from ..sessions import Sessions
from ..storage import IStorage, OIDCProvider
from .login import HOME

LIFETIME = 600
MAX_TRANSACTIONS = 1024
COOKIE = "bcs_oidc"


class Destination(BaseModel):
    return_to: str = Field(default=HOME, max_length=8192)

    @field_validator("return_to")
    @classmethod
    def local_path(cls, value: str) -> str:
        url = URL(value)
        path = PurePosixPath(url.raw_path)
        if url.scheme or url.authority or path.root != "/":
            raise ValueError("return_to must be a local absolute path")
        return str(
            url.with_path(str(path), encoded=True, keep_query=True, keep_fragment=True)
        )


class Transaction(Destination):
    expires_at_ms: int
    browser_hash: str
    provider_id: str
    nonce: str
    code_verifier: str
    redirect_uri: str
    issuer: str
    subject: str


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
    # BCS keeps login state in memory rather than the framework session adapter.
    # Authlib handles the protocol and token checks.
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
        self._transactions: OrderedDict[str, Transaction] = OrderedDict()

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
        try:
            destination = Destination.model_validate(dict(request.query_params))
        except ValidationError:
            destination = Destination()
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
        self._transactions[state] = Transaction(
            expires_at_ms=now_ms() + LIFETIME * 1000,
            browser_hash=sha256(browser.encode()).hexdigest(),
            provider_id=provider.id,
            nonce=nonce,
            code_verifier=verifier,
            redirect_uri=provider.redirect_uri,
            issuer=hint.issuer if hint else "",
            subject=hint.subject if hint else "",
            return_to=destination.return_to,
        )
        # Each entry is read only when consumed, so the oldest pending entry
        # is also the least recently used one.
        while self._transactions and (
            len(self._transactions) > MAX_TRANSACTIONS
            or next(iter(self._transactions.values())).expires_at_ms <= now_ms()
        ):
            self._transactions.popitem(last=False)
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
        transaction = self._transactions.get(values.state)
        if (
            transaction is None
            or transaction.expires_at_ms <= now_ms()
            or transaction.browser_hash
            != sha256(request.cookies.get(COOKIE, "").encode()).hexdigest()
            or request.path_params["provider_id"] != transaction.provider_id
        ):
            return await self.failure(request, "oidc.expired")
        self._transactions.pop(values.state)
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
        response = RedirectResponse(transaction.return_to, status_code=302)
        set_session(
            response,
            self._sessions.issue(account, max_age=provider.session_minutes * 60),
            request=request,
            max_age=provider.session_minutes * 60,
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
