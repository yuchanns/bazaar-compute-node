from __future__ import annotations

from pathlib import Path

import pytest

from bazaar_compute_node.app.application import NodeApplication
from bazaar_compute_node.app.config import (
    AgentConfiguration,
    ChannelConfiguration,
    NodeConfiguration,
    RuntimeConfiguration,
)
from bazaar_compute_node.app.registry import AdapterRegistry
from bazaar_compute_node.core.lifecycle import TimeoutBudget
from bazaar_compute_node.i18n import ENGLISH, SIMPLIFIED_CHINESE

AGENT_ID = "0198d4e6-29c5-7465-b74b-88db31f0c118"


def make_configuration(
    *,
    storage: str = "sqlite",
    lang: str | None = None,
) -> NodeConfiguration:
    return NodeConfiguration(
        storage=storage,
        audit="test",
        lang=lang,
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("configured_language", "system_language", "expected"),
    (
        ("zh-CN", "en_US", SIMPLIFIED_CHINESE),
        (None, "zh_CN", SIMPLIFIED_CHINESE),
        (None, "zh_TW", ENGLISH),
    ),
)
async def test_node_composes_one_translator_for_all_agents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    configured_language: str | None,
    system_language: str,
    expected: str,
) -> None:
    monkeypatch.setattr(
        "bazaar_compute_node.i18n.catalog.locale.getlocale",
        lambda: (system_language, "UTF-8"),
    )
    shared_factories = AdapterRegistry().load_shared(storage="sqlite", audit="test")
    node = NodeApplication(
        configuration=make_configuration(lang=configured_language),
        shared_factories=shared_factories,
        endpoint_path=tmp_path / "bcn.sock",
        timeout_budget=make_budget(),
    )

    assert node.translator.language == expected
    await node.start()
    try:
        assert node.agents[AGENT_ID].translator is node.translator
    finally:
        await node.stop()


@pytest.mark.asyncio
async def test_command_dispatcher_rejects_requests_before_and_after_lifecycle(
    tmp_path: Path,
) -> None:
    shared_factories = AdapterRegistry().load_shared(storage="sqlite", audit="test")
    node = NodeApplication(
        configuration=make_configuration(),
        shared_factories=shared_factories,
        endpoint_path=tmp_path / "bcn.sock",
        timeout_budget=make_budget(),
    )

    before_start = await node._dispatch({"kind": "control", "operation": "health"})
    assert before_start["ok"] is True
    before_result = before_start["result"]
    assert isinstance(before_result, dict)
    assert before_result["ready"] is False
    await node.start()
    assert node.timer_wheel._driver_task is not None
    await node.stop()
    assert node.timer_wheel._driver_task is None
    after_stop = await node._dispatch({"kind": "control", "operation": "health"})
    assert after_stop["ok"] is True
    after_result = after_stop["result"]
    assert isinstance(after_result, dict)
    assert after_result["ready"] is False


@pytest.mark.asyncio
async def test_command_dispatcher_enforces_command_deadline(tmp_path: Path) -> None:
    shared_factories = AdapterRegistry().load_shared(storage="sqlite", audit="test")
    node = NodeApplication(
        configuration=make_configuration(storage="sqlite"),
        shared_factories=shared_factories,
        endpoint_path=tmp_path / "bcn.sock",
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
            response = await node.agents[AGENT_ID].command_dispatcher(
                {
                    "kind": "command",
                    "resource": "message",
                    "command": "check",
                    "agent_id": AGENT_ID,
                    "session_id": "blocked-by-storage-lock",
                }
            )
        assert response["ok"] is False
        assert response["code"] == "COMMAND_TIMEOUT"
    finally:
        await node.stop()
