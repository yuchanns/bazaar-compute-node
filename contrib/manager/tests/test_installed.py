"""Install real releases and exercise the same workflow in venv and uv tool."""

from __future__ import annotations

lazy import asyncio
lazy import json
lazy import os
lazy import shutil
lazy import subprocess
lazy import threading
lazy import tomllib
lazy from functools import partial
lazy from hashlib import sha256
lazy from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
lazy from pathlib import Path

import pytest
lazy import tomli_w
lazy from packaging.version import Version


class PackageRegistry(SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        if self.path.endswith(".whl"):
            self.send_header("Cache-Control", "public, max-age=365000000, immutable")
        elif "/simple/" in self.path:
            self.send_header("Cache-Control", "public, max-age=600")
        super().end_headers()


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
                "agent",
                "agent-command",
                "audit",
                "channel",
                "timer",
                "harness",
                "status",
                "config",
                "config-versions",
                "manager",
                "invoke",
                "storage",
                "schema",
                "legacy-node-migrations",
                "messages",
                "messages-node",
                "collaboration",
                "reminder",
                "node-management",
                "sidecar",
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
    (source / "contrib" / "manager" / "catalog.uv.lock").unlink()
    shutil.copy(source / "uv.lock", source / "contrib" / "manager" / "catalog.uv.lock")
    output = path / "registry" / "one" / "files"
    current = Version(
        tomllib.loads((source / "app" / "pyproject.toml").read_text())["project"][
            "version"
        ]
    )
    versions = {
        "current": str(current),
        "target": f"{current.major}.{current.minor}.{current.micro + 1}",
    }
    (path / "versions.json").write_text(json.dumps(versions))
    for version in versions.values():
        for member in members:
            project = source / member / "pyproject.toml"
            document = tomllib.loads(project.read_text())
            document["project"]["version"] = version
            project.write_text(tomli_w.dumps(document))
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
                "\n".join(
                    f'<a href="../../files/{file}#sha256={sha256((registry / "files" / file).read_bytes()).hexdigest()}">{file}</a>'
                    for file in files
                    if name != "bazaar-compute-node"
                    or f"-{versions['current']}-" in file
                )
            )
    return path


@pytest.mark.parametrize("deployment", ["venv", "tool"])
@pytest.mark.parametrize("workflow", ["installed_smoke.py", "live_smoke.py"])
async def test_real_installed_manager(
    wheels: Path, tmp_path: Path, deployment: str, workflow: str
):
    def setup():
        registry = tmp_path / "registry"
        shutil.copytree(wheels / "registry", registry)
        handler = partial(PackageRegistry, directory=str(registry))
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        source = f"http://127.0.0.1:{server.server_port}/one/simple/"
        other = f"http://127.0.0.1:{server.server_port}/two/simple/"
        (registry / "one" / "catalog.json").write_text(
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
            "UV_CACHE_DIR": str(tmp_path / "cache"),
        }
        versions = json.loads((wheels / "versions.json").read_text())
        if deployment == "tool":
            command = [
                "uv",
                "tool",
                "install",
                "--python",
                "3.15",
                "--index",
                source,
                "--prerelease",
                "allow",
                "--with",
                "blockbuster>=1.5.29,<1.6",
                f"bazaar-compute-node<{versions['target']}",
            ]
            prefix = tmp_path / "tools" / "bazaar-compute-node"
        else:
            prefix = tmp_path / "environment"
            subprocess.run(
                ["uv", "venv", "--python", "3.15", str(prefix)],
                check=True,
            )
            command = [
                "uv",
                "pip",
                "install",
                "--python",
                str(prefix),
                "--index",
                source,
                "--prerelease",
                "allow",
                "blockbuster>=1.5.29,<1.6",
                f"bazaar-compute-node<{versions['target']}",
            ]
        try:
            subprocess.run(command, env=environment, check=True)
        except BaseException:
            server.shutdown()
            server.server_close()
            thread.join()
            raise
        python = subprocess.run(
            ["uv", "python", "find", "--no-project", str(prefix)],
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        return python, environment, source, other, server, thread

    python, environment, source, other, server, thread = await asyncio.to_thread(setup)
    process = await asyncio.create_subprocess_exec(
        str(python),
        str(Path(__file__).with_name(workflow)),
        str(tmp_path),
        source,
        other,
        str(wheels),
        env=environment,
        cwd=tmp_path,
    )
    try:
        assert await process.wait() == 0
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
        await asyncio.to_thread(server.shutdown)
        await asyncio.to_thread(server.server_close)
        await asyncio.to_thread(thread.join)
