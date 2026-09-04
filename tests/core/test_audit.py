from __future__ import annotations

from dataclasses import replace

import pytest
from bcn_test_support import RecordingAudit

from bazaar_compute_node.core.actor import Thread
from bazaar_compute_node.core.approval import ApprovalBinding
from bazaar_compute_node.core.audit import AuditEvent, ErrorKind
from bazaar_compute_node.core.correlation import CorrelationContext
from bazaar_compute_node.core.lifecycle import TimeoutBudget
from bazaar_compute_node.core.models import (
    ApprovalRequest,
    RuntimeEventState,
)
from bazaar_compute_node.core.observability import LogLevel
from bazaar_compute_node.core.orchestration.services import SessionAuditRecorder


class _FailingAudit:
    @property
    def name(self) -> str:
        return "failing"

    async def append(self, event: AuditEvent, *, timeout: float) -> None:
        del event, timeout
        raise OSError("audit sink unavailable")


def make_approval_request() -> ApprovalRequest:
    return ApprovalRequest(
        request_id="request-1",
        actor=Thread("bcn-1"),
        runtime_session_id="runtime-1",
        action="command",
        created_at_ms=1,
        turn_id="turn-1",
    )


def test_approval_binding_preserves_runtime_to_channel_correlation() -> None:
    request = make_approval_request()
    binding = ApprovalBinding(
        request_id=request.request_id,
        actor=request.actor,
        channel_session_id="channel-1",
        runtime_session_id=request.runtime_session_id,
        turn_id=request.turn_id,
    )

    assert binding.matches(request)
    assert not binding.matches(replace(request, request_id="request-2"))


def test_audit_event_requires_stable_error_kind_and_redacted_metadata() -> None:
    event = AuditEvent(
        event_name="runtime.turn.failed",
        state=RuntimeEventState.FAILED,
        created_at_ms=2,
        correlation=CorrelationContext(
            bcn_session_id="bcn-1",
            runtime_session_id="runtime-1",
            turn_id="turn-1",
        ),
        level=LogLevel.ERROR,
        error_kind=ErrorKind.PROVIDER_UNKNOWN,
        error_message="provider completion was not observed",
        metadata={"retryable": False},
    )

    assert event.error_kind is ErrorKind.PROVIDER_UNKNOWN
    with pytest.raises(ValueError, match="error_kind"):
        AuditEvent(
            event_name="runtime.turn.failed",
            state=RuntimeEventState.FAILED,
            created_at_ms=2,
            correlation=event.correlation,
            error_message="unclassified",
        )
    with pytest.raises(ValueError, match="sensitive field"):
        AuditEvent(
            event_name="runtime.turn.failed",
            state=RuntimeEventState.FAILED,
            created_at_ms=2,
            correlation=event.correlation,
            metadata={"token": "secret"},
        )
    with pytest.raises(ValueError, match="sensitive field"):
        AuditEvent(
            event_name="runtime.turn.failed",
            state=RuntimeEventState.FAILED,
            created_at_ms=2,
            correlation=event.correlation,
            metadata={
                "provider": {
                    "permission_denials": [{"tool_input": {"token": "nested-secret"}}]
                }
            },
        )


@pytest.mark.asyncio
async def test_session_audit_recursively_omits_sensitive_provider_metadata() -> None:
    audit = RecordingAudit()
    recorder = SessionAuditRecorder(
        sink=audit,
        timeout_budget=TimeoutBudget(1, 1, 1, 1),
        clock=lambda: 1,
    )

    await recorder.append(
        event_name="claudecode.turn.completed",
        state=RuntimeEventState.COMPLETED,
        correlation=CorrelationContext(bcn_session_id="session-1"),
        metadata={
            "provider_subtype": "success",
            "permission_denial_count": 1,
            "permission_denials": [
                {
                    "tool_name": "Bash",
                    "tool_input": {
                        "command": "deploy",
                        "token": "nested-secret",
                    },
                }
            ],
        },
    )

    metadata = audit.events[0].metadata
    assert metadata["provider_subtype"] == "success"
    assert metadata["permission_denial_count"] == 1
    assert "permission_denials" not in metadata


@pytest.mark.asyncio
async def test_session_audit_failure_does_not_fail_business_operation() -> None:
    recorder = SessionAuditRecorder(
        sink=_FailingAudit(),
        timeout_budget=TimeoutBudget(1, 1, 1, 1),
        clock=lambda: 1,
    )

    await recorder.append(
        event_name="runtime.turn.completed",
        state=RuntimeEventState.COMPLETED,
        correlation=CorrelationContext(bcn_session_id="session-1"),
    )
