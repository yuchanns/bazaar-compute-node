"""Who is logged in: a signed cookie that names the account, when it was
issued, and the account session version it was issued under."""

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
_KEY_BYTES = 32


@dataclass(frozen=True, slots=True)
class Claim:
    """What a cookie says, once its signature has been checked."""

    account_id: str
    issued_at_ms: int
    session_version: int

    def matches(self, account: Account) -> bool:
        return self.session_version == account.session_version


class Sessions:
    """Issues and reads session cookies with a key that arrives when the
    server starts, after the data directory is known to exist."""

    def __init__(self, max_age: int = 600) -> None:
        self.max_age = max_age
        self.key = b""

    def issue(self, account: Account, *, max_age: int | None = None) -> str:
        issued = now_ms()
        expires = issued + (self.max_age if max_age is None else max_age) * 1000
        body = f"{account.id}:{issued}:{account.session_version}:{expires}"
        return f"{body}:{self._sign(body)}"

    def read(self, cookie: str | None) -> Claim | None:
        """The claim in a cookie, or nothing when it is forged or expired."""

        if not cookie:
            return None
        body, _, signature = cookie.rpartition(":")
        if not body or not hmac.compare_digest(self._sign(body), signature):
            return None
        parts = body.split(":")
        if len(parts) != 4:
            return None
        account_id, issued, mark, expires = parts
        if (
            not mark.isdigit()
            or not issued.isdigit()
            or not expires.isdigit()
            or now_ms() >= int(expires)
        ):
            return None
        return Claim(
            account_id=account_id, issued_at_ms=int(issued), session_version=int(mark)
        )

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
    "Claim",
    "Sessions",
    "load_session_key",
]
