"""Install and release the default agent's workspace-owned bcc launcher."""

from __future__ import annotations

lazy import os
lazy import shlex
lazy import sys
lazy from pathlib import Path

lazy from jinja2 import Environment, PackageLoader, StrictUndefined

lazy from .models import AgentConfig


def install(config: AgentConfig, workspace: Path) -> Path:
    directory = workspace / ".bcn" / "bin"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        directory.chmod(0o700)
    command = directory / ("bcc.cmd" if os.name == "nt" else "bcc")
    try:
        templates = Environment(
            loader=PackageLoader("bazaar_contrib_agent", "resources"),
            undefined=StrictUndefined,
            autoescape=False,
            keep_trailing_newline=True,
        )
        if os.name == "nt":
            command.write_text(
                templates.get_template("bcc.cmd").render(
                    agent_id=config.id, mode=config.mode, python=sys.executable
                ),
                encoding="utf-8",
            )
            command.with_suffix(".ps1").write_text(
                templates.get_template("bcc.ps1").render(
                    agent_id=config.id, mode=config.mode, python=sys.executable
                ),
                encoding="utf-8",
            )
        else:
            command.write_text(
                templates.get_template("bcc.sh").render(
                    agent_id=shlex.quote(config.id),
                    mode=shlex.quote(config.mode),
                    python=shlex.quote(sys.executable),
                ),
                encoding="utf-8",
            )
            command.chmod(0o700)
    except BaseException:
        remove(command)
        raise
    return command


def remove(command: Path) -> None:
    for path in (
        (command, command.with_suffix(".ps1"))
        if command.suffix == ".cmd"
        else (command,)
    ):
        path.unlink(missing_ok=True)
