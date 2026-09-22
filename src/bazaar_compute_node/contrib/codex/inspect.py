"""What Codex can tell about itself before an agent runs on it."""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Mapping
from typing import Any, cast

from ...core.client import CLIENT_INFO
from ...core.runtime import RuntimeAvailability, RuntimeModel, RuntimeModels
from ...core.utils.command import platform_environment, said_by
from .client import Client
from .process import JsonlProcessSpec, JsonlProcessSupervisor
from .protocol import AppServerProtocolError, JsonlTransportError
from .runtime import ENVIRONMENT

EXECUTABLE = "codex"


async def inspect_runtime(*, timeout: float = 30) -> RuntimeAvailability:
    """Whether Codex can be run here, and which version: the command has to
    be on the path and run."""

    executable = await asyncio.to_thread(shutil.which, EXECUTABLE)
    if executable is None:
        return RuntimeAvailability(kind="codex", available=False, error="not found")
    version = await _version(executable, timeout=timeout)
    if version is None:
        return RuntimeAvailability(kind="codex", available=False, error="not found")
    return RuntimeAvailability(kind="codex", available=True, version=version)


async def list_models(*, timeout: float = 30) -> RuntimeModels:
    """The models Codex will answer as: an App Server of its own is
    started, asked, and stopped again - an agent's own server is never
    disturbed for this."""

    executable = await asyncio.to_thread(shutil.which, EXECUTABLE)
    if executable is None:
        return RuntimeModels(error="not found")
    return await _models(executable, timeout=timeout)


async def _version(executable: str, *, timeout: float) -> str | None:
    """What the command says it is, or nothing when it will not run."""

    # `codex-cli 0.5.0`, and any other shape: the version is the last word
    said = await said_by(
        executable,
        "--version",
        environment=platform_environment(ENVIRONMENT),
        timeout=timeout,
    )
    return said.split()[-1] if said else None


async def _models(executable: str, *, timeout: float) -> RuntimeModels:
    """The models the App Server lists, page after page; or none, and why it
    would not say. The shapes are `ModelListResponse` and `Model` in
    codex-rs/app-server-protocol/src/protocol/v2/model.rs."""

    supervisor = JsonlProcessSupervisor(
        JsonlProcessSpec(
            executable=executable,
            arguments=("app-server", "--stdio"),
            environment=platform_environment(ENVIRONMENT),
        )
    )
    client = Client(supervisor)
    models: list[RuntimeModel] = []
    try:
        await supervisor.start(timeout=timeout)
        await client.initialize(client_info=CLIENT_INFO, timeout=timeout)
        cursor: str | None = None
        while True:
            answer = await supervisor.request(
                "model/list", {"cursor": cursor}, timeout=timeout
            )
            page = cast(Mapping[str, Any], answer["result"])
            models.extend(_model(item) for item in page["data"] if not item["hidden"])
            cursor = page.get("nextCursor")
            if cursor is None:
                break
    except (
        JsonlTransportError,
        AppServerProtocolError,
        OSError,
        TimeoutError,
    ) as error:
        return RuntimeModels(error=str(error) or type(error).__name__)
    finally:
        await supervisor.stop(timeout=timeout)
    return RuntimeModels(models=tuple(models))


def _model(item: Mapping[str, Any]) -> RuntimeModel:
    """One model as the App Server lists it: the name `thread/start` takes,
    the one to show, and the efforts it takes."""

    return RuntimeModel(
        id=item["model"],
        name=item["displayName"],
        efforts=tuple(
            option["reasoningEffort"] for option in item["supportedReasoningEfforts"]
        ),
        default_effort=item["defaultReasoningEffort"],
        default=item["isDefault"],
    )


__all__ = ["EXECUTABLE", "inspect_runtime", "list_models"]
