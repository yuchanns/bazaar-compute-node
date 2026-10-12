"""Build and install the app with an independent third-party carrier wheel."""

lazy import asyncio
lazy import os
lazy import subprocess
lazy import sys
lazy from pathlib import Path


async def test_installed_node_uses_files_and_a_persistent_remote_carrier(
    tmp_path: Path,
):
    root = (await asyncio.to_thread(Path(__file__).resolve)).parents[2]
    artifacts = tmp_path / "wheels"

    def build():
        for package in (
            "bazaar-core",
            "bazaar-contrib-logging",
            "bazaar-contrib-status",
            "bazaar-contrib-config",
            "bazaar-contrib-config-filesystem",
            "bazaar-contrib-config-versions",
            "bazaar-compute-node",
        ):
            subprocess.run(
                [
                    "uv",
                    "build",
                    "--wheel",
                    "--package",
                    package,
                    "--out-dir",
                    str(artifacts),
                ],
                cwd=root,
                check=True,
                capture_output=True,
            )
        subprocess.run(
            [
                "uv",
                "build",
                "--wheel",
                "--directory",
                str(Path(__file__).parent / "fixtures" / "contribs"),
                "--out-dir",
                str(artifacts),
            ],
            check=True,
            capture_output=True,
        )
        environment = tmp_path / "environment"
        subprocess.run(
            ["uv", "venv", "--python", "3.15", str(environment)],
            check=True,
            capture_output=True,
        )
        executable = environment / (
            "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
        )
        subprocess.run(
            [
                "uv",
                "pip",
                "install",
                "--python",
                str(executable),
                "blockbuster>=1.5.29,<1.6",
                *map(str, artifacts.glob("*.whl")),
            ],
            check=True,
            capture_output=True,
        )
        return executable

    executable = await asyncio.to_thread(build)
    command = [
        str(executable),
        str(Path(__file__).with_name("configuration_smoke.py")),
        str(tmp_path),
    ]
    environment = dict(os.environ)
    environment["PATH"] = (
        str(executable.parent) + os.pathsep + environment.get("PATH", "")
    )
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=tmp_path,
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        output, errors = await asyncio.wait_for(process.communicate(), 90)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    assert process.returncode == 0, (output + errors).decode()
    assert "workflow passed" in output.decode()
