from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

from bazaar_compute_node.core.utils.command import platform_environment, said_by


@pytest.mark.asyncio
async def test_a_command_that_hangs_is_killed_not_left_behind(tmp_path: Path) -> None:
    """Asking a command about itself gives up in time, and the command it
    gave up on is gone rather than left running."""

    pid_file = tmp_path / "pid"
    script = tmp_path / "hang.py"
    script.write_text(
        f"import os, time\nopen({str(pid_file)!r}, 'w').write(str(os.getpid()))\n"
        "time.sleep(60)\n"
    )

    said = await said_by(
        sys.executable, str(script), environment=platform_environment(), timeout=1
    )

    assert said is None
    pid = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.asyncio
async def test_a_command_whose_asking_is_cancelled_is_killed(tmp_path: Path) -> None:
    """The node going down while a command is asked about itself takes the
    command down with it."""

    pid_file = tmp_path / "pid"
    script = tmp_path / "hang.py"
    script.write_text(
        f"import os, time\nopen({str(pid_file)!r}, 'w').write(str(os.getpid()))\n"
        "time.sleep(60)\n"
    )
    asking = asyncio.create_task(
        said_by(
            sys.executable, str(script), environment=platform_environment(), timeout=60
        )
    )
    async with asyncio.timeout(10):
        while not pid_file.exists() or not pid_file.read_text():
            await asyncio.sleep(0.02)
    asking.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asking
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)


@pytest.mark.asyncio
async def test_a_probe_gets_the_platform_and_its_own_names_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The node's environment holds every agent's credentials; a process
    started to ask a runtime about itself sees the platform's variables and
    the runtime's own, and none of those."""

    monkeypatch.setenv("BCN_AGENT_TELEGRAM_TOKEN", "secret")
    monkeypatch.setenv("CODEX_HOME", "/srv/codex")
    script = tmp_path / "env.py"
    script.write_text(
        "import os\nprint(','.join(sorted(n for n in os.environ if n in "
        "('BCN_AGENT_TELEGRAM_TOKEN', 'CODEX_HOME', 'PATH'))))\n"
    )

    said = await said_by(
        sys.executable,
        str(script),
        environment=platform_environment(("CODEX_HOME",)),
        timeout=10,
    )

    assert said == "CODEX_HOME,PATH"
