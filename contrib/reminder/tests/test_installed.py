"""Build and independently install Reminder with real application consumers."""

from __future__ import annotations

lazy import asyncio
lazy import os
lazy from pathlib import Path


async def test_installed_reminders_use_durable_messages_and_shared_timer(
    tmp_path: Path,
):
    root = (await asyncio.to_thread(Path(__file__).resolve)).parents[3]
    output = tmp_path / "distributions"
    environment = dict(os.environ) | {
        "BCN_HOME": str(tmp_path / "home"),
        "UV_TOOL_DIR": str(tmp_path / "tools"),
        "UV_TOOL_BIN_DIR": str(tmp_path / "bin"),
        "UV_CACHE_DIR": str(tmp_path / "cache"),
    }
    for path in (
        root / "core",
        root / "app",
        *(
            root / "contrib" / name
            for name in (
                "agent",
                "audit",
                "channel",
                "timer",
                "timer-wheel",
                "harness",
                "config",
                "config-filesystem",
                "config-versions",
                "status",
                "logging",
                "manager",
                "invoke",
                "node-management",
                "sidecar",
                "sidecar-unix",
                "sidecar-windows",
                "storage",
                "sqlite",
                "schema",
                "node-migrations",
                "messages",
                "messages-node",
                "collaboration",
                "reminder",
            )
        ),
        root / "contrib/agent/tests/fixtures/workbench",
        Path(__file__).parent / "fixtures/gateway",
    ):
        process = await asyncio.create_subprocess_exec(
            "uv",
            "build",
            str(path),
            "--no-sources",
            "--out-dir",
            str(output),
            cwd=tmp_path,
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await process.communicate()
        assert process.returncode == 0, stdout.decode()
    for suffix in ("*.whl", "*.tar.gz"):
        command = ["uv", "run", "--isolated", "--no-project", "--python", "3.15"]
        for artifact in await asyncio.to_thread(list, output.glob(suffix)):
            command.extend(["--with", str(artifact)])
        command.append(str(Path(__file__).with_name("installed_smoke.py")))
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=tmp_path,
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await process.communicate()
        assert process.returncode == 0, stdout.decode()
