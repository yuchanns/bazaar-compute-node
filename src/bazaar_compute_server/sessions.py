"""Who is logged in: a signed cookie that names the account, when it was
issued, and which password it was issued under."""

from __future__ import annotations

import asyncio
import hmac
import os
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from .clock import now_ms
from .config import write_private
from .storage import Account

COOKIE = "bcs_session"
# how long a login lasts before the person is asked again
SESSION_DAYS = 30
_SESSION_MS = SESSION_DAYS * 24 * 60 * 60 * 1000
_KEY_BYTES = 32
# how much of the password hash a session remembers: enough to tell one
# password from the next, too little to help anyone guess it
_FINGERPRINT = 8


@dataclass(frozen=True, slots=True)
class Claim:
    """What a cookie says, once its signature has been checked."""

    account_id: str
    issued_at_ms: int
    fingerprint: str

    def matches(self, account: Account) -> bool:
        return hmac.compare_digest(self.fingerprint, fingerprint(account))


def fingerprint(account: Account) -> str:
    """The part of the hash a session is bound to: change the password and
    every session issued before it stops matching."""

    return account.password_hash.rsplit("$", 1)[-1][:_FINGERPRINT]


class Sessions:
    """Issues and reads session cookies with a key that arrives when the
    server starts, after the data directory is known to exist."""

    def __init__(self) -> None:
        self.key = b""

    def issue(self, account: Account) -> str:
        body = f"{account.id}:{now_ms()}:{fingerprint(account)}"
        return f"{body}:{self._sign(body)}"

    def read(self, cookie: str | None) -> Claim | None:
        """The claim in a cookie, or nothing when it is forged or expired."""

        if not cookie:
            return None
        body, _, signature = cookie.rpartition(":")
        if not body or not hmac.compare_digest(self._sign(body), signature):
            return None
        parts = body.split(":")
        if len(parts) != 3:
            return None
        account_id, issued, mark = parts
        if not issued.isdigit() or now_ms() - int(issued) > _SESSION_MS:
            return None
        return Claim(account_id=account_id, issued_at_ms=int(issued), fingerprint=mark)

    def _sign(self, body: str) -> str:
        if not self.key:
            raise RuntimeError("session key is not loaded")
        return hmac.new(self.key, body.encode("utf-8"), sha256).hexdigest()


async def load_session_key(data_dir: Path) -> bytes:
    """The signing key under the data directory, made on first use and
    readable by the owner alone."""

    return await asyncio.to_thread(_load_or_create, data_dir / "session.key")


def _load_or_create(path: Path) -> bytes:
    if path.exists():
        key = path.read_bytes()
        # a key of another size is not one of ours; signing with it would
        # fail quietly, so the start fails loudly instead
        if len(key) != _KEY_BYTES:
            raise RuntimeError(f"{path} does not hold a session key")
        return key
    key = os.urandom(_KEY_BYTES)
    # written whole or not at all: a start cut short leaves no half a key
    write_private(path, key)
    return key


__all__ = [
    "COOKIE",
    "SESSION_DAYS",
    "Claim",
    "Sessions",
    "fingerprint",
    "load_session_key",
]
