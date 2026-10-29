"""Exercise configured run commands and graceful exit in real node processes."""

from __future__ import annotations

lazy import asyncio
lazy import os
lazy import signal
lazy import sys
lazy import tomllib
lazy from importlib.resources import files
lazy from pathlib import Path

import pytest
from bazaar_contrib_config_filesystem import FileConfigStore


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process signals")
@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM])
async def test_run_uses_selected_configuration_and_exits(tmp_path: Path, sig: int):
    log = tmp_path / "selected-logs" / "node.jsonl"
    configured = tmp_path / (
        "selected.toml" if sig == signal.SIGTERM else "options with spaces.toml"
    )
    payload = tomllib.loads(
        await asyncio.to_thread(
            files("bazaar_compute_node").joinpath("defaults.toml").read_text
        )
    )
    for package in payload["contribs"]:
        for entry in package["enabled"]:
            if entry["entry"] == "logging":
                entry["config"] = {"path": str(log)}
    async with FileConfigStore(configured) as store:
        await store.write(payload, expected_revision=None)
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "bazaar_compute_node",
        "--configer-options",
        str(tmp_path / ("ignored.toml" if sig == signal.SIGTERM else configured.name)),
        "run",
        # 0.2's supervisor names the configuration after the command.
        *(["--config", str(configured)] if sig == signal.SIGTERM else []),
        cwd=tmp_path,
        env=dict(os.environ) | {"BCN_HOME": str(tmp_path / "node")},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        assert process.stdout is not None
        report = b""
        while b"bcn ready" not in report:
            line = await process.stdout.readline()
            if not line:
                _, errors = await process.communicate()
                raise AssertionError(errors.decode())
            report += line
        assert b"node-management" in report and b"running" in report
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
