from __future__ import annotations

from pathlib import Path

from bazaar_compute_node.app.application import NodeApplication
from bazaar_compute_node.app.config import (
    AgentConfiguration,
    ChannelConfiguration,
    NodeConfiguration,
    RuntimeConfiguration,
    write_configuration,
)
from bazaar_compute_node.app.registry import AdapterRegistry
from bazaar_compute_node.core.lifecycle import TimeoutBudget

AGENT_ID = "0198d4e6-29c5-7465-b74b-88db31f0c118"


async def node_reporting_to(base: str, tmp_path: Path) -> NodeApplication:
    """A real node with one agent, reporting to the server at `base` and
    taking its requests from there, the way `bcn server connect` sets one
    up; `BCN_SERVER_TOKEN` holds its enrolment. Its configuration is on
    disk beside its socket, so a change made from the server is written
    where a restart would read it."""

    server = {"url": base, "token_env": "BCN_SERVER_TOKEN"}
    configuration = NodeConfiguration(
        version_check=False,
        storage="sqlite",
        audit="server",
        audit_options=server,
        control="server",
        control_options=server,
        agents=(
            AgentConfiguration(
                id=AGENT_ID,
                name="Kana",
                channels=(ChannelConfiguration(kind="test"),),
                runtimes=(RuntimeConfiguration(kind="test"),),
            ),
        ),
    )
    config_path = tmp_path / "config.toml"
    write_configuration(config_path, configuration)
    node = NodeApplication(
        configuration=configuration,
        shared_factories=AdapterRegistry().load_shared(
            storage="sqlite", audit="server", control="server"
        ),
        endpoint_path=tmp_path / "bcn.sock",
        config_path=config_path,
        env_path=tmp_path / "runtime.env",
        timeout_budget=TimeoutBudget(
            startup_seconds=5,
            provider_call_seconds=5,
            command_seconds=5,
            shutdown_seconds=2,
        ),
    )
    await node.start()
    return node
