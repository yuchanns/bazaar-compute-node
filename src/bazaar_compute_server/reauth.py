"""Signed hints for starting OIDC authentication, never for granting access."""

from __future__ import annotations

import hmac
from base64 import urlsafe_b64decode, urlsafe_b64encode
from hashlib import sha256

from pydantic import BaseModel, ValidationError

from .clock import now_ms

COOKIE = "bcs_reauth"
MAX_AGE = 30 * 24 * 60 * 60


class Hint(BaseModel):
    account_id: str
    session_version: int
    provider_id: str
    issuer: str
    subject: str
    expires_at_ms: int

    def sign(self, key: bytes) -> str:
        body = urlsafe_b64encode(self.model_dump_json().encode()).decode()
        signature = hmac.new(key, b"oidc-reauth:" + body.encode(), sha256).hexdigest()
        return f"{body}:{signature}"

    @classmethod
    def read(cls, cookie: str, key: bytes) -> Hint | None:
        body, _, signature = cookie.rpartition(":")
        expected = hmac.new(key, b"oidc-reauth:" + body.encode(), sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            return None
        try:
            hint = cls.model_validate_json(urlsafe_b64decode(body))
        except ValueError, ValidationError:
            return None
        return hint if hint.expires_at_ms > now_ms() else None
