from __future__ import annotations

from pathlib import Path

from .process import JsonlProcessSupervisor
from .protocol import JsonlMessage


def build_thread_start_params(
    developer_instructions: str,
    *,
    model: str | None = None,
    approval_policy: str | None = None,
    cwd: Path | None = None,
    ephemeral: bool | None = None,
) -> dict[str, object]:
    """Build provider-local parameters for a Codex ``thread/start`` request."""

    _validate_non_empty_string("developer_instructions", developer_instructions)
    params: dict[str, object] = {
        "developerInstructions": developer_instructions,
    }
    if model is not None:
        _validate_non_empty_string("model", model)
        params["model"] = model
    if approval_policy is not None:
        _validate_non_empty_string("approval_policy", approval_policy)
        params["approvalPolicy"] = approval_policy
    if cwd is not None:
        if not isinstance(cwd, Path):
            raise TypeError("cwd must be a Path or None")
        params["cwd"] = str(cwd)
    if ephemeral is not None:
        if not isinstance(ephemeral, bool):
            raise TypeError("ephemeral must be a bool or None")
        params["ephemeral"] = ephemeral
    return params


class CodexAppServerClient:
    """Small typed facade over the adapter-local Codex JSONL supervisor."""

    def __init__(self, supervisor: JsonlProcessSupervisor) -> None:
        self.supervisor = supervisor

    async def start_thread(
        self,
        developer_instructions: str,
        *,
        model: str | None = None,
        approval_policy: str | None = None,
        cwd: Path | None = None,
        ephemeral: bool | None = None,
        timeout: float,
    ) -> JsonlMessage:
        return await self.supervisor.request(
            "thread/start",
            build_thread_start_params(
                developer_instructions,
                model=model,
                approval_policy=approval_policy,
                cwd=cwd,
                ephemeral=ephemeral,
            ),
            timeout=timeout,
        )


def _validate_non_empty_string(name: str, value: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")


__all__ = ["CodexAppServerClient", "build_thread_start_params"]
