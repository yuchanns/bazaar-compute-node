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
async def test_command_dispatcher_rejects_requests_before_and_after_lifecycle(
    tmp_path: Path,
) -> None:
    factories = AdapterRegistry().load(
        channel="test",
        runtime="test",
        storage="sqlite",
        audit="test",
    )
    node = NodeApplication(
        factories=factories,
        endpoint_path=tmp_path / "bcn.sock",
        workspace_id="agent-command",
        timeout_budget=make_budget(),
    )

    before_start = await node.command_dispatcher(
        {"kind": "control", "operation": "health"}
    )
    assert before_start["code"] == "SERVICE_NOT_READY"
    await node.start()
    assert node.timer_wheel._driver_task is not None
    await node.stop()
    assert node.timer_wheel._driver_task is None
    after_stop = await node.command_dispatcher(
        {"kind": "control", "operation": "health"}
    )
    assert after_stop["code"] == "SERVICE_NOT_READY"


@pytest.mark.asyncio
async def test_command_dispatcher_enforces_command_deadline(tmp_path: Path) -> None:
    factories = AdapterRegistry().load(
        channel="test",
        runtime="test",
        storage="test",
        audit="test",
    )
    node = NodeApplication(
        factories=factories,
        endpoint_path=tmp_path / "bcn.sock",
        workspace_id="agent-deadline",
        timeout_budget=TimeoutBudget(
            startup_seconds=2,
            provider_call_seconds=2,
            command_seconds=0.01,
            shutdown_seconds=2,
        ),
    )

    await node.start()
    try:
        async with node.storage.transaction():
            response = await node.command_dispatcher(
                {
                    "kind": "command",
                    "resource": "message",
                    "command": "check",
                    "session_id": "blocked-by-storage-lock",
                }
            )
        assert response["ok"] is False
        assert response["code"] == "COMMAND_TIMEOUT"
    finally:
        await node.stop()
