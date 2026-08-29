"""Install a newer bcn release and hand this node over to it."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from packaging.version import InvalidVersion, Version

from .. import __distribution__
from ..rendering import TextTemplate
from .system_service import (
    TEMPLATE_REVISION,
    _powershell_literal,
    installed_template_revision,
    render_windows_wrapper,
    windows_live_directory,
    windows_wrapper_path,
)
from .version_check import VersionWatcher

_STAGING_DIRECTORY = f"{__distribution__}.staging"
_REPLACE_MANAGED_FILE = TextTemplate.from_resource(
    "system_service/replace_managed_file.ps1"
)


class UpgradeError(RuntimeError):
    """Raised when the node cannot install or hand over to the new release."""


def _uv_executable() -> str:
    executable = shutil.which("uv")
    if executable is None:
        raise UpgradeError(
            "cannot resolve the uv executable; bcn was not installed through uv"
        )
    return executable


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


def _refresh_windows_wrapper() -> None:
    """Bring the installed launcher up to what this release expects of it.

    The swap happens in the launcher, so a node whose launcher predates it would
    install a release that never gets swapped in. Rewriting is a precondition of
    the upgrade rather than part of it.
    """

    wrapper = windows_wrapper_path()
    if not wrapper.exists():
        # nothing hosts this node, so nothing has to be swapped for it either
        return
    content = wrapper.read_text(encoding="utf-8")
    revision = installed_template_revision(content)
    if revision is not None and revision >= TEMPLATE_REVISION:
        return
    literals = _wrapper_literals(content)
    rendered = render_windows_wrapper(**literals)
    _run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            _REPLACE_MANAGED_FILE.render(
                {
                    "target": _powershell_literal(wrapper),
                    "content": _powershell_literal(rendered),
                }
            ),
        ]
    )


def _wrapper_literals(content: str) -> dict[str, str]:
    """Recover the values the installed launcher was rendered with.

    The launcher being replaced was written by an older release, so there is no
    record of the arguments it was installed with other than the launcher
    itself. Its own `$environment_script` in particular is where a node's
    secrets come from, and re-rendering without it would leave the node unable
    to start.
    """

    wanted = {
        "executable": "$executable",
        "config_path": "$configPath",
        "environment_script": "$environmentScript",
        "log_path": "$logPath",
    }
    literals: dict[str, str] = {}
    for line in content.splitlines():
        for name, variable in wanted.items():
            prefix = f"{variable} = "
            if line.startswith(prefix):
                literals[name] = line[len(prefix) :].strip()
    missing = sorted(set(wanted) - set(literals))
    if missing:
        raise UpgradeError(
            f"the installed launcher cannot be read; it is missing {', '.join(missing)}"
        )
    return literals


def _install_windows(version: str) -> None:
    # the running node holds its own files open, so the new release is installed
    # beside them and the launcher swaps the two before bcn starts again
    tool_directory = _upgrade_target_path().parent
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
    # the swap keeps the replaced release as a rollback point, and only a node
    # that came up as this version proves it is no longer needed
    _upgrade_target_path().write_text(version, encoding="utf-8")


def _upgrade_target_path() -> Path:
    live = windows_live_directory()
    if live is None:
        raise UpgradeError("cannot resolve the uv tool directory; APPDATA is not set")
    return live.with_name(f"{__distribution__}.upgrade-target")


def discard_replaced_release(installed_version: str) -> None:
    """Drop the rollback copy once this process proves the swap worked."""

    try:
        target = _upgrade_target_path()
    except UpgradeError:
        # nowhere a swap could have been staged, so nothing to drop
        return
    if not target.exists():
        return
    if target.read_text(encoding="utf-8").strip() != installed_version:
        # the swap did not happen, so the release it replaced is still the way back
        return
    shutil.rmtree(
        target.with_name(f"{__distribution__}.old"),
        ignore_errors=True,
    )
    target.unlink(missing_ok=True)


class UpgradeService:
    """Install the release the user agreed to, then hand the node over to it."""

    def __init__(
        self,
        *,
        version_watcher: VersionWatcher,
        installed_version: str,
        request_restart: Callable[[], None],
    ) -> None:
        self._version_watcher = version_watcher
        self._installed_version = installed_version
        self._request_restart = request_restart

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
            await asyncio.to_thread(_refresh_windows_wrapper)
            await asyncio.to_thread(_install_windows, version)
        else:
            await asyncio.to_thread(_install_posix, version)

    def restart(self) -> None:
        """Ask whatever hosts this node to start it again on the new release.

        The node cannot restart itself from the inside: on Windows the stop it
        would ask for kills the process tree it is asking from. Exiting is the
        one thing it can do that its host is already watching for.
        """

        self._request_restart()


__all__ = [
    "UpgradeError",
    "UpgradeService",
    "discard_replaced_release",
]
