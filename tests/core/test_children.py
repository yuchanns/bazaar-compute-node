from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

from bazaar_compute_node.contrib.claude.process import ProcessSpec, ProcessSupervisor
from bazaar_compute_node.contrib.codex.process import (
    JsonlProcessSpec,
    JsonlProcessSupervisor,
)
from bazaar_compute_node.core.utils import children

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX process groups; Windows uses taskkill"
)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def _pid_in(path: Path) -> int:
    async with asyncio.timeout(5):
        while not path.exists() or not path.read_text().strip():
            await asyncio.sleep(0.01)
    return int(path.read_text())


@pytest.mark.asyncio
async def test_runtimes_left_running_are_ended_with_what_they_started(
    tmp_path: Path,
) -> None:
    """What a node could not stop in time is ended before it exits: asked
    first, killed when it will not go, and whatever it started goes with it."""

    environment = {"PATH": os.environ["PATH"]}
    # one that goes when asked, and leaves a process of its own behind it
    polite = ProcessSupervisor(
        ProcessSpec(
            executable="/bin/sh",
            arguments=("-c", f"sleep 60 & echo $! > {tmp_path / 'polite'}; wait"),
            cwd=tmp_path,
            environment=environment,
        )
    )
    # one that will not go when asked, nor will what it started
    stubborn = JsonlProcessSupervisor(
        JsonlProcessSpec(
            executable="/bin/sh",
            arguments=(
                "-c",
                f"trap '' TERM; sleep 60 & echo $! > {tmp_path / 'stubborn'}; wait",
            ),
            cwd=tmp_path,
            environment=environment,
        )
    )
    await polite.start(timeout=5)
    await stubborn.start(timeout=5)
    started = (await _pid_in(tmp_path / "polite"), await _pid_in(tmp_path / "stubborn"))
    assert all(_alive(pid) for pid in started)

    async with asyncio.timeout(children.END_GRACE_SECONDS + 5):
        await children.end_all()

    async with asyncio.timeout(5):
        while any(_alive(pid) for pid in started):
            await asyncio.sleep(0.01)
        while polite.is_running or stubborn.returncode is None:
            await asyncio.sleep(0.01)
    await polite.stop(timeout=1)
    await stubborn.stop(timeout=1)


@pytest.mark.asyncio
async def test_runtimes_that_go_when_asked_are_not_waited_on(tmp_path: Path) -> None:
    """The grace is a limit, not a wait: once every runtime asked to end is
    gone, the node goes on."""

    polite = ProcessSupervisor(
        ProcessSpec(
            executable="/bin/sh",
            arguments=("-c", "sleep 60"),
            cwd=tmp_path,
            environment={"PATH": os.environ["PATH"]},
        )
    )
    await polite.start(timeout=5)
    loop = asyncio.get_running_loop()
    began = loop.time()
    await children.end_all()
    assert loop.time() - began < children.END_GRACE_SECONDS
    async with asyncio.timeout(5):
        while polite.is_running:
            await asyncio.sleep(0.01)
    await polite.stop(timeout=1)


@pytest.mark.asyncio
async def test_what_a_runtime_leaves_behind_goes_when_it_does(tmp_path: Path) -> None:
    """A runtime that ends on its own while something it started is still
    running takes that with it."""

    runtime = JsonlProcessSupervisor(
        JsonlProcessSpec(
            executable="/bin/sh",
            arguments=("-c", f"sleep 60 & echo $! > {tmp_path / 'left'}; exit 0"),
            cwd=tmp_path,
            environment={"PATH": os.environ["PATH"]},
        )
    )
    await runtime.start(timeout=5)
    left = await _pid_in(tmp_path / "left")
    async with asyncio.timeout(5):
        while runtime.returncode is None or _alive(left):
            await asyncio.sleep(0.01)
    await runtime.stop(timeout=1)
