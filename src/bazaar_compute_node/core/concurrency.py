from __future__ import annotations

import asyncio
from typing import Protocol


class IThreadConcurrency(Protocol):
    """Return the shared lock for one thread.

    Command, cursor, turn, and outbound fresh-check operations for the same
    thread must use the same lock. Locks for different threads are
    independent and must not be acquired by a global node lock.
    """

    def for_thread(self, thread_id: str) -> asyncio.Lock:
        """Return a stable lock keyed by the opaque thread id."""
        ...


class ThreadLockRegistry:
    """In-memory per-thread lock registry for one running node process."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    def for_thread(self, thread_id: str) -> asyncio.Lock:
        if not isinstance(thread_id, str) or not thread_id:
            raise ValueError("thread_id must be a non-empty string")
        return self._locks.setdefault(thread_id, asyncio.Lock())
