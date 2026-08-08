from __future__ import annotations

from dataclasses import replace

import pytest

from bazaar_compute_node.core.approval import ApprovalBinding
from bazaar_compute_node.core.audit import AuditEvent, ErrorKind
from bazaar_compute_node.core.correlation import CorrelationContext
from bazaar_compute_node.core.models import (
    ApprovalRequest,
    RuntimeEventState,
)
from bazaar_compute_node.core.observability import LogLevel


def make_approval_request() -> ApprovalRequest:
    return ApprovalRequest(
        request_id="request-1",
        session_id="bcn-1",
        runtime_session_id="runtime-1",
        action="command",
        created_at_ms=1,
        turn_id="turn-1",
    )


def test_approval_binding_preserves_runtime_to_channel_correlation() -> None:
    request = make_approval_request()
    binding = ApprovalBinding(
        request_id=request.request_id,
        bcn_session_id=request.session_id,
        channel_session_id="channel-1",
        runtime_session_id=request.runtime_session_id,
        turn_id=request.turn_id,
    )

    assert binding.matches(request)
    assert not binding.matches(replace(request, request_id="request-2"))


def test_correlation_context_rejects_invalid_local_sequence() -> None:
    with pytest.raises(ValueError, match="inbound_seq"):
        CorrelationContext(bcn_session_id="bcn-1", inbound_seq=-1)


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
