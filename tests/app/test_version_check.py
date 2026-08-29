from __future__ import annotations

import pytest

from bazaar_compute_node.app.version_check import VersionWatcher
from bazaar_compute_node.core.timerwheel import TimerWheel


def _watcher(wheel: TimerWheel) -> VersionWatcher:
    return VersionWatcher(
        timer_wheel=wheel,
        current_version="0.0.1",
        request_timeout_seconds=1,
    )


@pytest.mark.asyncio
async def test_version_watcher_reports_nothing_before_it_has_looked() -> None:
    wheel = TimerWheel()
    watcher = _watcher(wheel)

    # case: a watcher that has not run yet offers no upgrade
    assert watcher.available_version() is None

    # case: stopping one that never started is a no-op
    await watcher.stop(timeout=1)
    assert watcher.available_version() is None
