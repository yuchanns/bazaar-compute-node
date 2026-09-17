"""Accounts: the one that exists before anyone logs in."""

from __future__ import annotations

from .secrets import new_secret
from .storage import Account, IStorage

# root is the account made at first start, the only one there is; roles
# and other accounts come later
ADMIN = "admin"
# base64url of this many bytes: long enough that printing it once is safe
_PASSWORD_BYTES = 24


async def ensure_admin(storage: IStorage) -> tuple[Account, str | None]:
    """The root account, and the password it was just given if it is new;
    that password is shown once and never kept in the clear."""

    existing = await storage.find_account(ADMIN)
    if existing is not None:
        return existing, None
    password = new_secret(_PASSWORD_BYTES)
    account = await storage.add_account(ADMIN, password)
    return account, password


__all__ = ["ADMIN", "ensure_admin"]
