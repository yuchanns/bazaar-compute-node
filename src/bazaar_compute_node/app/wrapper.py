from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path


def install_bcc_wrapper(bin_dir: Path) -> Path:
    """Install the development wrapper and return the executable path."""

    bin_dir.mkdir(parents=True, exist_ok=True)
    python_executable = Path(sys.executable)
    if os.name == "nt":
        command_path = bin_dir / "bcc.cmd"
        command_path.write_text(
            f'@echo off\r\n"{python_executable}" -m bazaar_compute_node.bcc %*\r\n',
            encoding="utf-8",
        )
        (bin_dir / "bcc.ps1").write_text(
            f'& "{python_executable}" -m bazaar_compute_node.bcc @args\n'
            "exit $LASTEXITCODE\n",
            encoding="utf-8",
        )
        return command_path

    command_path = bin_dir / "bcc"
    command_path.write_text(
        "#!/bin/sh\n"
        f"exec {shlex.quote(str(python_executable))} "
        '-m bazaar_compute_node.bcc "$@"\n',
        encoding="utf-8",
    )
    os.chmod(command_path, 0o755)
    return command_path
