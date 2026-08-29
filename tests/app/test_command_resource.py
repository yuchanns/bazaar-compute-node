from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from bazaar_compute_node.app.application import NodeApplication
from bazaar_compute_node.app.command import serialize_inbox_target
from bazaar_compute_node.app.config import (
    AgentConfiguration,
    ChannelConfiguration,
    NodeConfiguration,
    RuntimeConfiguration,
)
from bazaar_compute_node.app.registry import AdapterRegistry
from bazaar_compute_node.app.resource_dispatch import CommandDispatcher
from bazaar_compute_node.core.command import (
    ICommandService,
    IReminderService,
    MessageSendFreshnessHold,
    MessageSendSuccess,
)
from bazaar_compute_node.core.lifecycle import TimeoutBudget
from bazaar_compute_node.core.models import (
    ChannelTargetKind,
    InboxTargetSummary,
    Message,
    MessageDirection,
    OutboundDeliveryState,
)

AGENT_ID = "0198d4e6-29c5-7465-b74b-88db31f0c118"


def make_configuration() -> NodeConfiguration:
    return NodeConfiguration(
        version_check=False,
        storage="sqlite",
        audit="test",
        agents=(
            AgentConfiguration(
                id=AGENT_ID,
                name="Test Agent",
                channel=ChannelConfiguration(kind="test"),
                runtimes=(RuntimeConfiguration(kind="test"),),
            ),
        ),
    )


def make_budget() -> TimeoutBudget:
    return TimeoutBudget(
        startup_seconds=2,
        provider_call_seconds=2,
        command_seconds=2,
        shutdown_seconds=2,
    )


def test_inbox_target_serializer_selects_one_latest_time() -> None:
    summary = InboxTargetSummary(
        target="dm:user-1",
        session_id="session-1",
        target_kind=ChannelTargetKind.DM,
        current=True,
        pending_count=0,
        last_activity_at_ms=100,
        latest_message_id="message-1",
        latest_provider_time_ms=99,
        latest_received_at_ms=100,
    )

    result = serialize_inbox_target(summary)

    assert result["latest_time_ms"] == 99


@pytest.mark.asyncio
async def test_command_dispatch_requires_resource_and_rejects_collisions(
    tmp_path: Path,
) -> None:
    shared_factories = AdapterRegistry().load_shared(storage="sqlite", audit="test")
    node = NodeApplication(
        configuration=make_configuration(),
        shared_factories=shared_factories,
        endpoint_path=tmp_path / "bcn.sock",
        timeout_budget=make_budget(),
    )
    await node.start()
    try:
        dispatcher = node.agents[AGENT_ID].command_dispatcher
        missing_resource = await dispatcher(
            {
                "kind": "command",
                "command": "check",
                "session_id": "bcn-a",
            }
        )
        assert missing_resource["ok"] is False
        assert missing_resource["code"] == "RESOURCE_REQUIRED"

        message_collision = await dispatcher(
            {
                "kind": "command",
                "resource": "message",
                "command": "unfollow",
                "session_id": "bcn-a",
            }
        )
        assert message_collision["ok"] is False
        assert message_collision["code"] == "UNKNOWN_COMMAND"

        thread_collision = await dispatcher(
            {
                "kind": "command",
                "resource": "thread",
                "command": "check",
                "session_id": "bcn-a",
            }
        )
        assert thread_collision["ok"] is False
        assert thread_collision["code"] == "UNKNOWN_COMMAND"

        inbox_collision = await dispatcher(
            {
                "kind": "command",
                "resource": "inbox",
                "command": "check",
                "session_id": "bcn-a",
            }
        )
        assert inbox_collision["ok"] is False
        assert inbox_collision["code"] == "UNKNOWN_COMMAND"

        unknown_resource = await dispatcher(
            {
                "kind": "command",
                "resource": "unknown",
                "command": "list",
                "session_id": "bcn-a",
            }
        )
        assert unknown_resource["ok"] is False
        assert unknown_resource["code"] == "UNKNOWN_RESOURCE"
    finally:
        await node.stop()


@pytest.mark.asyncio
async def test_message_send_renders_freshness_hold() -> None:
    message = Message(
        direction=MessageDirection.INBOUND,
        seq=7,
        message_id="message-7",
        session_id="session-source",
        channel_session_id="channel-source",
        channel="test",
        provider_thread_id="provider-thread",
        provider_message_id="provider-message",
        received_at_ms=1_000,
        sender=None,
        target="dm:source",
        message_type="text",
        body="new context",
    )
    service = SimpleNamespace(
        send=AsyncMock(
            return_value=MessageSendFreshnessHold(
                target="dm:source",
                messages=(message,),
                referenced_messages=(),
                newer_message_total=1,
                snapshot_seq=6,
                current_inbound_seq=7,
                draft_replaced=False,
            )
        )
    )
    dispatcher = CommandDispatcher(
        cast(ICommandService, service),
        reminder_service=cast(IReminderService, object()),
        timeout_budget=make_budget(),
    )
    dispatcher.start_accepting()
    request = {
        "kind": "command",
        "resource": "message",
        "command": "send",
        "session_id": "session-source",
        "target": "dm:source",
        "body": "Hello.",
        "command_id": "message-command-1",
        "created_at_ms": 1_100,
    }

    freshness = await dispatcher(request)
    assert freshness["ok"] is True
    freshness_result = cast(Mapping[str, object], freshness["result"])
    freshness_text = cast(str, freshness_result["text"])
    assert freshness_text.startswith(
        "Unreviewed synced context for this target: 1 message."
    )
    assert "[1/1 seq=7 msg=message-7" in freshness_text
    assert "End of window: 1/1 shown." in freshness_text
    assert 'bcc message send --send-draft --target "dm:source"' in freshness_text
    assert set(service.send.await_args.kwargs) == {
        "session_id",
        "command_id",
        "raw_target",
        "body",
        "created_at_ms",
        "attachment_paths",
        "reply_to_message_id",
        "send_draft",
    }


@pytest.mark.asyncio
async def test_message_send_renders_provider_outcomes() -> None:
    service = SimpleNamespace(send=AsyncMock())
    dispatcher = CommandDispatcher(
        cast(ICommandService, service),
        reminder_service=cast(IReminderService, object()),
        timeout_budget=make_budget(),
    )
    dispatcher.start_accepting()
    request = {
        "kind": "command",
        "resource": "message",
        "command": "send",
        "session_id": "session-source",
        "target": "dm:source",
        "body": "Hello.",
        "command_id": "message-command-1",
        "created_at_ms": 1_100,
    }
    outcomes = (
        (
            OutboundDeliveryState.QUEUED,
            True,
            None,
            "Message queued to dm:source. Message ID: outbound-1",
        ),
        (
            OutboundDeliveryState.PARTIAL,
            False,
            "SEND_PARTIAL",
            "Do not retry the complete message automatically",
        ),
        (
            OutboundDeliveryState.UNKNOWN,
            False,
            "SEND_UNKNOWN",
            "Reconcile channel delivery",
        ),
        (
            OutboundDeliveryState.FAILED,
            False,
            "SEND_FAILED",
            "Fix the provider error",
        ),
    )

    for state, ok, code, expected_text in outcomes:
        service.send.return_value = MessageSendSuccess(
            message=cast(
                Message,
                SimpleNamespace(
                    delivery_state=state,
                    message_id="outbound-1",
                    error_message="provider outcome",
                ),
            ),
            target="dm:source",
        )
        response = await dispatcher(request)

        assert response["ok"] is ok
        if ok:
            result = cast(Mapping[str, object], response["result"])
            assert result["text"] == expected_text
        else:
            assert response["code"] == code
            assert expected_text in cast(str, response["next_action"])
