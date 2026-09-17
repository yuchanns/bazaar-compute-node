"""The server's clock, and moments as the viewer reads them."""

from __future__ import annotations

from datetime import UTC, datetime, tzinfo
from time import time_ns
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# where the server sits: the zone a viewer gets when it did not say its own
LOCAL: tzinfo = datetime.now().astimezone().tzinfo or UTC


def now_ms() -> int:
    return time_ns() // 1_000_000


def zone(name: str | None) -> tzinfo:
    """The viewer's zone by IANA name, or the server's when there is none."""

    if name:
        try:
            return ZoneInfo(name)
        except ZoneInfoNotFoundError, ValueError:
            pass
    return LOCAL


def start_of_today_ms(tz: tzinfo) -> int:
    today = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(today.timestamp() * 1000)


def clock_text(at_ms: int, tz: tzinfo) -> str:
    return datetime.fromtimestamp(at_ms / 1000, tz).strftime("%H:%M:%S")


__all__ = ["LOCAL", "clock_text", "now_ms", "start_of_today_ms", "zone"]
