"""Watch PyPI for a newer release of this package."""

from __future__ import annotations

import asyncio
import logging

import aiohttp
from packaging.version import Version

from .. import __distribution__
from ..core.timerwheel import TimerWheel, TimerWheelClosedError

_CHECK_INTERVAL_MS = 3_600_000
_RELEASE_URL = f"https://pypi.org/pypi/{__distribution__}/json"


class VersionWatcher:
    """Poll PyPI so an Agent can offer the upgrade while it talks to the user."""

    def __init__(
        self,
        *,
        timer_wheel: TimerWheel,
        current_version: str,
        request_timeout_seconds: float,
    ) -> None:
        self._timer_wheel = timer_wheel
        self._current_version = current_version
        self._request_timeout_seconds = request_timeout_seconds
        self._available: str | None = None
        self._task: asyncio.Task[None] | None = None
        self._logger = logging.getLogger("bazaar_compute_node.application.version")

    def available_version(self) -> str | None:
        """Return the newer release seen by the last check, if there was one."""

        return self._available

    async def start(self, *, timeout: float) -> None:
        del timeout
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="bcn-version-watcher")

    async def stop(self, *, timeout: float) -> None:
        del timeout
        task = self._task
        if task is None:
            return
        self._task = None
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def _run(self) -> None:
        while True:
            await self._check()
            try:
                await self._timer_wheel.create(_CHECK_INTERVAL_MS).wait()
            except TimerWheelClosedError:
                return

    async def _check(self) -> None:
        try:
            session_timeout = aiohttp.ClientTimeout(
                total=self._request_timeout_seconds,
            )
            async with (
                aiohttp.ClientSession(timeout=session_timeout) as session,
                session.get(_RELEASE_URL) as response,
            ):
                response.raise_for_status()
                payload = await response.json()
            latest = payload["info"]["version"]
            # a withdrawn release makes info.version smaller again, so the
            # comparison decides both directions rather than latching
            self._available = (
                latest if Version(latest) > Version(self._current_version) else None
            )
        except asyncio.CancelledError:
            raise
        except aiohttp.ClientError, TimeoutError, ValueError, KeyError, TypeError:
            self._logger.warning("version check failed", exc_info=True)


__all__ = ["VersionWatcher"]
