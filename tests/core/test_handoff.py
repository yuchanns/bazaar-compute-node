from __future__ import annotations

from bazaar_compute_node.core.handoff import (
    HANDOFF_CHECK_LIMIT,
    HandoffCheckItem,
    HandoffCheckRequest,
    HandoffCheckResult,
    HandoffSendRequest,
    HandoffSendResult,
)
from bazaar_compute_node.core.models import Handoff


def make_handoff(
    *,
    source_message_id: str | None = "message-1",
    body: str = "Read the source conversation.\nSend the requested details.",
    created_at_ms: int = 1_000,
    read_at_ms: int | None = None,
) -> Handoff:
    return Handoff(
        handoff_id="handoff-1",
        command_id="command-1",
        source_session_id="session-source",
        target_session_id="session-target",
        source_message_id=source_message_id,
        body=body,
        created_at_ms=created_at_ms,
        read_at_ms=read_at_ms,
    )


def test_handoff_preserves_multiline_body_and_optional_source_message() -> None:
    body = "Read the source conversation.\n\nSend the requested details."

    anchored = make_handoff(body=body)
    unanchored = make_handoff(source_message_id=None, body=body)

    assert anchored.body == body
    assert anchored.source_message_id == "message-1"
    assert unanchored.source_message_id is None


def test_handoff_mark_read_returns_a_read_copy() -> None:
    handoff = make_handoff()

    assert handoff.pending is True
    read = handoff.mark_read(at_ms=1_010)

    assert read.pending is False
    assert read.read_at_ms == 1_010
    assert handoff.pending is True
    assert handoff.read_at_ms is None


def test_send_request_preserves_body_and_optional_anchor() -> None:
    body = "Read this source.\nContinue there."
    request = HandoffSendRequest(
        target="dm:user",
        body=body,
        command_id="command-1",
        created_at_ms=1_000,
        source_message_id=None,
    )

    assert request.body == body
    assert request.source_message_id is None


def test_check_request_uses_the_fixed_batch_limit() -> None:
    assert HandoffCheckRequest().limit == HANDOFF_CHECK_LIMIT


def test_send_and_check_results_preserve_service_output() -> None:
    pending = make_handoff()
    read = pending.mark_read(at_ms=1_010)
    item = HandoffCheckItem(handoff=read, source_target="group:source")

    assert HandoffSendResult(handoff=pending, target="dm:user").handoff is pending
    assert HandoffCheckResult(items=(item,), has_more=False).items == (item,)
    assert item.source_target == "group:source"
    assert item.handoff.read_at_ms == 1_010
