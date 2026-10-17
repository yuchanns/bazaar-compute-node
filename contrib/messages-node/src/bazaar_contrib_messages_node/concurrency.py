from __future__ import annotations

import asyncio

from bazaar_contrib_messages import Thread


class ThreadLockRegistry:
    """In-memory per-thread lock registry for one running node process."""

    def __init__(self) -> None:
        self._locks: dict[Thread, asyncio.Lock] = {}

    def for_thread(self, actor: Thread) -> asyncio.Lock:
        return self._locks.setdefault(actor, asyncio.Lock())
