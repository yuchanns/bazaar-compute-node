"""Install a newer bcn release and hand this node over to it."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from packaging.version import InvalidVersion, Version

from .. import __distribution__
from ..core.timerwheel import TimerWheel, TimerWheelClosedError
from .system_service import resolve_bcn_executable
from .version_check import VersionWatcher

# long enough for the command response to reach the waiting bcc process
_RESTART_DELAY_MS = 1_000
_STAGING_DIRECTORY = f"{__distribution__}.staging"


class UpgradeError(RuntimeError):
    """Raised when the node cannot install or hand over to the new release."""


def _uv_executable() -> str:
    executable = shutil.which("uv")
    if executable is None:
        raise UpgradeError(
            "cannot resolve the uv executable; bcn was not installed through uv"
        )
    return executable


def _windows_tool_directory() -> Path:
    configured = os.environ.get("UV_TOOL_DIR")
    if configured:
        return Path(configured).expanduser()
    app_data = os.environ.get("APPDATA")
    if not app_data:
        raise UpgradeError("cannot resolve the uv tool directory; APPDATA is not set")
    return Path(app_data) / "uv" / "tools"


def _run(command: list[str]) -> None:
    # no deadline: the install runs in the background with nobody waiting on it,
    # and uv already gives up on a request that never answers
    result = subprocess.run(
        command,
        capture_output=True,
        check=False,
        text=True,
        errors="replace",
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        suffix = f": {detail}" if detail else ""
        raise UpgradeError(
            f"upgrade command failed ({result.returncode}): {' '.join(command)}{suffix}"
        )


def _install_posix(version: str) -> None:
    _run(
        [
            _uv_executable(),
            "tool",
            "install",
            "--force",
            f"{__distribution__}=={version}",
        ]
    )


def _install_windows(version: str) -> None:
    # the running node holds its own files open, so the new release is installed
    # beside them and the launcher swaps the two before bcn starts again
    tool_directory = _windows_tool_directory()
    tool_directory.mkdir(parents=True, exist_ok=True)
    staging = tool_directory / _STAGING_DIRECTORY
    if staging.exists():
        shutil.rmtree(staging)
    uv = _uv_executable()
    build = Path(tempfile.mkdtemp(prefix=f"{__distribution__}-", dir=tool_directory))
    environment = build / "environment"
    try:
        _run([uv, "venv", str(environment)])
        _run(
            [
                uv,
                "pip",
                "install",
                "--python",
                str(environment),
                f"{__distribution__}=={version}",
            ]
        )
        environment.rename(staging)
    except BaseException:
        shutil.rmtree(build, ignore_errors=True)
        raise
    shutil.rmtree(build, ignore_errors=True)


class UpgradeService:
    """Install the release the user agreed to, then hand the node over to it."""

    def __init__(
        self,
        *,
        version_watcher: VersionWatcher,
        installed_version: str,
        timer_wheel: TimerWheel,
    ) -> None:
        self._version_watcher = version_watcher
        self._installed_version = installed_version
        self._timer_wheel = timer_wheel
        self._restart: asyncio.Task[None] | None = None
        self._logger = logging.getLogger("bazaar_compute_node.application.upgrade")

    @property
    def installed_version(self) -> str:
        return self._installed_version

    def available_version(self) -> str | None:
        return self._version_watcher.available_version()

    async def install(self, version: str) -> None:
        try:
            Version(version)
        except InvalidVersion as error:
            raise UpgradeError(f"{version!r} is not a release version") from error
        if os.name == "nt":
            await asyncio.to_thread(_install_windows, version)
        else:
            await asyncio.to_thread(_install_posix, version)

    def restart(self) -> None:
        """Restart the node once the caller has its answer."""

        if self._restart is not None:
            return
        self._restart = asyncio.create_task(self._restart_soon(), name="bcn-upgrade")

    async def _restart_soon(self) -> None:
        try:
            await self._timer_wheel.create(_RESTART_DELAY_MS).wait()
        except TimerWheelClosedError:
            return
        try:
            await asyncio.to_thread(self._spawn_restart)
        except Exception:
            self._logger.warning("upgrade restart failed", exc_info=True)

    def _spawn_restart(self) -> None:
        # the restart kills this process, so it runs detached: a child that
        # outlives us can still finish stopping and starting the service
        command = [str(resolve_bcn_executable()), "system-service", "restart"]
        if os.name == "nt":
            subprocess.Popen(
                command,
                creationflags=(
                    subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                ),
                close_fds=True,
            )
            return
        subprocess.Popen(
            command,
            start_new_session=True,
            close_fds=True,
        )


__all__ = ["UpgradeError", "UpgradeService"]
