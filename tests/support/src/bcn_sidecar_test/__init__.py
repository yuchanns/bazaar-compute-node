"""Build real releases and install the application and consumer independently."""

from __future__ import annotations

lazy import asyncio
lazy import subprocess
lazy from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def environment(tmp_path_factory):
    directory = tmp_path_factory.mktemp("sidecar-installed")
    root = Path(__file__).resolve().parents[4]
    wheels = directory / "wheels"
    for package in (
        "bazaar-core",
        "bazaar-contrib-config",
        "bazaar-contrib-config-filesystem",
        "bazaar-contrib-config-versions",
        "bazaar-contrib-status",
        "bazaar-contrib-logging",
        "bazaar-contrib-manager",
        "bazaar-contrib-invoke",
        "bazaar-contrib-timer",
        "bazaar-contrib-timer-wheel",
        "bazaar-contrib-node-management",
        "bazaar-contrib-sidecar",
        "bazaar-contrib-sidecar-unix",
        "bazaar-contrib-sidecar-windows",
        "bazaar-compute-node",
    ):
        subprocess.run(
            ["uv", "build", "--wheel", "--package", package, "--out-dir", str(wheels)],
            cwd=root,
            check=True,
        )
    subprocess.run(
        [
            "uv",
            "build",
            "--wheel",
            "--directory",
            str(Path(__file__).parent / "fixtures" / "example"),
            "--out-dir",
            str(wheels),
        ],
        check=True,
    )
    subprocess.run(
        ["uv", "venv", "--python", "3.15", str(directory / "venv")],
        check=True,
    )
    python = subprocess.run(
        ["uv", "python", "find", "--no-project", str(directory / "venv")],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(python),
            *map(str, wheels.glob("*.whl")),
        ],
        check=True,
    )
    return python, directory


@pytest.mark.parametrize(
    "script,args",
    [
        ("installed_smoke", ("native", "none")),
        ("installed_smoke", ("native", "server")),
        ("installed_smoke", ("tcp", "none")),
        ("native_edges", ()),
    ],
)
async def test_installed_consumers_and_native_lifecycles(environment, script, args):
    python, directory = environment
    process = await asyncio.create_subprocess_exec(
        str(python),
        str(Path(__file__).with_name(script + ".py")),
        *args,
        cwd=directory,
    )
    try:
        await process.wait()
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    assert process.returncode == 0
