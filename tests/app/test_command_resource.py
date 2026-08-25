from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from bazaar_compute_node.app.application import NodeApplication
from bazaar_compute_node.app.command import serialize_inbox_target, serialize_message
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
    IHandoffService,
    IReminderService,
    MessageSendFreshnessHold,
    MessageSendHandoffRequired,
)
from bazaar_compute_node.core.handoff import (
    HandoffCheckItem,
    HandoffCheckResult,
    HandoffSendResult,
)
from bazaar_compute_node.core.lifecycle import TimeoutBudget
from bazaar_compute_node.core.models import (
    ChannelTargetKind,
    Handoff,
    InboxTargetSummary,
    Message,
    MessageDirection,
    OutboundDeliveryState,
    SenderIdentity,
    SenderKind,
    SystemMessageKind,
)

AGENT_ID = "0198d4e6-29c5-7465-b74b-88db31f0c118"
SOURCE_MESSAGE_ID = "019d2f00-0000-7000-8000-000000000001"


def make_configuration() -> NodeConfiguration:
    return NodeConfiguration(
        storage="sqlite",
        audit="test",
        agents=(
            AgentConfiguration(
                id=AGENT_ID,
                name="Test Agent",
                channel=ChannelConfiguration(kind="test"),
                runtime=RuntimeConfiguration(kind="test"),
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


@pytest.mark.parametrize(
    ("kind", "source_target", "source_message_id"),
    (
        (SystemMessageKind.REMINDER, None, None),
        (SystemMessageKind.HANDOFF, "dm:source", "source-message-1"),
    ),
)
def test_message_serializer_projects_typed_system_formatter_fields(
    kind: SystemMessageKind,
    source_target: str | None,
    source_message_id: str | None,
) -> None:
    metadata: dict[str, object] = {
        "sender_kind": SenderKind.SYSTEM.value,
        "system_message_kind": kind.value,
    }
    if source_target is not None and source_message_id is not None:
        metadata.update(
            system_message_source_target=source_target,
            system_message_source_message_id=source_message_id,
        )
    message = Message(
        direction=MessageDirection.INBOUND,
        seq=1,
        message_id="system-message-1",
        session_id="session-1",
        channel_session_id="channel-1",
        channel="test",
        provider_thread_id="thread-1",
        provider_message_id=None,
        received_at_ms=1,
        sender=SenderIdentity(id="system", name="system"),
        target="dm:user-1",
        body="system event",
        metadata=metadata,
    )

    payload = serialize_message(message)

    assert payload["system_message_kind"] == kind.value
    assert payload["system_message_source_target"] == source_target
    assert payload["system_message_source_message_id"] == source_message_id


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
async def test_handoff_routes_validate_binding_and_serialize_results() -> None:
    bindings: list[str] = []

    async def validate_binding(
        session_id: str,
        raw_request: Mapping[str, object],
    ) -> None:
        del raw_request
        bindings.append(session_id)

    handoff = Handoff(
        handoff_id="handoff-1",
        command_id="command-1",
        source_session_id="session-source",
        target_session_id="session-target",
        source_message_id=SOURCE_MESSAGE_ID,
        body="Continue task.",
        created_at_ms=1_000,
    )
    handoff_service = SimpleNamespace(
        send=AsyncMock(
            return_value=HandoffSendResult(handoff=handoff, target="dm:target")
        ),
        check=AsyncMock(
            return_value=HandoffCheckResult(
                items=(
                    HandoffCheckItem(
                        handoff=handoff.mark_read(at_ms=2_000),
                        source_target="group:source",
                    ),
                ),
                has_more=False,
            )
        ),
    )
    dispatcher = CommandDispatcher(
        cast(ICommandService, object()),
        reminder_service=cast(IReminderService, object()),
        handoff_service=cast(IHandoffService, handoff_service),
        timeout_budget=make_budget(),
        session_binding_validator=validate_binding,
    )
    dispatcher.start_accepting()

    sent = await dispatcher(
        {
            "kind": "command",
            "resource": "handoff",
            "command": "send",
            "session_id": "session-source",
            "target": "dm:target",
            "body": "Continue task.",
            "command_id": "command-1",
            "source_message_id": SOURCE_MESSAGE_ID,
            "created_at_ms": 1_000,
        }
    )
    checked = await dispatcher(
        {
            "kind": "command",
            "resource": "handoff",
            "command": "check",
            "session_id": "session-target",
        }
    )

    assert sent["ok"] is True
    sent_result = cast(Mapping[str, object], sent["result"])
    assert sent_result["target"] == "dm:target"
    assert checked["ok"] is True
    result = cast(Mapping[str, object], checked["result"])
    items = cast(list[Mapping[str, object]], result["items"])
    assert items[0]["source_target"] == "group:source"
    assert bindings == ["session-source", "session-target"]


@pytest.mark.asyncio
async def test_message_send_renders_freshness_and_cross_session_holds() -> None:
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
            side_effect=(
                MessageSendFreshnessHold(
                    target="dm:source",
                    messages=(message,),
                    referenced_messages=(),
                    newer_message_total=1,
                    snapshot_seq=6,
                    current_inbound_seq=7,
                    draft_replaced=False,
                ),
                MessageSendHandoffRequired(target="dm:target"),
            )
        )
    )
    dispatcher = CommandDispatcher(
        cast(ICommandService, service),
        reminder_service=cast(IReminderService, object()),
        handoff_service=cast(IHandoffService, object()),
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
    cross_session = await dispatcher(
        {
            **request,
            "target": "dm:target",
            "body": "",
            "send_draft": True,
        }
    )

    assert freshness["ok"] is True
    freshness_result = cast(Mapping[str, object], freshness["result"])
    freshness_text = cast(str, freshness_result["text"])
    assert freshness_text.startswith(
        "Unreviewed synced context for this target: 1 message."
    )
    assert "[1/1 seq=7 msg=message-7" in freshness_text
    assert "End of window: 1/1 shown." in freshness_text
    assert 'bcc message send --send-draft --target "dm:source"' in freshness_text
    assert cross_session["ok"] is True
    cross_result = cast(Mapping[str, object], cross_session["result"])
    cross_text = cast(str, cross_result["text"])
    assert cross_text.startswith(
        "Your message was not sent because the target belongs to another conversation."
    )
    assert 'bcc handoff send --target "dm:target"' in cross_text
    assert "background, goal, and next action" in cross_text
    assert cross_text.endswith("You can also choose not to send anything.")
    assert service.send.await_args_list[1].kwargs["send_draft"] is True


@pytest.mark.asyncio
async def test_message_send_renders_provider_outcomes() -> None:
    service = SimpleNamespace(send=AsyncMock())
    dispatcher = CommandDispatcher(
        cast(ICommandService, service),
        reminder_service=cast(IReminderService, object()),
        handoff_service=cast(IHandoffService, object()),
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
        service.send.return_value = SimpleNamespace(
            delivery_state=state,
            target="dm:source",
            message_id="outbound-1",
            error_message="provider outcome",
        )
        response = await dispatcher(request)

        assert response["ok"] is ok
        if ok:
            result = cast(Mapping[str, object], response["result"])
            assert result["text"] == expected_text
        else:
            assert response["code"] == code
            assert expected_text in cast(str, response["next_action"])
