from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest

import bazaar_compute_node.app.upgrade as upgrade_module
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
from bazaar_compute_node.app.upgrade import UpgradeService
from bazaar_compute_node.core.actor import Actors, Mode
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


def make_configuration(mode: Mode = Mode.SESSION) -> NodeConfiguration:
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
                mode=mode,
            ),
        ),
    )


def make_upgrade_service(
    *,
    installed_version: str = "0.1.0",
    available_version: str | None = None,
    request_restart: Callable[[], None] = lambda: None,
) -> UpgradeService:
    return UpgradeService(
        available_version=lambda: available_version,
        installed_version=installed_version,
        request_restart=request_restart,
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
        thread_id="session-1",
        target_kind=ChannelTargetKind.DM,
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
                "actor_id": "bcn-a",
            }
        )
        assert missing_resource["ok"] is False
        assert missing_resource["code"] == "RESOURCE_REQUIRED"

        message_collision = await dispatcher(
            {
                "kind": "command",
                "resource": "message",
                "command": "unfollow",
                "actor_id": "bcn-a",
            }
        )
        assert message_collision["ok"] is False
        assert message_collision["code"] == "UNKNOWN_COMMAND"

        thread_collision = await dispatcher(
            {
                "kind": "command",
                "resource": "thread",
                "command": "check",
                "actor_id": "bcn-a",
            }
        )
        assert thread_collision["ok"] is False
        assert thread_collision["code"] == "UNKNOWN_COMMAND"

        inbox_collision = await dispatcher(
            {
                "kind": "command",
                "resource": "inbox",
                "command": "list",
                "actor_id": "bcn-a",
            }
        )
        assert inbox_collision["ok"] is False
        assert inbox_collision["code"] == "UNKNOWN_COMMAND"

        unknown_resource = await dispatcher(
            {
                "kind": "command",
                "resource": "unknown",
                "command": "list",
                "actor_id": "bcn-a",
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
        thread_id="session-source",
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
        actors=Actors(agent_id="agent-1", mode=Mode.SESSION),
        reminder_service=cast(IReminderService, object()),
        timeout_budget=make_budget(),
        upgrade_service=make_upgrade_service(),
    )
    dispatcher.start_accepting()
    request = {
        "kind": "command",
        "resource": "message",
        "command": "send",
        "actor_id": "session-source",
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
        "actor",
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
        actors=Actors(agent_id="agent-1", mode=Mode.SESSION),
        reminder_service=cast(IReminderService, object()),
        timeout_budget=make_budget(),
        upgrade_service=make_upgrade_service(),
    )
    dispatcher.start_accepting()
    request = {
        "kind": "command",
        "resource": "message",
        "command": "send",
        "actor_id": "session-source",
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


@pytest.mark.asyncio
async def test_a_node_that_cannot_upgrade_itself_offers_no_node_commands() -> None:
    dispatcher = CommandDispatcher(
        cast(ICommandService, SimpleNamespace()),
        actors=Actors(agent_id="agent-1", mode=Mode.SESSION),
        reminder_service=cast(IReminderService, object()),
        timeout_budget=make_budget(),
        upgrade_service=None,
    )
    dispatcher.start_accepting()

    result = await dispatcher(
        {
            "kind": "command",
            "resource": "node",
            "command": "version",
            "actor_id": "session-source",
        }
    )

    # case: on a platform where nothing would bring the node back, the resource
    # is not there at all rather than there and refusing
    assert result["ok"] is False
    assert result["code"] == "UNKNOWN_RESOURCE"


@pytest.mark.asyncio
async def test_upgrade_is_refused_before_a_release_is_announced() -> None:
    dispatcher = CommandDispatcher(
        cast(ICommandService, SimpleNamespace()),
        actors=Actors(agent_id="agent-1", mode=Mode.SESSION),
        reminder_service=cast(IReminderService, object()),
        timeout_budget=make_budget(),
        upgrade_service=make_upgrade_service(),
    )
    dispatcher.start_accepting()

    result = await dispatcher(
        {
            "kind": "command",
            "resource": "node",
            "command": "upgrade",
            "actor_id": "session-source",
            "message_id": "0198d4e6-29c5-7465-b74b-88db31f0c118",
        }
    )

    # case: an Agent that runs the command on its own gets told there is
    # nothing to install rather than a surprise restart
    assert result["ok"] is False
    assert result["code"] == "UPGRADE_NOT_AVAILABLE"


@pytest.mark.asyncio
async def test_upgrade_rejects_a_command_without_an_anchor() -> None:
    dispatcher = CommandDispatcher(
        cast(ICommandService, SimpleNamespace()),
        actors=Actors(agent_id="agent-1", mode=Mode.SESSION),
        reminder_service=cast(IReminderService, object()),
        timeout_budget=make_budget(),
        upgrade_service=make_upgrade_service(),
    )
    dispatcher.start_accepting()

    result = await dispatcher(
        {
            "kind": "command",
            "resource": "node",
            "command": "upgrade",
            "actor_id": "session-source",
        }
    )

    # case: without an anchor there is nothing to wake the Agent after the
    # restart, and the Agent is told which argument it left out
    assert result["ok"] is False
    assert result["code"] == "UPGRADE_ANCHOR_REQUIRED"
    assert "--message-id" in cast(str, result["error"])


@pytest.mark.asyncio
async def test_version_reports_the_process_and_not_the_disk() -> None:
    dispatcher = CommandDispatcher(
        cast(ICommandService, SimpleNamespace()),
        actors=Actors(agent_id="agent-1", mode=Mode.SESSION),
        reminder_service=cast(IReminderService, object()),
        timeout_budget=make_budget(),
        upgrade_service=make_upgrade_service(installed_version="0.1.0"),
    )
    dispatcher.start_accepting()

    result = await dispatcher(
        {
            "kind": "command",
            "resource": "node",
            "command": "version",
            "actor_id": "session-source",
        }
    )

    # case: an Agent confirming an upgrade needs the version it is talking to,
    # which is the one this process started with
    assert result["ok"] is True
    assert cast(Mapping[str, object], result["result"]) == {"version": "0.1.0"}


@pytest.mark.asyncio
async def test_one_upgrade_transaction_runs_at_a_time() -> None:
    restarts: list[None] = []
    service = make_upgrade_service(
        available_version="9.9.9",
        request_restart=lambda: restarts.append(None),
    )
    inside = 0
    overlapped = False

    def install(version: str) -> None:
        del version
        nonlocal inside, overlapped
        inside += 1
        overlapped = overlapped or inside > 1
        time.sleep(0.05)
        inside -= 1

    async def wake_after(version: str) -> str | None:
        del version
        nonlocal inside, overlapped
        inside += 1
        overlapped = overlapped or inside > 1
        await asyncio.sleep(0.05)
        inside -= 1
        return "reminder-1"

    # uv itself is covered by the e2e; what is under test here is whether two
    # sessions can be inside the transaction at the same time
    with patch.object(upgrade_module, "_install", install):
        results = await asyncio.gather(
            service.upgrade(wake_after=wake_after),
            service.upgrade(wake_after=wake_after),
        )

    # case: two Agents accepting offers at the same time must not overlap
    # anywhere between installing and asking to restart -- the second would
    # otherwise install a release the first has already answered for
    assert not overlapped
    assert [version for version, _ in results] == ["9.9.9", "9.9.9"]
    assert len(restarts) == 2
