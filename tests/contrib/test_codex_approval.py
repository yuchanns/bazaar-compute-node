from __future__ import annotations

import pytest

from bazaar_compute_node.contrib.codex.approval import parse_approval_request


@pytest.mark.parametrize(
    ("method", "provider_params", "expected_description"),
    [
        (
            "item/commandExecution/requestApproval",
            {
                "reason": "The command needs confirmation.",
                "command": ["python", "-m", "pytest"],
                "cwd": "/workspace",
            },
            (
                "The command needs confirmation.\n"
                "Command: python -m pytest\n"
                "Working directory: /workspace"
            ),
        ),
        (
            "item/fileChange/requestApproval",
            {
                "reason": "The file change needs confirmation.",
                "grantRoot": "/workspace",
                "cwd": "/workspace",
            },
            (
                "The file change needs confirmation.\n"
                "Grant root: /workspace\n"
                "Working directory: /workspace"
            ),
        ),
        (
            "item/permissions/requestApproval",
            {
                "reason": "The runtime needs access.",
                "permissions": {
                    "network": {"host": "api.example.test"},
                    "read": True,
                },
                "cwd": "/workspace",
            },
            (
                "The runtime needs access.\n"
                "Working directory: /workspace\n"
                'Permissions: {"network":{"host":"api.example.test"},"read":true}'
            ),
        ),
    ],
)
def test_parse_approval_request_renders_provider_description(
    method: str,
    provider_params: dict[str, object],
    expected_description: str,
) -> None:
    params: dict[str, object] = {
        "threadId": "thread-1",
        "turnId": "turn-1",
        "itemId": "item-1",
        "startedAtMs": 1,
        **provider_params,
    }
    envelope = parse_approval_request(
        {
            "id": "request-1",
            "method": method,
            "params": params,
        },
        session_id="session-1",
        runtime_session_id="runtime-1",
        turn_id="turn-1",
        provider_thread_id="thread-1",
        provider_turn_id="turn-1",
    )

    assert envelope.request.description == expected_description
