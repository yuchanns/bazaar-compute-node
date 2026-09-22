"""What Claude Code can tell about itself before an agent runs on it."""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path

from ...core.runtime import RuntimeAvailability, RuntimeModel, RuntimeModels
from ...core.utils.command import platform_environment, said_by
from .client import Client
from .process import ProcessSpec, ProcessSupervisor
from .protocol import ClaudeTransportError
from .runtime import ENVIRONMENT

EXECUTABLE = "claude"


async def inspect_runtime(*, timeout: float = 30) -> RuntimeAvailability:
    """Whether Claude Code can be run here, and which version: the command
    has to be on the path and run."""

    executable = await asyncio.to_thread(shutil.which, EXECUTABLE)
    said = (
        None
        if executable is None
        else await said_by(
            executable,
            "--version",
            environment=platform_environment(ENVIRONMENT),
            timeout=timeout,
        )
    )
    if executable is None or said is None:
        return RuntimeAvailability(
            kind="claudecode", available=False, error="not found"
        )
    # `2.1.280 (Claude Code)`: the version comes first
    return RuntimeAvailability(
        kind="claudecode", available=True, version=said.split()[0]
    )


async def list_models(*, timeout: float = 30) -> RuntimeModels:
    """The models Claude Code will answer as: a process of its own is
    started in stream-json mode, asked `list_models`, and stopped again -
    no session is opened and nothing is sent to a model."""

    executable = await asyncio.to_thread(shutil.which, EXECUTABLE)
    if executable is None:
        return RuntimeModels(error="not found")
    supervisor = ProcessSupervisor(
        ProcessSpec(
            executable=executable,
            arguments=(
                "-p",
                "--input-format",
                "stream-json",
                "--output-format",
                "stream-json",
                "--verbose",
            ),
            cwd=Path.cwd(),
            environment=platform_environment(ENVIRONMENT),
        )
    )
    client = Client(supervisor)
    try:
        await supervisor.start(timeout=timeout)
        answer = await client.control({"subtype": "list_models"}, timeout=timeout)
    except (ClaudeTransportError, OSError, TimeoutError) as error:
        return RuntimeModels(error=str(error) or type(error).__name__)
    finally:
        await client.close()
        await supervisor.stop(timeout=timeout)
    return RuntimeModels(models=_read_models(answer.get("response")))


def _read_models(response: object) -> tuple[RuntimeModel, ...]:
    """Each model as `list_models` names it: the value `--model` takes, the
    name to show, and the efforts it supports - none for one that does not
    take an effort."""

    if not isinstance(response, Mapping):
        return ()
    items = response.get("models")
    if not isinstance(items, Sequence):
        return ()
    models: list[RuntimeModel] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        value = item.get("value")
        if not isinstance(value, str) or not value:
            continue
        name = item.get("displayName")
        efforts = item.get("supportedEffortLevels")
        models.append(
            RuntimeModel(
                id=value,
                name=name if isinstance(name, str) else value,
                efforts=tuple(effort for effort in efforts if isinstance(effort, str))
                if isinstance(efforts, Sequence) and not isinstance(efforts, str)
                else (),
                # Claude Code lists what it answers as unasked under this value
                default=value == "default",
            )
        )
    return tuple(models)


__all__ = ["EXECUTABLE", "inspect_runtime", "list_models"]
