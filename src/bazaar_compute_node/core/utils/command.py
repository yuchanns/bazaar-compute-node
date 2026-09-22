"""Asking a command on this machine about itself."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterable, Mapping

# what any process of a runtime starts with from the node's own environment;
# the rest of it - every agent's credentials among them - it does not get
PLATFORM_ENVIRONMENT = frozenset(
    {
        "HOME",
        "LANG",
        "LC_ALL",
        "PATH",
        "TMPDIR",
        "TEMP",
        "TMP",
        "SystemRoot",
        "ComSpec",
        "PATHEXT",
        "USERPROFILE",
    }
)


def platform_environment(names: Iterable[str] = ()) -> dict[str, str]:
    """The node's own values of the platform's variables and of `names`, for
    a process started only to ask a runtime about itself."""

    return {
        name: os.environ[name]
        for name in sorted({*PLATFORM_ENVIRONMENT, *names})
        if os.environ.get(name)
    }


async def said_by(
    executable: str,
    *arguments: str,
    environment: Mapping[str, str],
    timeout: float,
) -> str | None:
    """The first line a command prints, or nothing when it will not run or
    does not finish well; one that does not finish in time, or whose asking
    is cancelled, is killed and waited for, not left behind."""

    try:
        process = await asyncio.create_subprocess_exec(
            executable,
            *arguments,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=dict(environment),
        )
    except OSError:
        return None
    try:
        async with asyncio.timeout(timeout):
            stdout, _ = await process.communicate()
    except TimeoutError:
        return None
    finally:
        # not finished in time, or the asking was cancelled: it goes too
        if process.returncode is None:
            process.kill()
            await process.wait()
    if process.returncode != 0:
        return None
    lines = stdout.decode("utf-8", "replace").strip().splitlines()
    return lines[0].strip() if lines else None


__all__ = ["PLATFORM_ENVIRONMENT", "platform_environment", "said_by"]
