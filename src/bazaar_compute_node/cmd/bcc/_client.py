from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Coroutine, Mapping
from functools import wraps
from typing import Any, cast

from ...app.transport import LocalCommandClient


class BccCommandError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        draft_saved: bool | None = None,
        next_action: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.draft_saved = draft_saved
        self.next_action = next_action


def _environment(name: str, code: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise BccCommandError(f"{name} is not set", code=code)
    return value


async def request(
    resource: str,
    command: str,
    payload: Mapping[str, object] = {},
    *,
    timeout: float | None = 10,
) -> Mapping[str, object]:
    """Send one session-scoped command to the node this agent runs under."""

    endpoint = _environment("BCN_ENDPOINT", "LOCAL_ENDPOINT_REQUIRED")
    request: dict[str, object] = {
        "kind": "command",
        "resource": resource,
        "command": command,
        "session_id": _environment("BCN_SESSION_ID", "SESSION_REQUIRED"),
        "runtime_session_id": _environment(
            "BCN_RUNTIME_SESSION_ID", "SESSION_BINDING_REQUIRED"
        ),
        "session_capability": _environment(
            "BCN_COMMAND_CAPABILITY", "SESSION_BINDING_REQUIRED"
        ),
        **payload,
    }
    response = await LocalCommandClient.request(endpoint, request, timeout=timeout)
    if response.get("ok") is not True:
        raise BccCommandError(
            str(response.get("error", "command failed")),
            code=str(response.get("code", "COMMAND_FAILED")),
            draft_saved=response.get("draft_saved") is True,
            next_action=(
                str(response["next_action"])
                if response.get("next_action") is not None
                else None
            ),
        )
    return cast(Mapping[str, object], response["result"])


def run[**P](
    command: Callable[P, Coroutine[Any, Any, None]],
) -> Callable[P, None]:
    """Let a click command body be async without each one opening a loop."""

    @wraps(command)
    def invoke(*args: P.args, **kwargs: P.kwargs) -> None:
        asyncio.run(command(*args, **kwargs))

    return invoke


__all__ = ["BccCommandError", "request", "run"]
