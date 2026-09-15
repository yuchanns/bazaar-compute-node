from __future__ import annotations

import asyncio
from typing import cast

import pytest
from bcn_test_support import TestChannel, TestRuntime

from bazaar_compute_node.core.actor import Thread
from bazaar_compute_node.core.channel import (
    Channel,
    ChannelIdentity,
    Channels,
    ChannelSendRequest,
)
from bazaar_compute_node.core.concurrency import ThreadLockRegistry
from bazaar_compute_node.core.lifecycle import TimeoutBudget
from bazaar_compute_node.core.models import (
    ChannelTargetKind,
    Message,
    MessageDirection,
    RuntimeEventEnvelope,
    RuntimeOutputEvent,
    SenderIdentity,
    SenderKind,
    TurnCompleted,
)
from bazaar_compute_node.core.outcomes import ProviderCallResult, ProviderCallStatus
from bazaar_compute_node.core.runtime import (
    RuntimeExpire,
)


def test_channel_identity_requires_safe_provider_fields() -> None:
    assert ChannelIdentity(id="provider-id") == ChannelIdentity(id="provider-id")
    assert ChannelIdentity(id="provider-id", name="Provider Name").name == (
        "Provider Name"
    )

    with pytest.raises(ValueError, match="id must be non-empty"):
        ChannelIdentity(id="")
    with pytest.raises(ValueError, match="name must not contain line breaks"):
        ChannelIdentity(id="provider-id", name="Provider\nName")


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
            thread_id="oc_abc",
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
    local_session_id = received.thread_id
    assert local_session_id != "oc_abc"

    channel.accept_turn_event(
        RuntimeOutputEvent(
            envelope=RuntimeEventEnvelope(
                actor=Thread(local_session_id),
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

    assert provider.event_sessions[0] == "oc_abc"
    assert provider.send_attempts[0].session_id == "oc_abc"


@pytest.mark.asyncio
async def test_a_minted_dm_address_carries_the_same_namespace_as_a_received_one() -> (
    None
):
    provider = TestChannel()
    channel = Channel("agent-test", provider)
    await channel.start(timeout=1)
    peer = SenderIdentity(id="peer-1", name="Peer")

    address = channel.dm_address(peer, sender_kind=SenderKind.AGENT)
    provider_address = provider.dm_address(peer, sender_kind=SenderKind.AGENT)
    assert address is not None
    assert provider_address is not None
    assert address.thread_id != provider_address.thread_id
    assert address.channel_session_id != provider_address.channel_session_id

    await channel.send(
        ChannelSendRequest(
            session_id=address.thread_id,
            body="hello",
            attachments=(),
            target_kind=ChannelTargetKind.DM,
            provider_thread_id=address.provider_thread_id,
        ),
        timeout=1,
    )

    assert provider.send_attempts[0].session_id == provider_address.thread_id


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
    registry = ThreadLockRegistry()
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    second_entered = asyncio.Event()
    order: list[str] = []

    async def first_operation() -> None:
        async with registry.for_thread("session-1"):
            order.append("first-enter")
            first_entered.set()
            await release_first.wait()
            order.append("first-exit")

    async def second_operation() -> None:
        await first_entered.wait()
        async with registry.for_thread("session-1"):
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
    registry = ThreadLockRegistry()
    first_entered = asyncio.Event()
    second_entered = asyncio.Event()
    release_first = asyncio.Event()

    async def first_operation() -> None:
        async with registry.for_thread("session-1"):
            first_entered.set()
            await release_first.wait()

    async def second_operation() -> None:
        await first_entered.wait()
        async with registry.for_thread("session-2"):
            second_entered.set()

    first_task = asyncio.create_task(first_operation())
    second_task = asyncio.create_task(second_operation())
    await asyncio.wait_for(second_entered.wait(), timeout=0.1)
    release_first.set()
    await asyncio.gather(first_task, second_task)


class _OtherKindChannel(TestChannel):
    @property
    def name(self) -> str:
        return "other"


class _RefusingChannel(TestChannel):
    async def start(self, *, timeout: float) -> None:
        del timeout
        raise ConnectionError("provider refused the token")


@pytest.mark.asyncio
async def test_channels_keeps_going_when_one_member_fails_to_start() -> None:
    refusing = _RefusingChannel()
    serving = _OtherKindChannel()
    serving.identity = ChannelIdentity(id="bot-other")
    channels = Channels((refusing, serving))

    assert channels.name == "test,other"
    assert channels.members == (refusing, serving)
    await channels.start(timeout=1)
    try:
        # the failure is visible per member, next to the ones that came up
        health = channels.health
        assert health["state"] == "degraded"
        failed, serving_record = cast(tuple[dict[str, object], ...], health["channels"])
        assert failed["startup_error"] == "ConnectionError: provider refused the token"
        assert serving_record["identity"] == "bot-other"
        assert "startup_error" not in serving_record
        # a member that is up but unwell is what the whole reports
        serving.accepting = False
        assert channels.health["state"] == "degraded"
        serving.accepting = True
        # the member that is up answers for the whole
        assert channels.get_identity() == serving.identity
        await serving.inject(
            Message(
                direction=MessageDirection.INBOUND,
                seq=1,
                message_id="message-1",
                thread_id="thread-1",
                channel_session_id="channel-1",
                channel="other",
                provider_thread_id="other:thread-1",
                provider_message_id="provider-1",
                received_at_ms=1,
                sender=SenderIdentity(id="sender-id"),
                target="dm:channel-1",
                body="hello",
            )
        )
        received = await anext(channels.receive())
        assert received.message_id == "message-1"
    finally:
        await channels.stop(timeout=1)
    assert serving.stopped is True
    assert refusing.stopped is False


@pytest.mark.asyncio
async def test_channels_with_no_member_up_does_not_start() -> None:
    channels = Channels((_RefusingChannel(), _RefusingChannel()))

    with pytest.raises(RuntimeError, match="no channel started"):
        await channels.start(timeout=1)


@pytest.mark.asyncio
async def test_channels_refuses_two_bots_of_one_kind_sharing_an_identity() -> None:
    first = TestChannel()
    first.identity = ChannelIdentity(id="bot-1")
    twin = TestChannel()
    twin.identity = ChannelIdentity(id="bot-1")
    channels = Channels((first, twin))

    with pytest.raises(ValueError, match="identity bot-1 is configured twice"):
        await channels.start(timeout=1)
    assert first.stopped is True
    assert twin.stopped is True


@pytest.mark.asyncio
async def test_channels_refuses_a_member_that_does_not_know_its_bot() -> None:
    named = TestChannel()
    nameless = _OtherKindChannel()
    nameless.identity = None
    channels = Channels((named, nameless))

    with pytest.raises(RuntimeError, match="other channel #2 has no identity"):
        await channels.start(timeout=1)
    assert named.stopped is True
    assert nameless.stopped is True


def test_channels_needs_at_least_one_member() -> None:
    with pytest.raises(ValueError, match="at least one channel"):
        Channels(())
