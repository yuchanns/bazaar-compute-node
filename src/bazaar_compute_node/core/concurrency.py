from __future__ import annotations

import asyncio
from typing import Protocol


class ISessionConcurrency(Protocol):
    """Return the shared lock for one bcn session.

    Command, cursor, turn, and outbound fresh-check operations for the same
    session must use the same lock. Locks for different sessions are
    independent and must not be acquired by a global node lock.
    """

    def for_session(self, bcn_session_id: str) -> asyncio.Lock:
        """Return a stable lock keyed by the opaque bcn session id."""
        ...


class SessionLockRegistry:
    """In-memory per-session lock registry for one running node process."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    def for_session(self, bcn_session_id: str) -> asyncio.Lock:
        if not isinstance(bcn_session_id, str) or not bcn_session_id:
            raise ValueError("bcn_session_id must be a non-empty string")
        return self._locks.setdefault(bcn_session_id, asyncio.Lock())
