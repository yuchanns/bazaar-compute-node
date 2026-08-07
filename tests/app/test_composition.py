from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest

from bazaar_compute_node.app.application import NodeApplication
from bazaar_compute_node.app.registry import AdapterRegistry
from bazaar_compute_node.app.transport import LocalCommandClient
from bazaar_compute_node.contrib.dummy import DummyChannel, DummyRuntime
from bazaar_compute_node.contrib.sqlite import SqliteDatabase
from bazaar_compute_node.core.lifecycle import TimeoutBudget
from bazaar_compute_node.core.models import InboundMessage
from bazaar_compute_node.core.paths import resolve_data_dir


def make_budget() -> TimeoutBudget:
    return TimeoutBudget(
        startup_seconds=2,
        provider_call_seconds=2,
        command_seconds=2,
        shutdown_seconds=2,
    )


def make_message(bcn_session_id: str) -> InboundMessage:
    return InboundMessage(
        seq=1,
        message_id=f"message-{bcn_session_id}-1",
        bcn_session_id=bcn_session_id,
        channel_session_id=f"channel-{bcn_session_id}",
        channel_slug="dummy",
        provider_message_id=f"provider-{bcn_session_id}-1",
        received_at_ms=1,
        sender_id="sender-1",
        sender_display_name="Sender",
        message_type="text",
        canonical_target=f"#dummy:{bcn_session_id}",
        body=f"inbound-{bcn_session_id}",
        provider_time_ms=1,
        provider_thread_id=f"thread-{bcn_session_id}",
    )


@pytest.mark.asyncio
async def test_sqlite_composition_serves_multiple_sessions_over_local_ipc(
    tmp_path: Path,
) -> None:
    factories = AdapterRegistry().load(
        channel_slug="dummy",
        runtime_slug="dummy",
        storage_slug="sqlite",
        audit_slug="dummy",
    )
    data_dir = resolve_data_dir()
    node = NodeApplication(
        factories=factories,
        channel_slug="dummy",
        runtime_slug="dummy",
        storage_slug="sqlite",
        audit_slug="dummy",
        endpoint_path=tmp_path / "bcn.sock",
        node_id="node-3a",
        timeout_budget=make_budget(),
    )
    channel = cast(DummyChannel, node.channel)
    runtime = cast(DummyRuntime, node.runtime)

    await node.start()
    endpoint = node.endpoint
    try:
        if os.name != "nt":
            assert (data_dir / "bin").stat().st_mode & 0o777 == 0o700
            assert (data_dir / "bin" / "bcc").stat().st_mode & 0o777 == 0o700
        health = await LocalCommandClient.request(
            endpoint,
            {"kind": "control", "operation": "health"},
        )
        assert health["ok"] is True
        health_result = health.get("result")
        assert isinstance(health_result, Mapping)
        assert health_result["started"] is True
        assert health_result["accepting"] is True
        assert health_result["channel"] == "dummy"
        assert health_result["runtime"] == "dummy"
        assert health_result["storage"] == "sqlite"
        assert health_result["audit"] == "dummy"
        assert health_result["node_id"] == "node-3a"
        workspace_id = health_result["workspace_id"]
        assert isinstance(workspace_id, str)

        unknown_session = await LocalCommandClient.request(
            endpoint,
            {
                "kind": "command",
                "command": "check",
                "session_id": "missing-session",
            },
        )
        assert unknown_session["ok"] is False
        assert unknown_session["code"] == "SESSION_NOT_FOUND"

        for session_id in ("bcn-a", "bcn-b"):
            await channel.inject(make_message(session_id))

        for _ in range(200):
            if len(channel.sent_messages) == 2:
                break
            await asyncio.sleep(0.01)
        assert len(channel.sent_messages) == 2
        assert {message.bcn_session_id for message in channel.sent_messages} == {
            "bcn-a",
            "bcn-b",
        }
        assert len(runtime.started_turns) == 2

        missing_capability = await LocalCommandClient.request(
            endpoint,
            {
                "kind": "command",
                "command": "check",
                "session_id": "bcn-a",
                "runtime_session_id": "runtime-bcn-a",
            },
        )
        assert missing_capability["code"] == "SESSION_BINDING_FAILED"
        cross_session_capability = await LocalCommandClient.request(
            endpoint,
            {
                "kind": "command",
                "command": "check",
                "session_id": "bcn-b",
                "runtime_session_id": "runtime-bcn-b",
                "session_capability": node._session_capabilities["bcn-a"],
            },
        )
        assert cross_session_capability["code"] == "SESSION_BINDING_FAILED"
    finally:
        await node.stop()

    assert not (tmp_path / "bcn.sock").exists()
    if os.name == "nt":
        assert not (data_dir / "bin" / "bcc.cmd").exists()
        assert not (data_dir / "bin" / "bcc.ps1").exists()
    else:
        assert not (data_dir / "bin" / "bcc").exists()
    assert node._wrapper_path is None
    persisted = SqliteDatabase()
    await persisted.start(timeout=2)
    try:
        identity = await persisted.initialize(node_id="node-3a")
        assert identity.workspace_id == workspace_id
        async with persisted.transaction() as transaction:
            for session_id in ("bcn-a", "bcn-b"):
                assert await transaction.get_bcn_session(session_id) is not None
                messages = await transaction.list_inbound_messages(session_id)
                assert [message.bcn_session_id for message in messages] == [session_id]
                assert await transaction.find_runtime_session(session_id) is not None
    finally:
        await persisted.stop(timeout=2)


@pytest.mark.asyncio
async def test_command_dispatcher_rejects_requests_before_and_after_lifecycle(
    tmp_path: Path,
) -> None:
    factories = AdapterRegistry().load(
        channel_slug="dummy",
        runtime_slug="dummy",
        storage_slug="sqlite",
        audit_slug="dummy",
    )
    node = NodeApplication(
        factories=factories,
        channel_slug="dummy",
        runtime_slug="dummy",
        storage_slug="sqlite",
        audit_slug="dummy",
        endpoint_path=tmp_path / "bcn.sock",
        timeout_budget=make_budget(),
    )

    before_start = await node.command_dispatcher(
        {"kind": "control", "operation": "health"}
    )
    assert before_start["code"] == "SERVICE_NOT_READY"
    await node.start()
    await node.stop()
    after_stop = await node.command_dispatcher(
        {"kind": "control", "operation": "health"}
    )
    assert after_stop["code"] == "SERVICE_NOT_READY"
