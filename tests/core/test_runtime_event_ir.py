from __future__ import annotations

from pathlib import Path

import pytest
from bcn_test_support import (
    MemoryStorage,
    RecordingAudit,
    TestChannel,
    TestRuntime,
    TestTurnPlan,
)

from bazaar_compute_node.core.actor import Actors, Mode, Thread
from bazaar_compute_node.core.lifecycle import TimeoutBudget
from bazaar_compute_node.core.models import (
    Message,
    MessageDirection,
    RuntimeEventEnvelope,
    RuntimeEventState,
    RuntimeOutputEvent,
    RuntimeTurnState,
    SenderIdentity,
    TurnCancelled,
    TurnCompleted,
    TurnFailed,
    TurnStarted,
    TurnUnknown,
)
from bazaar_compute_node.core.orchestration import AgentOrchestrator
from bazaar_compute_node.core.orchestration.turn import _with_a_reason
from bazaar_compute_node.core.timerwheel import TimerWheel
from bazaar_compute_node.i18n import ENGLISH, create_translator


def _message() -> Message:
    return Message(
        direction=MessageDirection.INBOUND,
        seq=1,
        message_id="message-1",
        thread_id="session-1",
        channel_session_id="channel-session-1",
        channel="test",
        provider_thread_id="provider-thread-1",
        provider_message_id="provider-message-1",
        received_at_ms=1,
        sender=SenderIdentity(id="sender-1", name="Sender"),
        message_type="text",
        target="dm:channel-session-1",
        body="run the task",
        metadata={"sender_kind": "human"},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("terminal_state", "terminal_type", "turn_state"),
    (
        (RuntimeEventState.COMPLETED, TurnCompleted, RuntimeTurnState.COMPLETED),
        (RuntimeEventState.FAILED, TurnFailed, RuntimeTurnState.FAILED),
        (RuntimeEventState.CANCELLED, TurnCancelled, RuntimeTurnState.CANCELLED),
        (RuntimeEventState.UNKNOWN, TurnUnknown, RuntimeTurnState.UNKNOWN),
    ),
)
async def test_turn_payloads_are_audited_forwarded_and_correlated(
    terminal_state: RuntimeEventState,
    terminal_type: type[TurnCompleted | TurnFailed | TurnCancelled | TurnUnknown],
    turn_state: RuntimeTurnState,
) -> None:
    channel = TestChannel()
    runtime = TestRuntime()
    storage = MemoryStorage()
    audit = RecordingAudit()
    await storage.start(timeout=1)
    orchestrator = AgentOrchestrator(
        actors=Actors(agent_id="agent-1", mode=Mode.SESSION),
        channel=channel,
        runtimes=(runtime,),
        storage=storage.scope("agent-1", "Test Agent"),
        audit=audit,
        timeout_budget=TimeoutBudget(
            startup_seconds=1,
            provider_call_seconds=1,
            command_seconds=1,
            shutdown_seconds=1,
        ),
        timer_wheel=TimerWheel(),
        workspace=Path.cwd,
        translator=create_translator(ENGLISH),
        error_feedback_detail=lambda _, text: text,
    )
    runtime.queue_turn_plan(
        TestTurnPlan(
            states=(RuntimeEventState.STARTED, terminal_state),
            terminal_metadata={
                "usage": {"input_tokens": 3, "output_tokens": 5},
                "stop_reason": "end_turn",
            },
        )
    )
    await orchestrator.start(timeout=1)
    try:
        turn = await orchestrator.handle_inbound(_message())

        assert turn is not None
        assert turn.state is turn_state
        assert turn.provider_turn_id == f"test-provider-{turn.turn_id}"
        assert isinstance(channel.events[0].payload, TurnStarted)
        assert isinstance(channel.events[-1].payload, terminal_type)
        runtime_audit = next(
            event
            for event in audit.events
            if event.event_name == f"runtime.turn.{terminal_state.value}"
            and event.correlation.turn_id == turn.turn_id
            and event.metadata
        )
        assert runtime_audit.state is terminal_state
        assert runtime_audit.metadata == {
            "usage": {"input_tokens": 3, "output_tokens": 5},
            "stop_reason": "end_turn",
        }
    finally:
        await orchestrator.stop(timeout=1)
        await storage.stop(timeout=1)


@pytest.mark.asyncio
async def test_synthesized_terminal_reaches_the_channel() -> None:
    channel = TestChannel()
    runtime = TestRuntime()
    storage = MemoryStorage()
    audit = RecordingAudit()
    await storage.start(timeout=1)
    orchestrator = AgentOrchestrator(
        actors=Actors(agent_id="agent-1", mode=Mode.SESSION),
        channel=channel,
        runtimes=(runtime,),
        storage=storage.scope("agent-1", "Test Agent"),
        audit=audit,
        timeout_budget=TimeoutBudget(
            startup_seconds=1,
            provider_call_seconds=1,
            command_seconds=1,
            shutdown_seconds=1,
        ),
        timer_wheel=TimerWheel(),
        workspace=Path.cwd,
        translator=create_translator(ENGLISH),
        error_feedback_detail=lambda _, text: text,
    )
    runtime.queue_turn_plan(TestTurnPlan(states=(RuntimeEventState.STARTED,)))
    await orchestrator.start(timeout=1)
    try:
        turn = await orchestrator.handle_inbound(_message())

        assert turn is not None
        assert turn.state is RuntimeTurnState.UNKNOWN
        terminal = channel.events[-1].payload
        assert isinstance(terminal, TurnUnknown)
        assert "turn" in terminal.event_name.casefold()
        assert channel.events[-1].envelope.turn_id == turn.turn_id
    finally:
        await orchestrator.stop(timeout=1)
        await storage.stop(timeout=1)


def test_core_names_a_failure_the_runtime_left_unexplained() -> None:
    # a provider can end a turn as failed without an error object; which runtime
    # it was is ours to know, so the reason comes from core, not the adapter
    envelope = RuntimeEventEnvelope(
        actor=Thread("bcn-1"),
        runtime_session_id="runtime-1",
        turn_id="turn-1",
        occurred_at_ms=1,
        provider_turn_id=None,
    )

    def reason(error_message: str | None) -> str | None:
        payload = _with_a_reason(
            RuntimeOutputEvent(
                envelope=envelope,
                payload=TurnFailed(
                    event_name="bcn.turn.failed",
                    error_kind="provider_failed",
                    error_message=error_message,
                ),
            )
        ).payload
        assert isinstance(payload, TurnFailed)
        return payload.error_message

    assert reason(None) == "Turn failed"
    assert reason("disk is full") == "disk is full"

    # an unknown ending is not a failure: the provider may yet have finished
    unknown = _with_a_reason(
        RuntimeOutputEvent(
            envelope=envelope,
            payload=TurnUnknown(
                event_name="bcn.turn.unknown", error_kind="provider_unknown"
            ),
        )
    ).payload
    assert isinstance(unknown, TurnUnknown)
    assert unknown.error_message == "Turn outcome is unknown"
