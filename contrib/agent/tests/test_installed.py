"""Install an independent agent consumer against public wheel and sdist APIs."""

from __future__ import annotations

lazy import asyncio
lazy import os
lazy from pathlib import Path


async def test_independent_agent_compositions_own_real_files(tmp_path: Path):
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
        root / "contrib/audit",
        root / "contrib/harness",
        root / "contrib/channel",
        root / "contrib/timer",
        root / "contrib/invoke",
        root / "contrib/storage",
        root / "contrib/schema",
        root / "contrib/sqlite",
        root / "contrib/legacy-node-migrations",
        root / "contrib/messages",
        root / "contrib/messages-node",
        root / "contrib/collaboration",
        root / "contrib/reminder",
        root / "contrib/timer-wheel",
        root / "contrib/sidecar",
        root / "contrib/sidecar-unix",
        root / "contrib/sidecar-windows",
        root / "contrib/agent",
        Path(__file__).parent / "fixtures/journal",
        Path(__file__).parent / "fixtures/workbench",
        Path(__file__).parent / "fixtures/editor",
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
        command = [
            "uv",
            "run",
            "--isolated",
            "--no-project",
            "--python",
            "3.15",
            "--with",
            "blockbuster>=1.5.29,<1.6",
        ]
        for artifact in await asyncio.to_thread(list, output.glob(suffix)):
            command.extend(["--with", str(artifact)])
        for script in ("consumer_smoke.py", "channel_smoke.py"):
            process = await asyncio.create_subprocess_exec(
                *command,
                str(Path(__file__).with_name(script)),
                cwd=tmp_path,
                env=environment,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await process.communicate()
            assert process.returncode == 0, stdout.decode()
