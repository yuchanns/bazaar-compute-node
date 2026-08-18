from __future__ import annotations

from collections.abc import Callable

import pytest
from bcn_test_support import RecordingAudit, TestChannel

from bazaar_compute_node.core.channel import ChannelDeliveryReceipt
from bazaar_compute_node.core.lifecycle import TimeoutBudget
from bazaar_compute_node.core.models import (
    ChannelTargetKind,
    InboundMessage,
    RuntimeTurn,
    RuntimeTurnState,
    SenderIdentity,
)
from bazaar_compute_node.core.orchestration.delivery import OutboundDeliveryService
from bazaar_compute_node.core.orchestration.error_feedback import RuntimeErrorReporter
from bazaar_compute_node.core.orchestration.services import SessionAuditRecorder
from bazaar_compute_node.core.outcomes import ProviderCallResult, ProviderCallStatus
from bazaar_compute_node.i18n import ENGLISH, SIMPLIFIED_CHINESE, create_translator


def make_message() -> InboundMessage:
    return InboundMessage(
        seq=7,
        message_id="message-7",
        session_id="bcn-1",
        channel_session_id="channel-1",
        channel="test",
        provider_thread_id="thread-1",
        provider_message_id="provider-message-7",
        received_at_ms=1,
        sender=SenderIdentity(id="sender-1", name="Sender"),
        message_type="text",
        canonical_target="group:channel-1",
        body="Run the task",
        target_kind=ChannelTargetKind.GROUP,
    )


def make_turn(
    state: RuntimeTurnState,
    *,
    error_kind: str | None = None,
    error_message: str | None = None,
) -> RuntimeTurn:
    return RuntimeTurn(
        turn_id="turn-1",
        session_id="runtime-1",
        state=state,
        started_at_ms=1,
        completed_at_ms=2,
        error_kind=error_kind,
        error_message=error_message,
    )


def make_reporter(
    channel: TestChannel,
    audit: RecordingAudit,
    *,
    language: str = ENGLISH,
    detail: Callable[[str, str], str] = lambda _session_id, text: text,
) -> RuntimeErrorReporter:
    budget = TimeoutBudget(
        startup_seconds=1,
        provider_call_seconds=1,
        command_seconds=1,
        shutdown_seconds=1,
    )
    return RuntimeErrorReporter(
        agent_id="agent-1",
        delivery=OutboundDeliveryService(channel, timeout=1),
        audit=SessionAuditRecorder(sink=audit, timeout_budget=budget, clock=lambda: 3),
        translator=create_translator(language),
        detail=detail,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "language", "expected_body"),
    (
        (
            RuntimeTurnState.FAILED,
            ENGLISH,
            "Execution failed: request contains <redacted>",
        ),
        (
            RuntimeTurnState.UNKNOWN,
            SIMPLIFIED_CHINESE,
            "执行状态未知：request contains <redacted>",
        ),
    ),
)
async def test_reporter_delivers_localized_terminal_error_detail(
    state: RuntimeTurnState,
    language: str,
    expected_body: str,
) -> None:
    channel = TestChannel()
    audit = RecordingAudit()
    await channel.start(timeout=1)
    reporter = make_reporter(
        channel,
        audit,
        language=language,
        detail=lambda _session_id, text: text.replace("secret-token", "<redacted>"),
    )

    await reporter.report(
        make_message(),
        make_turn(state, error_message="request contains secret-token"),
    )

    assert len(channel.send_attempts) == 1
    request = channel.send_attempts[0]
    assert request.body == expected_body
    assert request.session_id == "bcn-1"
    assert request.attachments == ()
    assert request.target_kind is ChannelTargetKind.GROUP
    assert request.provider_thread_id == "thread-1"
    assert request.provider_reply_to_message_id == "provider-message-7"
    assert [event.event_name for event in audit.events] == [
        "runtime.error_feedback.started",
        "runtime.error_feedback.sent",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "turn",
    (
        None,
        make_turn(RuntimeTurnState.COMPLETED),
        make_turn(RuntimeTurnState.CANCELLED, error_kind="cancelled"),
    ),
)
async def test_reporter_skips_non_error_turns(turn: RuntimeTurn | None) -> None:
    channel = TestChannel()
    audit = RecordingAudit()
    reporter = make_reporter(channel, audit)

    await reporter.report(make_message(), turn)

    assert channel.send_attempts == []
    assert audit.events == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider_result",
    (
        ProviderCallResult[ChannelDeliveryReceipt](
            status=ProviderCallStatus.FAILED,
            error_kind="provider_failed",
            error_message="delivery failed",
            receipt={"provider_receipt_ref": "failed-1"},
        ),
        ProviderCallResult[ChannelDeliveryReceipt](
            status=ProviderCallStatus.PARTIAL,
            value=ChannelDeliveryReceipt(provider_receipt_ref="partial-1"),
            error_kind="provider_partial",
            error_message="delivery was partial",
        ),
        ProviderCallResult[ChannelDeliveryReceipt](
            status=ProviderCallStatus.UNKNOWN,
            error_kind="provider_unknown",
            error_message="delivery is unknown",
            receipt={"provider_receipt_ref": "unknown-1"},
        ),
    ),
)
async def test_reporter_audits_unconfirmed_delivery_without_retry(
    provider_result: ProviderCallResult[ChannelDeliveryReceipt],
) -> None:
    channel = TestChannel()
    audit = RecordingAudit()
    await channel.start(timeout=1)
    channel.queue_send_result(provider_result)
    reporter = make_reporter(channel, audit)

    await reporter.report(
        make_message(),
        make_turn(
            RuntimeTurnState.FAILED,
            error_kind="provider_failed",
            error_message="runtime failed",
        ),
    )

    assert len(channel.send_attempts) == 1
    assert [event.event_name for event in audit.events] == [
        "runtime.error_feedback.started",
        "runtime.error_feedback.failed",
    ]
    assert audit.events[-1].metadata["delivery_state"] == provider_result.status.value


@pytest.mark.asyncio
async def test_reporter_uses_error_kind_when_terminal_detail_is_missing() -> None:
    channel = TestChannel()
    audit = RecordingAudit()
    await channel.start(timeout=1)
    reporter = make_reporter(channel, audit)

    await reporter.report(
        make_message(),
        make_turn(RuntimeTurnState.UNKNOWN, error_kind="provider_unknown"),
    )

    assert channel.send_attempts[0].body == (
        "Execution status is unknown: provider_unknown"
    )
