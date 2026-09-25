"""The runtime processes this node started and that have not ended: when
going down runs out of time, the node ends them itself before it exits, so
none is left running without it. Each is started in a group of its own, so
what it starts in turn ends with it."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
from typing import Any

# how long a runtime asked to end gets before it is killed
END_GRACE_SECONDS = 2.0

_running: set[int] = set()


def own_group() -> dict[str, Any]:
    """What `create_subprocess_exec` takes to start a process in a group of
    its own: the terminal's Ctrl-C does not reach it, the node ends it."""

    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def started(pid: int) -> None:
    _running.add(pid)


def ended(pid: int) -> None:
    """A runtime is gone: what it started and left running goes with it, so
    nothing it began outlives it unregistered."""

    if sys.platform != "win32":
        _signal_group(pid, signal.SIGKILL)
    _running.discard(pid)


async def end_all() -> None:
    """End every runtime still running, with what it started: asked first,
    killed when it is still there after the grace."""

    pids = tuple(_running)
    if not pids:
        return
    if sys.platform == "win32":
        await asyncio.gather(*(_end_tree(pid) for pid in pids))
    else:
        for pid in pids:
            _signal_group(pid, signal.SIGTERM)
        try:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + END_GRACE_SECONDS
            while any(_group_alive(pid) for pid in pids) and loop.time() < deadline:
                await asyncio.sleep(0.05)
        finally:
            # killed even when the wait itself is cut short
            for pid in pids:
                _signal_group(pid, signal.SIGKILL)
    _running.difference_update(pids)


def _signal_group(pid: int, number: signal.Signals) -> None:
    try:
        os.killpg(pid, number)
    except ProcessLookupError:
        pass


def _group_alive(pid: int) -> bool:
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def _end_tree(pid: int) -> None:
    process = await asyncio.create_subprocess_exec(
        "taskkill",
        "/T",
        "/F",
        "/PID",
        str(pid),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await process.wait()


__all__ = ["END_GRACE_SECONDS", "end_all", "ended", "own_group", "started"]
