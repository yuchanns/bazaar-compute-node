from __future__ import annotations

import asyncio

import pytest
from bcn_test_support import TestChannel, TestRuntime

from bazaar_compute_node.core.channel import (
    Channel,
    ChannelIdentity,
    ChannelSendRequest,
)
from bazaar_compute_node.core.concurrency import SessionLockRegistry
from bazaar_compute_node.core.lifecycle import TimeoutBudget
from bazaar_compute_node.core.models import (
    ChannelTargetKind,
    Message,
    MessageDirection,
    RuntimeEventEnvelope,
    RuntimeOutputEvent,
    SenderIdentity,
    TurnCompleted,
    TurnFailed,
)
from bazaar_compute_node.core.outcomes import ProviderCallResult, ProviderCallStatus
from bazaar_compute_node.core.runtime import (
    RuntimeExpire,
)


def test_channel_identity_requires_one_safe_provider_field() -> None:
    assert ChannelIdentity(id="provider-id") == ChannelIdentity(id="provider-id")
    assert ChannelIdentity(name="Provider Name").name == "Provider Name"

    with pytest.raises(ValueError, match="requires an id or name"):
        ChannelIdentity()
    with pytest.raises(ValueError, match="id must be non-empty"):
        ChannelIdentity(id="")
    with pytest.raises(ValueError, match="name must not contain line breaks"):
        ChannelIdentity(name="Provider\nName")


@pytest.mark.asyncio
async def test_channel_delegates_identity_during_lifecycle() -> None:
    provider = TestChannel()
    channel = Channel("agent-test", provider)
    provider.identity = ChannelIdentity(id="provider-id", name="Provider Name")

    assert channel.get_identity() is None
    await channel.start(timeout=1)
    try:
        assert channel.get_identity() == provider.identity
    finally:
        await channel.stop(timeout=1)
    assert channel.get_identity() is None


def test_channel_redacts_terminal_error_secrets() -> None:
    provider = TestChannel()
    channel = Channel(
        "agent-test",
        provider,
        redact=lambda session_id, text: text.replace(f"token-{session_id}", "<hidden>"),
    )
    envelope = RuntimeEventEnvelope(
        session_id="bcn-1",
        runtime_session_id="runtime-1",
        turn_id="turn-1",
        provider_turn_id=None,
        occurred_at_ms=1,
    )

    channel.accept_turn_event(
        RuntimeOutputEvent(
            envelope=envelope,
            payload=TurnFailed(
                event_name="bcn.turn.failed",
                error_kind="provider_failed",
                error_message="auth failed for token-bcn-1",
            ),
        ),
        session_id="bcn-1",
    )
    channel.accept_turn_event(
        RuntimeOutputEvent(
            envelope=envelope,
            payload=TurnCompleted(event_name="bcn.turn.completed"),
        ),
        session_id="bcn-1",
    )

    failed = provider.events[0].payload
    assert isinstance(failed, TurnFailed)
    assert failed.error_message == "auth failed for <hidden>"
    assert isinstance(provider.events[1].payload, TurnCompleted)


@pytest.mark.asyncio
async def test_channel_sends_and_streams_under_one_session_namespace() -> None:
    provider = TestChannel()
    channel = Channel("agent-test", provider)
    await channel.start(timeout=1)
    await provider.inject(
        Message(
            direction=MessageDirection.INBOUND,
            seq=1,
            message_id="00000000-0000-4000-8000-000000000001",
            session_id="oc_abc",
            channel_session_id="oc_abc",
            channel="test",
            provider_thread_id="user-id",
            provider_message_id="provider-1",
            received_at_ms=1,
            sender=SenderIdentity(id="sender-id", name="Sender"),
            target="dm:oc_abc",
            target_kind=ChannelTargetKind.DM,
            body="hello",
        )
    )

    received = await anext(channel.receive())
    local_session_id = received.session_id
    assert local_session_id != "oc_abc"

    channel.accept_turn_event(
        RuntimeOutputEvent(
            envelope=RuntimeEventEnvelope(
                session_id=local_session_id,
                runtime_session_id="runtime-1",
                turn_id="turn-1",
                provider_turn_id=None,
                occurred_at_ms=1,
            ),
            payload=TurnCompleted(event_name="bcn.turn.completed"),
        ),
        session_id=local_session_id,
    )
    await channel.send(
        ChannelSendRequest(
            session_id=local_session_id,
            body="done",
            attachments=(),
            target_kind=ChannelTargetKind.DM,
            provider_thread_id="user-id",
        ),
        timeout=1,
    )

    assert provider.events[0].envelope.session_id == "oc_abc"
    assert provider.send_attempts[0].session_id == "oc_abc"


def test_timeout_budget_requires_finite_positive_boundaries() -> None:
    budget = TimeoutBudget(
        startup_seconds=1,
        provider_call_seconds=2,
        command_seconds=3,
        shutdown_seconds=4,
    )

    assert budget.command_seconds == 3
    with pytest.raises(ValueError, match="provider_call_seconds"):
        TimeoutBudget(
            startup_seconds=1,
            provider_call_seconds=0,
            command_seconds=3,
            shutdown_seconds=4,
        )


def test_provider_result_requires_explicit_unknown_or_failure_reason() -> None:
    confirmed = ProviderCallResult(
        status=ProviderCallStatus.CONFIRMED,
        value="receipt",
    )
    assert confirmed.value == "receipt"

    queued = ProviderCallResult(
        status=ProviderCallStatus.QUEUED,
        value="queue-receipt",
    )
    assert queued.status is ProviderCallStatus.QUEUED

    partial = ProviderCallResult(
        status=ProviderCallStatus.PARTIAL,
        value="receipt-1",
        error_kind="provider_rejected_batch",
    )
    assert partial.value == "receipt-1"

    unknown = ProviderCallResult(
        status=ProviderCallStatus.UNKNOWN,
        error_kind="transport_eof",
    )
    assert unknown.status is ProviderCallStatus.UNKNOWN
    with pytest.raises(ValueError, match="error_kind"):
        ProviderCallResult(status=ProviderCallStatus.FAILED)
    with pytest.raises(ValueError, match="requires a value"):
        ProviderCallResult(
            status=ProviderCallStatus.PARTIAL,
            error_kind="provider_rejected_batch",
        )


@pytest.mark.asyncio
async def test_test_runtime_delivers_expiry_to_one_waiter() -> None:
    runtime = TestRuntime()
    await runtime.start(timeout=1)
    receiver = asyncio.create_task(runtime.receive_event())

    runtime.emit_expire("runtime-1")

    assert await receiver == RuntimeExpire("runtime-1")
    cancelled_receiver = asyncio.create_task(runtime.receive_event())
    await asyncio.sleep(0)
    cancelled_receiver.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled_receiver
    await runtime.stop(timeout=1)


@pytest.mark.asyncio
async def test_same_session_operations_share_one_serial_lock() -> None:
    registry = SessionLockRegistry()
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    second_entered = asyncio.Event()
    order: list[str] = []

    async def first_operation() -> None:
        async with registry.for_session("session-1"):
            order.append("first-enter")
            first_entered.set()
            await release_first.wait()
            order.append("first-exit")

    async def second_operation() -> None:
        await first_entered.wait()
        async with registry.for_session("session-1"):
            order.append("second-enter")
            second_entered.set()

    first_task = asyncio.create_task(first_operation())
    second_task = asyncio.create_task(second_operation())
    await first_entered.wait()
    await asyncio.sleep(0)
    assert not second_entered.is_set()

    release_first.set()
    await asyncio.gather(first_task, second_task)
    assert order == ["first-enter", "first-exit", "second-enter"]


@pytest.mark.asyncio
async def test_different_sessions_do_not_share_the_lock() -> None:
    registry = SessionLockRegistry()
    first_entered = asyncio.Event()
    second_entered = asyncio.Event()
    release_first = asyncio.Event()

    async def first_operation() -> None:
        async with registry.for_session("session-1"):
            first_entered.set()
            await release_first.wait()

    async def second_operation() -> None:
        await first_entered.wait()
        async with registry.for_session("session-2"):
            second_entered.set()

    first_task = asyncio.create_task(first_operation())
    second_task = asyncio.create_task(second_operation())
    await asyncio.wait_for(second_entered.wait(), timeout=0.1)
    release_first.set()
    await asyncio.gather(first_task, second_task)
