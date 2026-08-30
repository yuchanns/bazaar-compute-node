"""Install a newer bcn release and hand this node over to it."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
from collections.abc import Awaitable, Callable, Mapping
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

_STAGING_DIRECTORY = f"{__distribution__}.staging"
_REPLACE_MANAGED_FILE = TextTemplate.from_resource(
    "system_service/replace_managed_file.ps1"
)


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


def _run(command: list[str], *, env: Mapping[str, str] | None = None) -> None:
    # no deadline: uv already gives up on a request that never answers, and a
    # second limit would only cut short an install that was merely slow
    result = subprocess.run(
        command,
        capture_output=True,
        check=False,
        text=True,
        errors="replace",
        env=None if env is None else {**os.environ, **env},
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
        # the launcher is the only thing that can swap the staged release into
        # place, so without one the install would succeed and change nothing
        raise UpgradeError(
            "this node is not installed as a system service, so nothing can put "
            "the new release in place; run `bcn system-service install` first"
        )
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
    # beside them and the launcher swaps the two before bcn starts again. It is
    # the same `uv tool install` a user would run, pointed at a directory of our
    # own, so uv chooses the interpreter and lays the tool out as it always does
    tool_directory = _upgrade_target_path().parent
    tool_directory.mkdir(parents=True, exist_ok=True)
    staging = tool_directory / _STAGING_DIRECTORY
    if staging.exists():
        shutil.rmtree(staging)
    build = Path(tempfile.mkdtemp(prefix=f"{__distribution__}-", dir=tool_directory))
    try:
        _run(
            [
                _uv_executable(),
                "tool",
                "install",
                "--force",
                f"{__distribution__}=={version}",
            ],
            env={
                "UV_TOOL_DIR": str(build),
                # the entry points belong to the live installation, whose
                # trampoline already points at the directory this replaces
                "UV_TOOL_BIN_DIR": str(build / "bin"),
            },
        )
        (build / __distribution__).rename(staging)
    except BaseException:
        shutil.rmtree(build, ignore_errors=True)
        raise
    shutil.rmtree(build, ignore_errors=True)
    # the swap keeps the replaced release as a rollback point, and only a node
    # that came up as this version proves it is no longer needed
    try:
        _upgrade_target_path().write_text(version, encoding="utf-8")
    except OSError:
        # a staged release with no marker would be swapped in by some later
        # restart nobody connected to this upgrade, and nothing would then know
        # to clean up after it
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _upgrade_target_path() -> Path:
    live = windows_live_directory()
    if live is None:
        raise UpgradeError("cannot resolve the uv tool directory; APPDATA is not set")
    return live.with_name(f"{__distribution__}.upgrade-target")


def discard_replaced_release(installed_version: str) -> None:
    """Drop the rollback copy once this process proves the swap worked.

    A copy that cannot be read or removed — a file another process still holds,
    say — is not a reason to refuse to start. The next start tries again.
    """

    try:
        target = _upgrade_target_path()
    except UpgradeError:
        # nowhere a swap could have been staged, so nothing to drop
        return
    try:
        if not target.exists():
            return
        if target.read_text(encoding="utf-8").strip() != installed_version:
            # the swap did not happen, so the replaced release is the way back
            return
        previous = target.with_name(f"{__distribution__}.old")
        shutil.rmtree(previous, ignore_errors=True)
        if previous.exists():
            # something still holds part of it; the marker is what brings a
            # later start back here to try again
            return
        target.unlink(missing_ok=True)
    except OSError:
        logging.getLogger("bazaar_compute_node.application.upgrade").warning(
            "the replaced release could not be discarded", exc_info=True
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
        if os.name == "nt":
            await asyncio.to_thread(_refresh_windows_wrapper)
            await asyncio.to_thread(_install_windows, version)
        else:
            await asyncio.to_thread(_install_posix, version)


__all__ = [
    "UpgradeError",
    "UpgradeService",
    "UpgradeUnavailable",
    "discard_replaced_release",
]
