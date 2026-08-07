from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path


def _wrapper_paths(command_path: Path) -> tuple[Path, ...]:
    if command_path.name == "bcc":
        return (command_path,)
    if command_path.name == "bcc.cmd":
        return (command_path, command_path.with_name("bcc.ps1"))
    raise ValueError(f"unsupported bcc wrapper path: {command_path}")


def install_bcc_wrapper(bin_dir: Path) -> Path:
    """Install the development wrapper and return the executable path."""

    bin_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        os.chmod(bin_dir, 0o700)
    python_executable = Path(sys.executable)
    if os.name == "nt":
        command_path = bin_dir / "bcc.cmd"
        try:
            command_path.write_text(
                f'@echo off\r\n"{python_executable}" -m bazaar_compute_node.bcc %*\r\n',
                encoding="utf-8",
            )
            (bin_dir / "bcc.ps1").write_text(
                f'& "{python_executable}" -m bazaar_compute_node.bcc @args\n'
                "exit $LASTEXITCODE\n",
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
    """Remove only the wrapper files generated for one node instance."""

    for path in _wrapper_paths(command_path):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
