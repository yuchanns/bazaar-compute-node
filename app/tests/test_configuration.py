"""Build and install the app with an independent third-party carrier wheel."""

lazy import asyncio
lazy import os
lazy import subprocess
lazy from pathlib import Path


async def test_installed_node_uses_files_and_a_persistent_remote_carrier(
    tmp_path: Path,
):
    root = (await asyncio.to_thread(Path(__file__).resolve)).parents[2]
    artifacts = tmp_path / "wheels"

    def build():
        for package in (
            "bazaar-core",
            "bazaar-contrib-agent",
            "bazaar-contrib-agent-command",
            "bazaar-contrib-audit",
            "bazaar-contrib-channel",
            "bazaar-contrib-timer",
            "bazaar-contrib-harness",
            "bazaar-contrib-status",
            "bazaar-contrib-config",
            "bazaar-contrib-config-versions",
            "bazaar-contrib-manager",
            "bazaar-contrib-invoke",
            "bazaar-contrib-storage",
            "bazaar-contrib-schema",
            "bazaar-contrib-legacy-node-migrations",
            "bazaar-contrib-messages",
            "bazaar-contrib-messages-node",
            "bazaar-contrib-collaboration",
            "bazaar-contrib-reminder",
            "bazaar-contrib-node-management",
            "bazaar-contrib-sidecar",
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
        )
        packages: dict[str, list[Path]] = {}
        for wheel in artifacts.glob("*.whl"):
            name = wheel.name.split("-", 1)[0].replace("_", "-")
            packages.setdefault(name, []).append(wheel)
        for name, wheels in packages.items():
            index = artifacts / "simple" / name
            index.mkdir(parents=True)
            (index / "index.html").write_text(
                "\n".join(
                    f'<a href="{wheel.as_uri()}">{wheel.name}</a>' for wheel in wheels
                )
            )
        source = (artifacts / "simple").as_uri()
        for mode in ("default", "explicit", "custom"):
            directory = tmp_path / mode
            directory.mkdir()
            prefix = directory / "environment"
            subprocess.run(["uv", "venv", "--python", "3.15", str(prefix)], check=True)
            python = prefix / (
                "Scripts/python.exe" if os.name == "nt" else "bin/python"
            )
            subprocess.run(
                [
                    "uv",
                    "pip",
                    "install",
                    "--python",
                    str(python),
                    "--index",
                    source,
                    "--prerelease",
                    "allow",
                    "blockbuster>=1.5.29,<1.6",
                    str(packages["bazaar-compute-node"][0]),
                    *(
                        [str(packages["bcn-example-contribs"][0])]
                        if mode == "custom"
                        else []
                    ),
                ],
                check=True,
            )
            subprocess.run(
                [
                    str(python),
                    str(Path(__file__).with_name("bootstrap_smoke.py")),
                    str(directory),
                    mode,
                ],
                cwd=directory,
                env={**os.environ, "BCN_HOME": str(directory)},
                check=True,
            )
        environment = tmp_path / "environment"
        subprocess.run(
            ["uv", "venv", "--python", "3.15", str(environment)],
            check=True,
        )
        executable = Path(
            subprocess.run(
                ["uv", "python", "find", "--no-project", str(environment)],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
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
        )
        return executable

    executable = await asyncio.to_thread(build)
    command = [
        str(executable),
        str(root / "tests/run_installed.py"),
        str(artifacts),
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
    )
    try:
        await process.wait()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    assert process.returncode == 0
