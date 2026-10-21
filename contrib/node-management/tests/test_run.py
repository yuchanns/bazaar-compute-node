"""Exercise configured run commands and graceful exit in real node processes."""

from __future__ import annotations

lazy import asyncio
lazy import os
lazy import signal
lazy import sys
lazy from pathlib import Path

import pytest
from bazaar_contrib_config import ConfigDocument, ContribPackage, EnabledContrib
from bazaar_contrib_config_filesystem import FileConfigStore


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process signals")
@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM])
async def test_run_uses_selected_configuration_and_exits(tmp_path: Path, sig: int):
    log = tmp_path / "selected-logs" / "node.jsonl"
    configured = tmp_path / (
        "selected.toml" if sig == signal.SIGTERM else "options with spaces.toml"
    )
    payload = ConfigDocument(
        contribs=(
            ContribPackage(
                enabled=(
                    EnabledContrib(entry="commands"),
                    EnabledContrib(entry="node-management"),
                    EnabledContrib(entry="logging", config={"path": str(log)}),
                )
            ),
        ),
    ).model_dump(mode="json")
    async with FileConfigStore(configured) as store:
        await store.write(payload, expected_revision=None)
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "bazaar_compute_node",
        "--configer-options",
        str(tmp_path / ("ignored.toml" if sig == signal.SIGTERM else configured.name)),
        *(["--config", str(configured)] if sig == signal.SIGTERM else []),
        "run",
        cwd=tmp_path,
        env=dict(os.environ) | {"BCN_HOME": str(tmp_path / "node")},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        assert process.stdout is not None
        ready = await process.stdout.readline()
        if not ready:
            _, errors = await process.communicate()
            raise AssertionError(errors.decode())
        assert "bcn ready" in ready.decode()
        process.send_signal(sig)
        _, errors = await process.communicate()
        if sig == signal.SIGINT:
            assert process.returncode != 0, errors.decode()
        else:
            assert process.returncode == 0, errors.decode()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    assert await asyncio.to_thread(log.parent.is_dir)
