"""Exercise independent schema registration from installed artifacts."""

from __future__ import annotations

lazy import asyncio
lazy import os
lazy from pathlib import Path


async def test_installed_schemas_upgrade_existing_nodes_and_create_new_databases(
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
        *(
            root / "contrib" / name
            for name in (
                "agent",
                "audit",
                "channel",
                "harness",
                "invoke",
                "timer",
                "timer-wheel",
                "sidecar",
                "storage",
                "sqlite",
                "schema",
                "legacy-node-migrations",
                "messages",
                "messages-node",
                "collaboration",
                "reminder",
            )
        ),
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
    tests = Path(__file__).with_name("test_migrations.py")
    for suffix in ("*.whl", "*.tar.gz"):
        command = ["uv", "run", "--isolated", "--no-project", "--python", "3.15"]
        for artifact in await asyncio.to_thread(list, output.glob(suffix)):
            command.extend(["--with", str(artifact)])
        command.extend(
            [
                "--with",
                "pytest",
                "--with",
                "pytest-asyncio",
                "pytest",
                "-c",
                str(root / "contrib/legacy-node-migrations/pyproject.toml"),
                "--confcutdir",
                str(tests.parent),
                "-q",
                str(tests)
                + "::test_registered_03_database_accepts_owned_reminder_versions_and_restart",
                str(tests)
                + "::test_new_database_uses_current_baseline_and_retains_new_data",
                str(tests)
                + "::test_current_messages_keep_search_in_sync_with_the_sequence_key",
            ]
        )
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=tmp_path,
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await process.communicate()
        assert process.returncode == 0, stdout.decode()
