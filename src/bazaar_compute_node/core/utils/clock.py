from __future__ import annotations

import asyncio
from time import time_ns


def now_ms() -> int:
    return time_ns() // 1_000_000


def remaining(deadline: float) -> float:
    return max(0.0, deadline - asyncio.get_running_loop().time())


__all__ = ["now_ms", "remaining"]
