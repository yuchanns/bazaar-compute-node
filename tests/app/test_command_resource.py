from __future__ import annotations

from pathlib import Path

import pytest

from bazaar_compute_node.app.application import NodeApplication
from bazaar_compute_node.app.registry import AdapterRegistry
from bazaar_compute_node.core.lifecycle import TimeoutBudget


def make_budget() -> TimeoutBudget:
    return TimeoutBudget(
        startup_seconds=2,
        provider_call_seconds=2,
        command_seconds=2,
        shutdown_seconds=2,
    )


@pytest.mark.asyncio
async def test_command_dispatch_requires_resource_and_rejects_collisions(
    tmp_path: Path,
) -> None:
    factories = AdapterRegistry().load(
        channel="test",
        runtime="test",
        storage="test",
        audit="test",
    )
    node = NodeApplication(
        factories=factories,
        endpoint_path=tmp_path / "bcn.sock",
        timeout_budget=make_budget(),
    )
    await node.start()
    try:
        missing_resource = await node.command_dispatcher(
            {
                "kind": "command",
                "command": "check",
                "session_id": "bcn-a",
            }
        )
        assert missing_resource["ok"] is False
        assert missing_resource["code"] == "RESOURCE_REQUIRED"

        message_collision = await node.command_dispatcher(
            {
                "kind": "command",
                "resource": "message",
                "command": "unfollow",
                "session_id": "bcn-a",
            }
        )
        assert message_collision["ok"] is False
        assert message_collision["code"] == "UNKNOWN_COMMAND"

        thread_collision = await node.command_dispatcher(
            {
                "kind": "command",
                "resource": "thread",
                "command": "check",
                "session_id": "bcn-a",
            }
        )
        assert thread_collision["ok"] is False
        assert thread_collision["code"] == "UNKNOWN_COMMAND"

        unknown_resource = await node.command_dispatcher(
            {
                "kind": "command",
                "resource": "inbox",
                "command": "check",
                "session_id": "bcn-a",
            }
        )
        assert unknown_resource["ok"] is False
        assert unknown_resource["code"] == "UNKNOWN_RESOURCE"
    finally:
        await node.stop()
