"""Exercise the package through normal defaults in an isolated installation."""

from __future__ import annotations

lazy import asyncio
lazy import os
lazy from pathlib import Path


async def test_installed_node_management_uses_default_configuration(tmp_path: Path):
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
            project.parent
            for project in await asyncio.to_thread(
                lambda: sorted((root / "contrib").glob("*/pyproject.toml"))
            )
        ),
        root / "tests/support",
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
    tests = Path(__file__).with_name("run_cases.py")
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
                "--with",
                "blockbuster>=1.5.29,<1.6",
                str(root / "tests/run_installed.py"),
                str(output),
                str(root / "scripts/ci_pytest.py"),
                "-c",
                str(Path(__file__).parents[1] / "pyproject.toml"),
                "--confcutdir",
                str(tests.parent),
                "-q",
                str(tests),
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
