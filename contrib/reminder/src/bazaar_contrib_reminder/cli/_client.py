from __future__ import annotations

import os
from collections.abc import Callable, Coroutine, Mapping
from functools import wraps
from typing import Any, cast

import click
from bazaar_contrib_sidecar import Remote, Symbol


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


class ReminderClient:
    def __init__(self, remote: Remote):
        self.remote = remote

    async def request(
        self, resource: str, command: str, payload: Mapping[str, object] = {}
    ) -> Mapping[str, object]:
        """Wait for the node's own command budget and complete send outcome."""
        request: dict[str, object] = {
            "kind": "command",
            "resource": resource,
            "command": command,
            "agent_id": _environment("BCN_AGENT_ID", "AGENT_REQUIRED"),
            "actor_id": _environment("BCN_ACTOR_ID", "SESSION_REQUIRED"),
            "runtime_session_id": _environment(
                "BCN_RUNTIME_SESSION_ID", "SESSION_BINDING_REQUIRED"
            ),
            "session_capability": _environment(
                "BCN_COMMAND_CAPABILITY", "SESSION_BINDING_REQUIRED"
            ),
            **payload,
        }
        service = cast(
            Remote,
            await self.remote.call(
                "accept", Symbol("bazaar_contrib_reminder:REMINDER")
            ),
        )
        response = cast(Mapping[str, object], await service.call("request", request))
        if response.get("ok") is not True:
            raise BccCommandError(
                str(response.get("error", "command failed")),
                code=str(response.get("code", "COMMAND_FAILED")),
                draft_saved=response.get("draft_saved") is True,
                next_action=str(response["next_action"])
                if response.get("next_action") is not None
                else None,
            )
        return cast(Mapping[str, object], response["result"])


def run[**P](
    command: Callable[P, Coroutine[Any, Any, None]],
) -> Callable[P, Coroutine[Any, Any, None]]:
    """Return the command body to invoke's owning event loop."""

    @wraps(command)
    async def invoke(*args: P.args, **kwargs: P.kwargs) -> None:
        try:
            await command(*args, **kwargs)
        except BccCommandError as error:
            from ._format import print_error

            print_error(error)
            raise click.exceptions.Exit(1) from error

    return invoke
