from __future__ import annotations

import asyncio

import pytest
from packaging.version import Version

from bazaar_compute_node.app.version_check import VersionWatcher
from bazaar_compute_node.core.timerwheel import TimerWheel

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_real_pypi_reports_a_newer_release_than_an_ancient_one() -> None:
    wheel = TimerWheel()
    await wheel.start()
    watcher = VersionWatcher(
        timer_wheel=wheel,
        current_version="0.0.1",
        request_timeout_seconds=30,
    )
    try:
        await watcher.start(timeout=30)

        # case: the release PyPI reports is a version we can compare against
        async with asyncio.timeout(60):
            while watcher.available_version() is None:
                await asyncio.sleep(0.05)
        assert Version(watcher.available_version() or "") > Version("0.0.1")
    finally:
        await watcher.stop(timeout=5)
        await wheel.close()


@pytest.mark.asyncio
async def test_real_pypi_offers_no_upgrade_to_an_unreleased_version() -> None:
    wheel = TimerWheel()
    await wheel.start()
    watcher = VersionWatcher(
        timer_wheel=wheel,
        current_version="9999.0.0",
        request_timeout_seconds=30,
    )
    try:
        await watcher.start(timeout=30)
        await asyncio.sleep(3)

        # case: a version newer than anything published offers no upgrade
        assert watcher.available_version() is None
    finally:
        await watcher.stop(timeout=5)
        await wheel.close()
