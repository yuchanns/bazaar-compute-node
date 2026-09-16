from __future__ import annotations

from time import time_ns


def now_ms() -> int:
    return time_ns() // 1_000_000


__all__ = ["now_ms"]
