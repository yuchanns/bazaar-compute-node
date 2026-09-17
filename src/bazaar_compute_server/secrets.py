"""Password and secret hashing."""

from __future__ import annotations

import asyncio
import base64
import hmac
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

_N = 2**15
_R = 8
_P = 1
_LENGTH = 32
# a derivation takes a core and 128·n·r bytes (32 MiB) for as long as it runs;
# one per core is all the machine can do at once, the rest wait their turn
# instead of piling up in memory, however many logins arrive together
_DERIVING = ThreadPoolExecutor(
    max_workers=os.process_cpu_count() or 1, thread_name_prefix="bcs-scrypt"
)


async def derive[T](operation: Callable[..., T], *args: object) -> T:
    """Run one of the derivations below off the loop, in its turn."""

    return await asyncio.get_running_loop().run_in_executor(_DERIVING, operation, *args)


def hash_secret(secret: str) -> str:
    salt = os.urandom(16)
    digest = Scrypt(salt=salt, length=_LENGTH, n=_N, r=_R, p=_P).derive(
        secret.encode("utf-8")
    )
    return "$".join(("scrypt", str(_N), str(_R), str(_P), _b64(salt), _b64(digest)))


def verify_secret(secret: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    candidate = Scrypt(
        salt=_unb64(salt), length=len(_unb64(digest)), n=int(n), r=int(r), p=int(p)
    ).derive(secret.encode("utf-8"))
    return hmac.compare_digest(candidate, _unb64(digest))


def new_secret(length: int = 32) -> str:
    return _b64(os.urandom(length))


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


__all__ = ["derive", "hash_secret", "new_secret", "verify_secret"]
