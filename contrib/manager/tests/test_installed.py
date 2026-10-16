"""Install real releases and exercise the same workflow in venv and uv tool."""

from __future__ import annotations

lazy import asyncio
lazy import json
lazy import os
lazy import shutil
lazy import subprocess
lazy import sys
lazy import threading
lazy from functools import partial
lazy from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
lazy from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def wheels(tmp_path_factory):
    path = tmp_path_factory.mktemp("manager-releases")
    root = Path(__file__).resolve().parents[3]
    source = path / "source"
    source.mkdir()
    shutil.copy(root / "LICENSE", source / "LICENSE")
    shutil.copy(root / "uv.lock", source / "uv.lock")
    members = [
        "core",
        "app",
        *[
            f"contrib/{name}"
            for name in (
                "status",
                "audit",
                "logging",
                "config",
                "config-filesystem",
                "config-versions",
                "manager",
                "invoke",
                "sidecar",
                "sidecar-unix",
                "sidecar-windows",
            )
        ],
    ]
    for member in members:
        shutil.copytree(
            root / member,
            source / member,
            ignore=shutil.ignore_patterns("tests", "__pycache__"),
        )
    # The extracted sdist carries this file as ordinary source, like this build fixture.
    (source / "app" / "catalog.uv.lock").unlink()
    shutil.copy(source / "uv.lock", source / "app" / "catalog.uv.lock")
    output = path / "registry" / "one" / "files"
    for version in ("0.3.0", "0.3.1"):
        for member in members:
            project = source / member / "pyproject.toml"
            project.write_text(project.read_text().replace("0.3.0", version))
            subprocess.run(
                [
                    "uv",
                    "build",
                    "--wheel",
                    "--no-sources",
                    "--directory",
                    str(source / member),
                    "--out-dir",
                    str(output),
                ],
                check=True,
                capture_output=True,
            )
    for number, version in (("one", "0.1.0"), ("two", "0.2.0")):
        fixture = path / f"example-{number}"
        shutil.copytree(Path(__file__).parent / "fixtures" / "example", fixture)
        project = fixture / "pyproject.toml"
        project.write_text(project.read_text().replace("0.1.0", version))
        subprocess.run(
            [
                "uv",
                "build",
                "--wheel",
                "--directory",
                str(fixture),
                "--out-dir",
                str(path / "registry" / number / "files"),
            ],
            check=True,
            capture_output=True,
        )
    fixture = path / "carrier"
    shutil.copytree(Path(__file__).parent / "fixtures" / "carrier", fixture)
    subprocess.run(
        [
            "uv",
            "build",
            "--wheel",
            "--directory",
            str(fixture),
            "--out-dir",
            str(output),
        ],
        check=True,
        capture_output=True,
    )
    for number in ("one", "two"):
        registry = path / "registry" / number
        packages: dict[str, list[str]] = {}
        for wheel in (registry / "files").glob("*.whl"):
            name = wheel.name.split("-", 1)[0].replace("_", "-")
            packages.setdefault(name, []).append(wheel.name)
        for name, files in packages.items():
            directory = registry / "simple" / name
            directory.mkdir(parents=True)
            (directory / "index.html").write_text(
                "\n".join(f'<a href="../../files/{file}">{file}</a>' for file in files)
            )
    return path


@pytest.mark.parametrize("deployment", ["venv", "tool"])
async def test_real_installed_manager(wheels: Path, tmp_path: Path, deployment: str):
    def setup():
        handler = partial(SimpleHTTPRequestHandler, directory=str(wheels / "registry"))
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        source = f"http://127.0.0.1:{server.server_port}/one/simple/"
        other = f"http://127.0.0.1:{server.server_port}/two/simple/"
        (wheels / "registry" / "one" / "catalog.json").write_text(
            json.dumps(
                {
                    "contribs": [
                        {
                            "package": "bazaar-contrib-example",
                            "source": source,
                        }
                    ]
                }
            )
        )
        environment = dict(os.environ) | {
            "BCN_HOME": str(tmp_path),
            "UV_TOOL_DIR": str(tmp_path / "tools"),
            "UV_TOOL_BIN_DIR": str(tmp_path / "bin"),
        }
        if deployment == "tool":
            command = [
                "uv",
                "tool",
                "install",
                "--python",
                "3.15",
                "--index",
                source,
                "--with",
                "blockbuster>=1.5.29,<1.6",
                "bazaar-compute-node==0.3.0",
            ]
            prefix = tmp_path / "tools" / "bazaar-compute-node"
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
                "blockbuster>=1.5.29,<1.6",
                "bazaar-compute-node==0.3.0",
            ]
        try:
            subprocess.run(command, env=environment, check=True, capture_output=True)
        except BaseException:
            server.shutdown()
            server.server_close()
            thread.join()
            raise
        python = prefix / (
            "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
        )
        return python, environment, source, other, server, thread

    python, environment, source, other, server, thread = await asyncio.to_thread(setup)
    process = await asyncio.create_subprocess_exec(
        str(python),
        str(Path(__file__).with_name("installed_smoke.py")),
        str(tmp_path),
        source,
        other,
        env=environment,
        cwd=tmp_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        output, errors = await asyncio.wait_for(process.communicate(), 240)
        assert process.returncode == 0, (output + errors).decode()
        assert "workflow passed" in output.decode()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
        await asyncio.to_thread(server.shutdown)
        await asyncio.to_thread(server.server_close)
        await asyncio.to_thread(thread.join)
