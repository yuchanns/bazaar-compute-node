from __future__ import annotations

lazy import asyncio
lazy from pathlib import Path


async def test_independent_provider_uses_only_the_public_contract(tmp_path: Path):
    root = (await asyncio.to_thread(Path(__file__).resolve)).parents[3]
    output = tmp_path / "distributions"
    await asyncio.to_thread(output.mkdir)
    for path in (
        root / "core",
        root / "contrib/timer",
        Path(__file__).parent / "fixtures/asyncio",
    ):
        process = await asyncio.create_subprocess_exec(
            "uv",
            "build",
            str(path),
            "--no-sources",
            "--out-dir",
            str(output),
            cwd=tmp_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await process.communicate()
        assert process.returncode == 0, stdout.decode()

    for suffix in ("*.whl", "*.tar.gz"):
        command = ["uv", "run", "--isolated", "--no-project", "--python", "3.15"]
        for artifact in await asyncio.to_thread(list, output.glob(suffix)):
            command.extend(["--with", str(artifact)])
        command.append(str(Path(__file__).with_name("provider_smoke.py")))
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=tmp_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await process.communicate()
        assert process.returncode == 0, stdout.decode()
