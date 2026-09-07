from __future__ import annotations

from pathlib import Path

import pytest
from bcn_test_support.audit import RecordingAudit
from bcn_test_support.channel import TestChannel
from bcn_test_support.storage import MemoryStorage

from bazaar_compute_node.core.actor import Actors, Agent, Mode
from bazaar_compute_node.core.concurrency import ThreadLockRegistry
from bazaar_compute_node.core.lifecycle import TimeoutBudget
from bazaar_compute_node.core.models import (
    ChannelSession,
    ChannelTargetKind,
    Message,
    MessageDirection,
    SenderIdentity,
    SenderKind,
    Thread,
)
from bazaar_compute_node.core.orchestration.command import CommandService
from bazaar_compute_node.core.orchestration.delivery import OutboundDeliveryService
from bazaar_compute_node.core.orchestration.services import AuditRecorder
from bazaar_compute_node.core.storage import (
    InboxTargetResolutionError,
    IStorageScope,
)


async def _seed_group_message(scope: IStorageScope, sender: SenderIdentity) -> None:
    channel_session = ChannelSession(
        id="channel-group",
        channel="test",
        provider_thread_id="test:group:1",
        created_at_ms=1,
        updated_at_ms=1,
        target_kind=ChannelTargetKind.GROUP,
    )
    thread = Thread(
        id="thread-group",
        channel_session_id="channel-group",
        workspace_id="agent-a",
        created_at_ms=1,
        updated_at_ms=1,
    )
    await scope.save_channel_session(channel_session)
    await scope.save_thread(thread)
    await scope.save_message(
        Message(
            direction=MessageDirection.INBOUND,
            seq=0,
            message_id="message-group",
            thread_id=thread.id,
            channel_session_id=channel_session.id,
            channel="test",
            provider_thread_id=channel_session.provider_thread_id,
            provider_message_id="provider-1",
            received_at_ms=1,
            sender=sender,
            message_type="text",
            target="group:channel-group",
            body="hello",
            target_kind=ChannelTargetKind.GROUP,
            metadata={"sender_kind": SenderKind.AGENT.value},
        )
    )


def _command_service(storage: MemoryStorage, channel: TestChannel) -> CommandService:
    scope = storage.scope("agent-a", "Agent A")
    return CommandService(
        actors=Actors(agent_id="agent-a", mode=Mode.DANGEROUS_INDIVIDUAL),
        delivery=OutboundDeliveryService(channel, timeout=1),
        storage=scope,
        audit=AuditRecorder(
            sink=RecordingAudit(),
            timeout_budget=TimeoutBudget(
                startup_seconds=1,
                provider_call_seconds=1,
                command_seconds=1,
                shutdown_seconds=1,
            ),
            clock=lambda: 1000,
        ),
        concurrency=ThreadLockRegistry(),
        workspace=lambda: Path("/tmp"),
        clock=lambda: 1000,
    )


@pytest.mark.asyncio
async def test_dm_target_is_minted_from_a_sender_seen_in_a_group() -> None:
    storage = MemoryStorage()
    await storage.start(timeout=1)
    channel = TestChannel()
    scope = storage.scope("agent-a", "Agent A")
    await _seed_group_message(scope, SenderIdentity(id="peer-1", name="kana"))

    service = _command_service(storage, channel)
    # No such DM exists yet; the address has to come from the sender of that
    # group message. Going through `send` keeps the wiring itself under test.
    await service.send(
        actor=Agent("agent-a"),
        command_id="command-1",
        raw_target="dm:@kana",
        body="hi",
        created_at_ms=1000,
    )

    target = await scope.resolve_inbox_target("dm:@kana")
    assert target.channel_session.id == "channel-dm-peer-1"
    assert target.channel_session.provider_thread_id == "test:dm:peer-1"
    assert target.channel_session.target_kind is ChannelTargetKind.DM
    assert target.channel_session.target_handle == "kana"
    assert target.thread.id == "thread-dm-peer-1"
    assert storage.channel_sessions["channel-dm-peer-1"].target_handle_key == "kana"


@pytest.mark.asyncio
async def test_unknown_dm_target_stays_not_found() -> None:
    storage = MemoryStorage()
    await storage.start(timeout=1)
    channel = TestChannel()
    service = _command_service(storage, channel)

    with pytest.raises(InboxTargetResolutionError):
        await service.send(
            actor=Agent("agent-a"),
            command_id="command-1",
            raw_target="dm:@nobody",
            body="hi",
            created_at_ms=1000,
        )
