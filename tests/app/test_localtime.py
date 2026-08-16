from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from bazaar_compute_node.app import localtime


def _timestamp_ms(value: str) -> int:
    return int(datetime.fromisoformat(value).timestamp() * 1000)


def test_format_local_timestamp_uses_system_zone_and_explicit_offset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        localtime,
        "get_localzone",
        lambda: ZoneInfo("Asia/Shanghai"),
    )

    assert localtime.format_local_timestamp(0) == "1970-01-01T08:00:00+08:00"


def test_format_local_timestamp_tracks_dst_offset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        localtime,
        "get_localzone",
        lambda: ZoneInfo("America/New_York"),
    )

    assert (
        localtime.format_local_timestamp(_timestamp_ms("2026-01-15T12:00:00+00:00"))
        == "2026-01-15T07:00:00-05:00"
    )
    assert (
        localtime.format_local_timestamp(_timestamp_ms("2026-07-15T12:00:00+00:00"))
        == "2026-07-15T08:00:00-04:00"
    )


def test_system_timezone_name_refreshes_and_returns_iana_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def reload_zone() -> ZoneInfo:
        nonlocal calls
        calls += 1
        return ZoneInfo("America/New_York")

    monkeypatch.setattr(localtime, "reload_localzone", reload_zone)

    assert localtime.system_timezone_name() == "America/New_York"
    assert calls == 1


def test_system_timezone_name_fails_closed_for_non_iana_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _LocalZone:
        key = "local"

    monkeypatch.setattr(localtime, "reload_localzone", _LocalZone)

    with pytest.raises(ValueError, match="IANA timezone"):
        localtime.system_timezone_name()
