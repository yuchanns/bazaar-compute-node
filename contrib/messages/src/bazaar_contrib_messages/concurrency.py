from __future__ import annotations

import asyncio
from typing import Protocol

from .actor import Thread


class ThreadConcurrency(Protocol):
    """Share the lock for a conversation's cursor, turn and outbound checks."""

    def for_thread(self, actor: Thread) -> asyncio.Lock: ...
