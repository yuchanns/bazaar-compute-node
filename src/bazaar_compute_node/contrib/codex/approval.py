from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from ...core.actor import Actor
from ...core.models import (
    ApprovalDecision,
    ApprovalResult,
)
from ...core.models import (
    ApprovalRequest as CoreApprovalRequest,
)
from .protocol import (
    AppServerProtocolError,
    JsonlMessage,
    JsonlRequestId,
    is_request_id,
)

_COMMAND_METHOD = "item/commandExecution/requestApproval"
_FILE_CHANGE_METHOD = "item/fileChange/requestApproval"
_PERMISSIONS_METHOD = "item/permissions/requestApproval"

_APPROVAL_METHODS = frozenset(
    {
        _COMMAND_METHOD,
        _FILE_CHANGE_METHOD,
        _PERMISSIONS_METHOD,
    }
)


@dataclass(frozen=True, slots=True)
class ApprovalEnvelope:
    """Provider request plus its neutral approval projection."""

    request_id: JsonlRequestId
    method: str
    params: Mapping[str, object]
    request: CoreApprovalRequest


def is_approval_method(method: object) -> bool:
    return isinstance(method, str) and method in _APPROVAL_METHODS


_MAX_APPROVAL_DETAIL = 4_000


_APPROVAL_ACTIONS = {
    _COMMAND_METHOD: "command_execution",
    _FILE_CHANGE_METHOD: "file_change",
    _PERMISSIONS_METHOD: "permissions",
}


def _approval_details(
    method: str, params: Mapping[str, object], permissions: object
) -> dict[str, str]:
    """Read what an approval request is asking to be allowed."""

    details: dict[str, str] = {}
    reason = params.get("reason")
    if isinstance(reason, str) and reason.strip():
        details["reason"] = reason.strip()
    if method == _COMMAND_METHOD:
        command = params.get("command")
        if isinstance(command, str) and command.strip():
            details["command"] = command.strip()
        elif (
            isinstance(command, list)
            and command
            and all(isinstance(argument, str) and argument for argument in command)
        ):
            details["command"] = " ".join(command)
        cwd = params.get("cwd")
        if isinstance(cwd, str) and cwd:
            details["cwd"] = cwd
    elif method == _FILE_CHANGE_METHOD:
        grant_root = params.get("grantRoot")
        if isinstance(grant_root, str) and grant_root:
            details["grant_root"] = grant_root
        cwd = params.get("cwd")
        if isinstance(cwd, str) and cwd:
            details["cwd"] = cwd
    elif isinstance(permissions, Mapping):
        cwd = params.get("cwd")
        if isinstance(cwd, str) and cwd:
            details["cwd"] = cwd
        try:
            encoded_permissions = json.dumps(
                dict(permissions),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        except TypeError, ValueError:
            encoded_permissions = None
        if encoded_permissions:
            details["permissions"] = (
                encoded_permissions[: _MAX_APPROVAL_DETAIL - 1] + "…"
                if len(encoded_permissions) > _MAX_APPROVAL_DETAIL
                else encoded_permissions
            )
    return details


def _approval_metadata(
    method: str, params: Mapping[str, object], provider_item_id: str
) -> dict[str, object]:
    """Carry the provider's own identifiers alongside the request."""

    metadata: dict[str, object] = {
        "provider_method": method,
        "provider_item_id": provider_item_id,
    }
    for provider_key, metadata_key in (
        ("approvalId", "provider_approval_id"),
        ("environmentId", "provider_environment_id"),
    ):
        value = params.get(provider_key)
        if isinstance(value, str) and value:
            metadata[metadata_key] = value
    return metadata


def parse_approval_request(
    message: JsonlMessage,
    *,
    actor: Actor,
    runtime_session_id: str,
    turn_id: str,
    provider_thread_id: str,
    provider_turn_id: str | None,
) -> ApprovalEnvelope:
    method = message.get("method")
    if not isinstance(method, str) or not is_approval_method(method):
        raise AppServerProtocolError("message is not an approval request")
    request_id = message.get("id")
    if not is_request_id(request_id):
        raise AppServerProtocolError("approval request id must be an integer or string")
    request_id = cast(JsonlRequestId, request_id)
    params = message.get("params")
    if not isinstance(params, Mapping):
        raise AppServerProtocolError("approval request params must be an object")

    thread_value = _require_text(params, "threadId")
    if thread_value != provider_thread_id:
        raise AppServerProtocolError(
            "approval request thread does not match runtime thread"
        )
    provider_turn_value = _require_text(params, "turnId")
    if provider_turn_id is None or provider_turn_value != provider_turn_id:
        raise AppServerProtocolError(
            "approval request turn does not match runtime turn"
        )
    provider_item_id = _require_text(params, "itemId")
    started_at_ms = params.get("startedAtMs")
    if (
        not isinstance(started_at_ms, int)
        or isinstance(started_at_ms, bool)
        or started_at_ms < 0
    ):
        raise AppServerProtocolError(
            "approval request startedAtMs must be a non-negative integer"
        )
    permissions = params.get("permissions")
    if method == _PERMISSIONS_METHOD and not isinstance(permissions, Mapping):
        raise AppServerProtocolError(
            "permissions approval request has no permissions object"
        )

    return ApprovalEnvelope(
        request_id=request_id,
        method=method,
        params=params,
        request=CoreApprovalRequest(
            request_id=str(request_id),
            actor=actor,
            runtime_session_id=runtime_session_id,
            action=_APPROVAL_ACTIONS[method],
            created_at_ms=started_at_ms,
            turn_id=turn_id,
            details=_approval_details(method, params, permissions),
            metadata=_approval_metadata(method, params, provider_item_id),
        ),
    )


def build_approval_response(
    approval: ApprovalEnvelope,
    result: ApprovalResult,
) -> Mapping[str, object]:
    if result.request_id != approval.request.request_id:
        raise ValueError("approval result request id does not match provider request")
    approved = result.decision is ApprovalDecision.APPROVED
    if approval.method in {_COMMAND_METHOD, _FILE_CHANGE_METHOD}:
        return {"decision": "accept" if approved else "decline"}
    if approval.method == _PERMISSIONS_METHOD:
        if not approved:
            return {"permissions": {}}
        permissions = approval.params.get("permissions")
        if not isinstance(permissions, Mapping):
            raise AppServerProtocolError(
                "permissions approval request has no permissions object"
            )
        return {"permissions": dict(permissions), "scope": "turn"}
    raise AppServerProtocolError("unsupported approval response method")


def approval_error(error: BaseException) -> Mapping[str, object]:
    """Return a provider-safe JSON-RPC error without leaking request contents."""

    return {
        "code": -32000,
        "message": f"approval bridge failed: {type(error).__name__}",
    }


def _require_text(params: Mapping[str, object], field_name: str) -> str:
    value = params.get(field_name)
    if not isinstance(value, str) or not value:
        raise AppServerProtocolError(
            f"approval request {field_name} must be non-empty text"
        )
    return value


__all__ = [
    "ApprovalEnvelope",
    "approval_error",
    "build_approval_response",
    "is_approval_method",
    "parse_approval_request",
]
