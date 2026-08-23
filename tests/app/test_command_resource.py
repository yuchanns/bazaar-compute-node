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
    IHandoffService,
    IReminderService,
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
async def test_handoff_dispatch_validates_binding_and_serializes_results() -> None:
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
    service = SimpleNamespace(
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
        handoff_service=cast(IHandoffService, service),
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
