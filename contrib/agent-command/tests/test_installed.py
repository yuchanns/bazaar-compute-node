"""Install the command plugin and exercise its real configuration and review workflows."""

from __future__ import annotations

lazy import asyncio
lazy import os
lazy from pathlib import Path

import pytest
import pytest_asyncio


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def distributions(tmp_path_factory) -> Path:
    root = (await asyncio.to_thread(Path(__file__).resolve)).parents[3]
    path = tmp_path_factory.mktemp("agent-releases")
    output = path / "files"
    environment = dict(os.environ) | {"UV_CACHE_DIR": str(path / "cache")}
    members = [root / "core", root / "app"]
    members.extend(
        project.parent
        for project in await asyncio.to_thread(
            lambda: sorted((root / "contrib").glob("*/pyproject.toml"))
        )
    )
    members.extend(
        root / "contrib/agent/tests/fixtures" / name
        for name in ("journal", "workbench")
    )
    for member in members:
        process = await asyncio.create_subprocess_exec(
            "uv",
            "build",
            str(member),
            "--no-sources",
            "--out-dir",
            str(output),
            cwd=path,
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await process.communicate()
            assert process.returncode == 0, stdout.decode()
        finally:
            if process.returncode is None:
                process.terminate()
                await process.wait()
    return output


@pytest.mark.parametrize("deployment", ("venv", "tool"))
async def test_real_agent_commands(
    distributions: Path, tmp_path: Path, deployment: str
):
    def setup():
        import subprocess
        import sys
        import threading
        from functools import partial
        from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

        from packaging.utils import parse_wheel_filename

        packages = {}
        for wheel in distributions.glob("*.whl"):
            name, _, _, _ = parse_wheel_filename(wheel.name)
            packages.setdefault(name, []).append(wheel.name)
        if deployment == "tool":
            packages["bazaar-contrib-agent-command"] = [
                next(distributions.glob("bazaar_contrib_agent_command-*.tar.gz")).name
            ]
        for name, filenames in packages.items():
            directory = distributions / "simple" / name
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "index.html").write_text(
                "\n".join(
                    f'<a href="../../{filename}">{filename}</a>'
                    for filename in filenames
                )
            )
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            partial(SimpleHTTPRequestHandler, directory=str(distributions)),
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        source = f"http://127.0.0.1:{server.server_port}/simple/"
        environment = dict(os.environ) | {
            "BCN_HOME": str(tmp_path),
            "UV_TOOL_DIR": str(tmp_path / "tools"),
            "UV_TOOL_BIN_DIR": str(tmp_path / "bin"),
            "UV_CACHE_DIR": str(tmp_path / "cache"),
        }
        requirements = [
            f"{name}==0.3.0rc1"
            for name in packages
            if name.startswith("bazaar-")
            and name not in {"bazaar-compute-node", "bazaar-contrib-agent-command"}
        ]
        if deployment == "tool":
            prefix = tmp_path / "tools" / "bazaar-compute-node"
            command = [
                "uv",
                "tool",
                "install",
                "--python",
                "3.15",
                "--index",
                source,
                "bazaar-compute-node==0.3.0rc1",
                "--with",
                "blockbuster>=1.5.29,<1.6",
            ]
            for requirement in requirements:
                command.extend(("--with", requirement))
        else:
            prefix = tmp_path / "environment"
            subprocess.run(
                ["uv", "venv", "--python", "3.15", str(prefix)],
                check=True,
                capture_output=True,
            )
            command = [
                "uv",
                "pip",
                "install",
                "--python",
                str(
                    prefix
                    / (
                        "Scripts/python.exe"
                        if sys.platform == "win32"
                        else "bin/python"
                    )
                ),
                "--index",
                source,
                "bazaar-compute-node==0.3.0rc1",
                "blockbuster>=1.5.29,<1.6",
                *requirements,
            ]
        try:
            subprocess.run(command, env=environment, check=True, capture_output=True)
        except BaseException:
            server.shutdown()
            server.server_close()
            thread.join()
            raise
        return (
            prefix
            / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python"),
            environment,
            source,
            server,
            thread,
        )

    python, environment, source, server, thread = await asyncio.to_thread(setup)
    tests = (await asyncio.to_thread(Path(__file__).resolve)).parents[2] / "agent/tests"
    process = await asyncio.create_subprocess_exec(
        str(python),
        str(Path(__file__).with_name("management_smoke.py")),
        str(tmp_path),
        source,
        str(tests),
        cwd=tmp_path,
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        output, _ = await process.communicate()
        assert process.returncode == 0, output.decode()
        assert "Agent management workflow passed" in output.decode()
    finally:
        if process.returncode is None:
            process.terminate()
            await process.wait()
        await asyncio.to_thread(server.shutdown)
        await asyncio.to_thread(server.server_close)
        await asyncio.to_thread(thread.join)
