"""Install a newer bcn release and hand this node over to it."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from collections.abc import Awaitable, Callable, Mapping

from packaging.version import InvalidVersion, Version

from .. import __distribution__

# uv writes UTF-8 whatever the console is set to, so a node in a non-UTF-8
# locale would otherwise report its diagnostics as mojibake
_UV_OUTPUT_ENCODING = "utf-8"


class UpgradeError(RuntimeError):
    """Raised when the node cannot install or hand over to the new release."""


class UpgradeUnavailable(UpgradeError):
    """Raised when no newer release has been announced."""


def _uv_executable() -> str:
    executable = shutil.which("uv")
    if executable is None:
        # a node started by a service manager inherits that manager's PATH,
        # which often does not include wherever uv was installed, so this says
        # nothing about whether uv exists on the machine
        raise UpgradeError(
            "uv is not on this node's PATH, so the release cannot be "
            "installed; add the directory holding uv to the PATH the node "
            "runs with, then try again"
        )
    return executable


def _run(
    command: list[str],
    *,
    env: Mapping[str, str] | None = None,
    encoding: str | None = None,
) -> None:
    # no deadline: uv already gives up on a request that never answers, and a
    # second limit would only cut short an install that was merely slow
    result = subprocess.run(
        command,
        capture_output=True,
        check=False,
        text=True,
        encoding=encoding,
        errors="replace",
        env=None if env is None else {**os.environ, **env},
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        suffix = f": {detail}" if detail else ""
        raise UpgradeError(
            f"upgrade command failed ({result.returncode}): {' '.join(command)}{suffix}"
        )


def _install(version: str) -> None:
    _run(
        [
            _uv_executable(),
            "tool",
            "install",
            "--force",
            # the release was announced by PyPI's own API minutes ago, while uv
            # resolves against a separately cached index that can still predate
            # it and would then report the version as one that does not exist
            "--refresh-package",
            __distribution__,
            f"{__distribution__}=={version}",
        ],
        encoding=_UV_OUTPUT_ENCODING,
    )


class UpgradeService:
    """Install the release the user agreed to, then hand the node over to it."""

    def __init__(
        self,
        *,
        available_version: Callable[[], str | None],
        installed_version: str,
        request_restart: Callable[[], None],
    ) -> None:
        self._available_version = available_version
        self._installed_version = installed_version
        self._request_restart = request_restart
        self._lock = asyncio.Lock()

    @property
    def installed_version(self) -> str:
        return self._installed_version

    def available_version(self) -> str | None:
        return self._available_version()

    async def upgrade(
        self,
        *,
        wake_after: Callable[[str], Awaitable[str | None]],
    ) -> tuple[str, str | None]:
        """Install the release on offer and hand the node over to it.

        The whole thing is one transaction. Two sessions can accept an offer at
        the same time, and by then the offers need not even name the same
        release; letting the second in before the first has asked to restart
        would boot a version nobody was told about, and an install cannot be
        called back once its thread is running.

        ``wake_after`` records how the Agent is prompted to check the node over
        afterwards, and returns the reminder it left, or None if it could not
        leave one.
        """

        async with self._lock:
            version = self.available_version()
            if version is None:
                raise UpgradeUnavailable(
                    "No newer release has been announced for this node."
                )
            try:
                Version(version)
            except InvalidVersion as error:
                raise UpgradeError(f"{version!r} is not a release version") from error
            await self.install(version)
            reminder_id = await wake_after(version)
            # the node cannot restart itself from the inside: on Windows the
            # stop it would ask for kills the process tree it is asking from.
            # Exiting is the one thing it can do that its host watches for.
            self._request_restart()
            return version, reminder_id

    async def install(self, version: str) -> None:
        await asyncio.to_thread(_install, version)


__all__ = [
    "UpgradeError",
    "UpgradeService",
    "UpgradeUnavailable",
]
