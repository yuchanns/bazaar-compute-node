from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ...core.client import ClientInfo
from .process import JsonlProcessSupervisor
from .protocol import (
    CodexAppServerProtocolError,
    JsonlMessage,
)


@dataclass(frozen=True, slots=True)
class CodexThreadInfo:
    """Provider-local thread identity returned by App Server."""

    thread_id: str
    session_id: str | None = None
    path: str | None = None


@dataclass(frozen=True, slots=True)
class CodexTurnInfo:
    """Provider-local turn identity and terminal status."""

    turn_id: str
    status: str
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class CodexErrorInfo:
    """Provider-local error notification fields needed by the runtime adapter."""

    thread_id: str
    turn_id: str
    will_retry: bool
    message: str
    error_type: str | None = None


def build_initialize_params(
    client_info: ClientInfo,
) -> dict[str, object]:
    return {
        "clientInfo": {
            "name": client_info.name,
            "version": client_info.version,
        },
        "capabilities": {"experimentalApi": True},
    }


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
        params["cwd"] = _path_text(cwd, "cwd")
    if ephemeral is not None:
        if not isinstance(ephemeral, bool):
            raise TypeError("ephemeral must be a bool or None")
        params["ephemeral"] = ephemeral
    return params


def build_thread_resume_params(
    thread_id: str,
    *,
    model: str | None = None,
    approval_policy: str | None = None,
    cwd: Path | None = None,
) -> dict[str, object]:
    """Build provider-local parameters for a persisted thread resume."""

    _validate_non_empty_string("thread_id", thread_id)
    params: dict[str, object] = {
        "threadId": thread_id,
        "excludeTurns": True,
    }
    if model is not None:
        _validate_non_empty_string("model", model)
        params["model"] = model
    if approval_policy is not None:
        _validate_non_empty_string("approval_policy", approval_policy)
        params["approvalPolicy"] = approval_policy
    if cwd is not None:
        params["cwd"] = _path_text(cwd, "cwd")
    return params


def build_turn_start_params(
    thread_id: str,
    input_text: str,
    *,
    client_user_message_id: str | None = None,
    model: str | None = None,
    effort: str | None = None,
    approval_policy: str | None = None,
    cwd: Path | None = None,
    sandbox_policy: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build a text turn request with optional model and reasoning effort."""

    _validate_non_empty_string("thread_id", thread_id)
    _validate_non_empty_string("input_text", input_text)
    params: dict[str, object] = {
        "threadId": thread_id,
        "input": [{"type": "text", "text": input_text}],
    }
    if client_user_message_id is not None:
        _validate_non_empty_string("client_user_message_id", client_user_message_id)
        params["clientUserMessageId"] = client_user_message_id
    if model is not None:
        _validate_non_empty_string("model", model)
        params["model"] = model
    if effort is not None:
        _validate_non_empty_string("effort", effort)
        params["effort"] = effort
    if approval_policy is not None:
        _validate_non_empty_string("approval_policy", approval_policy)
        params["approvalPolicy"] = approval_policy
    if cwd is not None:
        params["cwd"] = _path_text(cwd, "cwd")
    if sandbox_policy is not None:
        if not sandbox_policy:
            raise ValueError("sandbox_policy must not be empty")
        params["sandboxPolicy"] = dict(sandbox_policy)
    return params


def build_turn_interrupt_params(thread_id: str, turn_id: str) -> dict[str, object]:
    _validate_non_empty_string("thread_id", thread_id)
    _validate_non_empty_string("turn_id", turn_id)
    return {"threadId": thread_id, "turnId": turn_id}


def build_turn_steer_params(
    thread_id: str,
    turn_id: str,
    input_text: str,
) -> dict[str, object]:
    _validate_non_empty_string("thread_id", thread_id)
    _validate_non_empty_string("turn_id", turn_id)
    _validate_non_empty_string("input_text", input_text)
    return {
        "threadId": thread_id,
        "expectedTurnId": turn_id,
        "input": [{"type": "text", "text": input_text}],
    }


class CodexAppServerClient:
    """Typed facade over the adapter-local Codex JSONL supervisor."""

    def __init__(self, supervisor: JsonlProcessSupervisor) -> None:
        self.supervisor = supervisor

    async def initialize(
        self,
        *,
        client_info: ClientInfo,
        timeout: float,
    ) -> JsonlMessage:
        response = await self.supervisor.request(
            "initialize",
            build_initialize_params(client_info),
            timeout=timeout,
        )
        await self.supervisor.notify("initialized", timeout=timeout)
        return response

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

    async def resume_thread(
        self,
        thread_id: str,
        *,
        model: str | None = None,
        approval_policy: str | None = None,
        cwd: Path | None = None,
        timeout: float,
    ) -> JsonlMessage:
        return await self.supervisor.request(
            "thread/resume",
            build_thread_resume_params(
                thread_id,
                model=model,
                approval_policy=approval_policy,
                cwd=cwd,
            ),
            timeout=timeout,
        )

    async def read_thread(
        self,
        thread_id: str,
        *,
        include_turns: bool = True,
        timeout: float,
    ) -> JsonlMessage:
        _validate_non_empty_string("thread_id", thread_id)
        return await self.supervisor.request(
            "thread/read",
            {"threadId": thread_id, "includeTurns": include_turns},
            timeout=timeout,
        )

    async def start_turn(
        self,
        thread_id: str,
        input_text: str,
        *,
        client_user_message_id: str | None = None,
        model: str | None = None,
        effort: str | None = None,
        approval_policy: str | None = None,
        cwd: Path | None = None,
        sandbox_policy: Mapping[str, object] | None = None,
        timeout: float,
    ) -> JsonlMessage:
        return await self.supervisor.request(
            "turn/start",
            build_turn_start_params(
                thread_id,
                input_text,
                client_user_message_id=client_user_message_id,
                model=model,
                effort=effort,
                approval_policy=approval_policy,
                cwd=cwd,
                sandbox_policy=sandbox_policy,
            ),
            timeout=timeout,
        )

    async def interrupt_turn(
        self,
        thread_id: str,
        turn_id: str,
        *,
        timeout: float,
    ) -> JsonlMessage:
        return await self.supervisor.request(
            "turn/interrupt",
            build_turn_interrupt_params(thread_id, turn_id),
            timeout=timeout,
        )

    async def steer_turn(
        self,
        thread_id: str,
        turn_id: str,
        input_text: str,
        *,
        timeout: float,
    ) -> JsonlMessage:
        return await self.supervisor.request(
            "turn/steer",
            build_turn_steer_params(thread_id, turn_id, input_text),
            timeout=timeout,
        )

    async def receive(self, *, timeout: float | None = None) -> JsonlMessage:
        return await self.supervisor.receive(timeout=timeout)


def parse_thread_response(response: Mapping[str, object]) -> CodexThreadInfo:
    result = _require_mapping(response, "result")
    thread = _require_mapping(result, "thread")
    thread_id = _require_text(thread, "id", "thread.id")
    return CodexThreadInfo(
        thread_id=thread_id,
        session_id=_optional_text(thread.get("sessionId"), "thread.sessionId"),
        path=_optional_text(thread.get("path"), "thread.path"),
    )


def parse_turn_response(response: Mapping[str, object]) -> CodexTurnInfo:
    result = _require_mapping(response, "result")
    return _parse_turn(_require_mapping(result, "turn"))


def parse_turn_steer_response(response: Mapping[str, object]) -> str:
    result = _require_mapping(response, "result")
    return _require_text(result, "turnId", "result.turnId")


def parse_turn_notification(message: Mapping[str, object]) -> tuple[str, CodexTurnInfo]:
    params = _require_mapping(message, "params")
    thread_id = _require_text(params, "threadId", "params.threadId")
    turn = _parse_turn(_require_mapping(params, "turn"))
    return thread_id, turn


def parse_error_notification(message: Mapping[str, object]) -> CodexErrorInfo:
    params = _require_mapping(message, "params")
    thread_id = _require_text(params, "threadId", "params.threadId")
    turn_id = _require_text(params, "turnId", "params.turnId")
    will_retry = params.get("willRetry")
    if not isinstance(will_retry, bool):
        raise CodexAppServerProtocolError("params.willRetry must be a bool")
    error = _require_mapping(params, "error")
    message_text = _require_text(error, "message", "params.error.message")
    error_type = _error_type(error.get("codexErrorInfo"))
    return CodexErrorInfo(
        thread_id=thread_id,
        turn_id=turn_id,
        will_retry=will_retry,
        message=message_text,
        error_type=error_type,
    )


def _parse_turn(value: Mapping[str, object]) -> CodexTurnInfo:
    turn_id = _require_text(value, "id", "turn.id")
    status = _require_text(value, "status", "turn.status")
    error_value = value.get("error")
    error_message = None
    if error_value is not None:
        if not isinstance(error_value, Mapping):
            raise CodexAppServerProtocolError("turn.error must be an object")
        error_message = _require_text(
            error_value,
            "message",
            "turn.error.message",
        )
    return CodexTurnInfo(
        turn_id=turn_id,
        status=status,
        error_message=error_message,
    )


def _error_type(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str) and value:
        return value
    if isinstance(value, Mapping):
        if len(value) != 1:
            raise CodexAppServerProtocolError(
                "params.error.codexErrorInfo must contain one error type"
            )
        key = next(iter(value))
        if not isinstance(key, str) or not key:
            raise CodexAppServerProtocolError(
                "params.error.codexErrorInfo has an invalid error type"
            )
        return key
    raise CodexAppServerProtocolError(
        "params.error.codexErrorInfo must be text, an object, or null"
    )


def _require_mapping(value: Mapping[str, object], key: str) -> Mapping[str, object]:
    nested = value.get(key)
    if not isinstance(nested, Mapping):
        raise CodexAppServerProtocolError(f"{key} must be an object")
    return nested


def _require_text(value: Mapping[str, object], key: str, field_name: str) -> str:
    nested = value.get(key)
    if not isinstance(nested, str) or not nested:
        raise CodexAppServerProtocolError(f"{field_name} must be non-empty text")
    return nested


def _optional_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise CodexAppServerProtocolError(f"{field_name} must be non-empty text")
    return value


def _path_text(value: Path, field_name: str) -> str:
    if not isinstance(value, Path):
        raise TypeError(f"{field_name} must be a Path")
    return str(value)


def _validate_non_empty_string(name: str, value: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")


__all__ = [
    "CodexAppServerClient",
    "CodexAppServerProtocolError",
    "CodexErrorInfo",
    "CodexThreadInfo",
    "CodexTurnInfo",
    "build_initialize_params",
    "build_thread_resume_params",
    "build_thread_start_params",
    "build_turn_interrupt_params",
    "build_turn_start_params",
    "parse_error_notification",
    "parse_thread_response",
    "parse_turn_notification",
    "parse_turn_response",
]
