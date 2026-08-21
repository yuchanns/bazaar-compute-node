from __future__ import annotations

import os
import re
import shlex
import sys
from pathlib import Path

_AGENT_ID = re.compile(r"^[A-Za-z0-9._-]+$")


def _wrapper_paths(command_path: Path) -> tuple[Path, ...]:
    if command_path.name == "bcc":
        return (command_path,)
    if command_path.name == "bcc.cmd":
        return (command_path, command_path.with_name("bcc.ps1"))
    raise ValueError(f"unsupported bcc wrapper path: {command_path}")


def install_bcc_wrapper(bin_dir: Path, *, agent_id: str) -> Path:
    """Install one Agent-bound bcc wrapper and return its executable path."""

    if not isinstance(agent_id, str) or not _AGENT_ID.fullmatch(agent_id):
        raise ValueError("agent_id contains unsupported wrapper characters")
    bin_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        os.chmod(bin_dir, 0o700)
    python_executable = Path(sys.executable)
    if os.name == "nt":
        command_path = bin_dir / "bcc.cmd"
        try:
            command_path.write_text(
                "@echo off\n"
                f'set "BCN_AGENT_ID={agent_id}"\n'
                'set "PYTHONUTF8=1"\n'
                'set "PYTHONIOENCODING=utf-8"\n'
                f'"{python_executable}" -m bazaar_compute_node.bcc %*\n',
                encoding="utf-8",
            )
            (bin_dir / "bcc.ps1").write_text(
                f"$env:BCN_AGENT_ID = '{agent_id}'\n"
                "$env:PYTHONUTF8 = '1'\n"
                "$env:PYTHONIOENCODING = 'utf-8'\n"
                f'$python = "{python_executable}"\n'
                "$previousOutputEncoding = $OutputEncoding\n"
                "$exitCode = 1\n"
                "try {\n"
                "    $OutputEncoding = [System.Text.UTF8Encoding]::new($false)\n"
                "    if ($MyInvocation.ExpectingInput) {\n"
                "        @($Input) | & $python -m bazaar_compute_node.bcc @args\n"
                "    } else {\n"
                "        & $python -m bazaar_compute_node.bcc @args\n"
                "    }\n"
                "    $exitCode = $LASTEXITCODE\n"
                "} finally {\n"
                "    $OutputEncoding = $previousOutputEncoding\n"
                "}\n"
                "exit $exitCode\n",
                encoding="utf-8",
            )
        except BaseException:
            remove_bcc_wrapper(command_path)
            raise
        return command_path

    command_path = bin_dir / "bcc"
    try:
        command_path.write_text(
            "#!/bin/sh\n"
            f"export BCN_AGENT_ID={shlex.quote(agent_id)}\n"
            f"exec {shlex.quote(str(python_executable))} "
            '-m bazaar_compute_node.bcc "$@"\n',
            encoding="utf-8",
        )
        os.chmod(command_path, 0o700)
    except BaseException:
        remove_bcc_wrapper(command_path)
        raise
    return command_path


def remove_bcc_wrapper(command_path: Path) -> None:
    """Remove only the wrapper files generated for one Agent workspace."""

    for path in _wrapper_paths(command_path):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
