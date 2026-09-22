from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from pathlib import Path

import pytest
from bcn_test_support import RecordingAudit, TestRuntime
from test_composition import AGENT_ID, make_budget, make_configuration

from bazaar_compute_node.app.application import NodeApplication
from bazaar_compute_node.app.config import (
    AgentConfiguration,
    ChannelConfiguration,
    ConfigurationError,
    RuntimeConfiguration,
    load_node_configuration,
    write_configuration,
)
from bazaar_compute_node.app.registry import AdapterRegistry

NEWCOMER_ID = "0199aa00-1111-7222-8333-444455556666"


@pytest.mark.asyncio
async def test_a_running_node_takes_agents_in_changes_them_and_lets_them_go(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An agent added while the node runs is started, written to the
    configuration file and beaten out in the health; changed, it is a new
    instance; removed, it is gone from all three while the others run on.
    One that cannot start stays on the list with its failure."""

    config_path = tmp_path / "config.toml"
    env_path = tmp_path / "runtime.env"
    write_configuration(config_path, make_configuration())
    monkeypatch.delenv(
        "BCN_0199AA00_1111_7222_8333_444455556666_CHANNEL0_TEST_TOKEN", raising=False
    )
    node = NodeApplication(
        configuration=make_configuration(),
        shared_factories=AdapterRegistry().load_shared(storage="sqlite", audit="test"),
        endpoint_path=tmp_path / "bcn.sock",
        timeout_budget=make_budget(),
        config_path=config_path,
        env_path=env_path,
    )
    audit = node.audit
    assert isinstance(audit, RecordingAudit)
    await node.start()
    try:
        first = node.agents[AGENT_ID]
        newcomer = AgentConfiguration(
            id=NEWCOMER_ID,
            name="Newcomer",
            channels=(ChannelConfiguration(kind="test"),),
            runtimes=(RuntimeConfiguration(kind="test"),),
        )

        # case: added - running, on disk, its token under the node's own
        # variable name in the environment file and the process
        added = await node.add_agent(newcomer, {0: {"token": "secret-1"}})
        assert added.channels[0].options == {
            "token_env": "BCN_0199AA00_1111_7222_8333_444455556666_CHANNEL0_TEST_TOKEN"
        }
        assert (
            os.environ["BCN_0199AA00_1111_7222_8333_444455556666_CHANNEL0_TEST_TOKEN"]
            == "secret-1"
        )
        assert (
            "BCN_0199AA00_1111_7222_8333_444455556666_CHANNEL0_TEST_TOKEN='secret-1'"
            in env_path.read_text()
        )
        assert node.agents[NEWCOMER_ID].started and node.agents[AGENT_ID] is first
        stored = load_node_configuration(config_path)
        assert [agent.id for agent in stored.agents] == [AGENT_ID, NEWCOMER_ID]
        assert stored.agents[1].channels[0].options == {
            "token_env": "BCN_0199AA00_1111_7222_8333_444455556666_CHANNEL0_TEST_TOKEN"
        }
        assert _beaten(audit) == [AGENT_ID, NEWCOMER_ID]
        assert "secret-1" not in str(audit.events)

        # case: changed - a new instance under the same id, the first agent
        # untouched, the new token replacing the old under the same name
        running = node.agents[NEWCOMER_ID]
        changed = await node.update_agent(
            replace(added, name="Renamed"), {0: {"token": "secret-2"}}
        )
        assert changed.channels[0].options == {
            "token_env": "BCN_0199AA00_1111_7222_8333_444455556666_CHANNEL0_TEST_TOKEN"
        }
        assert (
            os.environ["BCN_0199AA00_1111_7222_8333_444455556666_CHANNEL0_TEST_TOKEN"]
            == "secret-2"
        )
        assert (
            env_path.read_text().count(
                "BCN_0199AA00_1111_7222_8333_444455556666_CHANNEL0_TEST_TOKEN="
            )
            == 1
        )
        assert node.agents[NEWCOMER_ID] is not running and not running.started
        assert node.agents[NEWCOMER_ID].name == "Renamed"
        assert node.agents[AGENT_ID] is first
        assert load_node_configuration(config_path).agents[1].name == "Renamed"

        # case: a change to an agent already done for is refused on its
        # state, not queued behind it; the first change completes as it was
        renaming = asyncio.ensure_future(
            node.update_agent(replace(changed, name="Renamed again"))
        )
        await asyncio.sleep(0)
        with pytest.raises(ValueError, match="agent is terminated"):
            await node.remove_agent(NEWCOMER_ID)
        changed = await renaming
        assert node.agents[NEWCOMER_ID].name == "Renamed again"

        # case: one that cannot start is kept on the list, the failure said
        broken = await node.update_agent(
            replace(changed, channels=(ChannelConfiguration(kind="nowhere"),))
        )
        assert NEWCOMER_ID not in node.agents
        assert node.agent_startup_results[NEWCOMER_ID].status == "failed"
        assert [a.id for a in node.configuration.agents] == [AGENT_ID, NEWCOMER_ID]
        updated = [e for e in audit.events if e.event_name == "agent.updated"][-1]
        assert updated.metadata["error_type"] and broken.name == "Renamed again"

        # case: removed - gone from the node, the health and the file; the
        # variable stays, the first agent runs on
        removed = await node.remove_agent(NEWCOMER_ID)
        assert removed.id == NEWCOMER_ID
        assert NEWCOMER_ID not in node.agents
        assert NEWCOMER_ID not in node.agent_startup_results
        assert [a.id for a in load_node_configuration(config_path).agents] == [AGENT_ID]
        assert _beaten(audit) == [AGENT_ID]
        assert (
            "BCN_0199AA00_1111_7222_8333_444455556666_CHANNEL0_TEST_TOKEN="
            in env_path.read_text()
        )
        assert node.agents[AGENT_ID] is first and first.started
        with pytest.raises(ValueError):
            await node.remove_agent(NEWCOMER_ID)
        assert [
            e.event_name for e in audit.events if e.event_name.startswith("agent.")
        ] == [
            "agent.added",
            "agent.updated",
            "agent.updated",
            "agent.updated",
            "agent.removed",
        ]
    finally:
        await node.stop()
        monkeypatch.delenv(
            "BCN_0199AA00_1111_7222_8333_444455556666_CHANNEL0_TEST_TOKEN",
            raising=False,
        )


@pytest.mark.asyncio
async def test_a_change_the_node_will_not_have_keeps_nothing_and_a_bad_stop_goes_on(
    tmp_path: Path,
) -> None:
    """A change the node's configuration as a whole refuses - a second agent
    by an existing name - keeps none of its credentials. A change whose old
    instance reports trouble stopping still brings the new one up."""

    config_path = tmp_path / "config.toml"
    env_path = tmp_path / "runtime.env"
    write_configuration(config_path, make_configuration())
    node = NodeApplication(
        configuration=make_configuration(),
        shared_factories=AdapterRegistry().load_shared(storage="sqlite", audit="test"),
        endpoint_path=tmp_path / "bcn.sock",
        timeout_budget=make_budget(),
        config_path=config_path,
        env_path=env_path,
    )
    await node.start()
    try:
        first = node.configuration.agents[0]
        twin = replace(first, id=NEWCOMER_ID)
        with pytest.raises(ConfigurationError, match="unique"):
            await node.add_agent(twin, {0: {"token": "never-kept"}})
        assert not env_path.exists() or "never-kept" not in env_path.read_text()
        assert "never-kept" not in os.environ.values()

        runtime = node.agents[AGENT_ID].runtimes[0]
        assert isinstance(runtime, TestRuntime)
        runtime.stop_error = RuntimeError("wrapper left behind")
        renamed = await node.update_agent(replace(first, name="Renamed"))
        assert node.agents[AGENT_ID].started
        assert node.agents[AGENT_ID].name == renamed.name == "Renamed"
    finally:
        await node.stop()


@pytest.mark.asyncio
async def test_a_stop_cancelled_on_the_way_down_is_cancelled(tmp_path: Path) -> None:
    """A removal cancelled while it stops its agent - the node going down
    under a request - finishes the stop and is cancelled, not turned into a
    failure the one cancelling would never hear of."""

    config_path = tmp_path / "config.toml"
    write_configuration(config_path, make_configuration())
    node = NodeApplication(
        configuration=make_configuration(),
        shared_factories=AdapterRegistry().load_shared(storage="sqlite", audit="test"),
        endpoint_path=tmp_path / "bcn.sock",
        timeout_budget=make_budget(),
        config_path=config_path,
    )
    await node.start()
    try:
        agent = node.agents[AGENT_ID]
        runtime = agent.runtimes[0]
        assert isinstance(runtime, TestRuntime)
        runtime.stop_gate = asyncio.Event()
        removing = asyncio.create_task(node.remove_agent(AGENT_ID))
        async with asyncio.timeout(10):
            while agent.started:
                await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        removing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await removing
    finally:
        await node.stop()


def _beaten(audit: RecordingAudit) -> list[object]:
    """The agents the latest health beat listed, by id."""

    beat = [event for event in audit.events if event.event_name == "node.health"][-1]
    agents = beat.metadata["agents"]
    assert isinstance(agents, list | tuple)
    return [record["agent_id"] for record in agents]
