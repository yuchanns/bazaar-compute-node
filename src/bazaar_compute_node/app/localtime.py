from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from tzlocal import get_localzone, reload_localzone


def format_local_timestamp(timestamp_ms: int) -> str:
    if (
        isinstance(timestamp_ms, bool)
        or not isinstance(timestamp_ms, int)
        or timestamp_ms < 0
    ):
        raise ValueError("timestamp_ms must be a non-negative integer")
    return (
        datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC)
        .astimezone(get_localzone())
        .isoformat(timespec="seconds")
    )


def system_timezone_name() -> str:
    try:
        timezone = reload_localzone()
        timezone_name = timezone.key
        canonical = ZoneInfo(timezone_name)
    except (AttributeError, ZoneInfoNotFoundError, ValueError, OSError) as error:
        raise ValueError(
            "system timezone could not be resolved to an IANA timezone"
        ) from error
    return canonical.key


__all__ = ["format_local_timestamp", "system_timezone_name"]
