from __future__ import annotations

import os
import sqlite3
import stat
import tomllib
from pathlib import Path
from uuid import UUID

import pytest

import bazaar_compute_node.app.config as config_module
from bazaar_compute_node.app.config import ConfigurationError, load_node_configuration
from bazaar_compute_node.core.actor import Mode
from bazaar_compute_node.core.runtime import RuntimeSandboxMode

LEGACY_AGENT_ID = "0198d4e6-29c5-7465-b74b-88db31f0c118"
FALLBACK_AGENT_ID = "0198d4e7-2a28-7448-8228-388be1bf70b7"


def test_empty_legacy_config_is_upgraded_to_zero_agent_v3(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"

    configuration = load_node_configuration(config_path)

    assert configuration.version == "3"
    assert configuration.agents == ()
    assert configuration.lang is None
    assert configuration.version_check is True
    assert tomllib.loads(config_path.read_text(encoding="utf-8")) == {
        "version": "3",
        "node": {"storage": "sqlite", "audit": "logging", "version_check": True},
    }
    if os.name != "nt":
        assert stat.S_IMODE(config_path.stat().st_mode) == 0o600


def test_wecom_legacy_config_reuses_sqlite_workspace_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(config_module, "resolve_data_dir", lambda: data_dir)
    with sqlite3.connect(data_dir / "bcn.sqlite3") as connection:
        connection.execute(
            "CREATE TABLE node_state ("
            "singleton_key INTEGER PRIMARY KEY, workspace_id TEXT"
            ")"
        )
        connection.execute(
            "INSERT INTO node_state (singleton_key, workspace_id) VALUES (1, ?)",
            (LEGACY_AGENT_ID,),
        )

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
version = "1"

[node]
channel = "wecom"
runtime = "codex"

[channel.wecom]
bot_id = "wecom-bot"
secret_env = "WECOM_SECRET"
websocket_url = "wss://wecom.example.test"

[runtime]
model = "gpt-5.6-luna"
effort = "max"
sandbox_mode = "danger-full-access"
network_access = false
idle_timeout = 12.5

[runtime.env]
include = ["CUSTOM_CA"]
""".lstrip(),
        encoding="utf-8",
    )

    configuration = load_node_configuration(config_path)

    assert len(configuration.agents) == 1
    agent = configuration.agents[0]
    assert agent.id == LEGACY_AGENT_ID
    assert agent.name == "default"
    assert agent.channel.kind == "wecom"
    assert agent.channel.options == {
        "bot_id": "wecom-bot",
        "secret_env": "WECOM_SECRET",
        "websocket_url": "wss://wecom.example.test",
    }
    assert len(agent.runtimes) == 1
    runtime = agent.runtimes[0]
    assert runtime.kind == "codex"
    assert runtime.model == "gpt-5.6-luna"
    assert runtime.effort == "max"
    assert runtime.sandbox_mode is RuntimeSandboxMode.DANGER_FULL_ACCESS
    assert runtime.network_access is False
    # the v1 runtime idle timeout is lifted onto the agent by the v2 -> v3 step
    assert agent.idle_timeout_seconds == 12.5
    # a v1 include list becomes a same-name v3 mapping in one chained upgrade
    assert runtime.env == {"CUSTOM_CA": "CUSTOM_CA"}
    text = config_path.read_text(encoding="utf-8")
    assert '[agent.runtime.env]\nCUSTOM_CA = "CUSTOM_CA"' in text
    written = tomllib.loads(text)
    assert written["version"] == "3"
    assert written["agent"][0]["id"] == LEGACY_AGENT_ID
    assert written["agent"][0]["name"] == "default"
    assert written["agent"][0]["idle_timeout"] == 12.5
    assert written["agent"][0]["channel"]["kind"] == "wecom"
    assert written["agent"][0]["runtime"] == [
        {
            "kind": "codex",
            "model": "gpt-5.6-luna",
            "effort": "max",
            "sandbox_mode": "danger-full-access",
            "network_access": False,
            "env": {"CUSTOM_CA": "CUSTOM_CA"},
        }
    ]


def test_telegram_legacy_config_uses_uuid7_and_default_token_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(config_module, "resolve_data_dir", lambda: data_dir)
    sqlite3.connect(data_dir / "bcn.sqlite3").close()
    monkeypatch.setattr(config_module, "uuid7", lambda: UUID(FALLBACK_AGENT_ID))
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[node]\nchannel = "telegram"\nruntime = "codex"\n',
        encoding="utf-8",
    )

    configuration = load_node_configuration(config_path)

    agent = configuration.agents[0]
    assert agent.id == FALLBACK_AGENT_ID
    assert agent.channel.kind == "telegram"
    assert agent.channel.options == {"token_env": "BCN_TELEGRAM_BOT_TOKEN"}


def test_v2_configuration_is_migrated_to_a_single_element_runtime_array(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
version = "2"

[node]
storage = "sqlite"
audit = "logging"

[[agent]]
id = "0198d4e6-29c5-7465-b74b-88db31f0c118"
name = "CloudStrife"

[agent.channel]
kind = "telegram"
token_env = "TELEGRAM_TOKEN"

[agent.runtime]
kind = "codex"
model = "gpt-5.6-luna"
env_include = ["CODEX_HOME", "CUSTOM_CA"]
env = { CODEX_HOME = "BCN_CODEX_HOME_WORK" }
provider_option = "kept"
""".lstrip(),
        encoding="utf-8",
    )

    configuration = load_node_configuration(config_path)

    assert configuration.version == "3"
    (agent,) = configuration.agents
    (runtime,) = agent.runtimes
    assert runtime.kind == "codex"
    assert runtime.model == "gpt-5.6-luna"
    # both spellings merge, and the newer env wins the name they share
    assert runtime.env == {
        "CODEX_HOME": "BCN_CODEX_HOME_WORK",
        "CUSTOM_CA": "CUSTOM_CA",
    }
    # non-standard runtime keys still reach the provider options untouched
    assert runtime.options == {"provider_option": "kept"}
    written = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert written["version"] == "3"
    assert written["agent"][0]["runtime"][0]["env"] == {
        "CODEX_HOME": "BCN_CODEX_HOME_WORK",
        "CUSTOM_CA": "CUSTOM_CA",
    }


def test_v3_configuration_round_trips_multiple_runtimes(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
version = "3"

[node]
storage = "sqlite"
audit = "logging"
lang = "zh-CN"

[[agent]]
id = "0198d4e6-29c5-7465-b74b-88db31f0c118"
name = "CloudStrife"
idle_timeout = 60

[agent.channel]
kind = "telegram"
token_env = "TELEGRAM_TOKEN"

[[agent.runtime]]
kind = "claudecode"
model = "opus"

[agent.runtime.env]
ANTHROPIC_API_KEY = "ANTHROPIC_API_KEY_WORK"
SSH_AUTH_SOCK = "SSH_AUTH_SOCK"

[[agent.runtime]]
kind = "codex"
network_access = false
provider_option = "kept"

[agent.runtime.env]
CODEX_HOME = "BCN_CODEX_HOME_PERSONAL"

[[agent]]
id = "0198d4e7-2a28-7448-8228-388be1bf70b7"
name = "Tifa"
mode = "dangerous_individual"

[agent.channel]
kind = "wecom"
secret_env = "WECOM_SECRET"

[[agent.runtime]]
kind = "codex"
""".lstrip(),
        encoding="utf-8",
    )
    original = config_path.read_text(encoding="utf-8")

    first = load_node_configuration(config_path)
    second = load_node_configuration(config_path)

    assert first == second
    assert first.lang == "zh-CN"
    assert [agent.name for agent in first.agents] == ["CloudStrife", "Tifa"]
    assert [runtime.kind for runtime in first.agents[0].runtimes] == [
        "claudecode",
        "codex",
    ]
    assert first.agents[0].idle_timeout_seconds == 60
    assert first.agents[0].runtimes[0].env == {
        "ANTHROPIC_API_KEY": "ANTHROPIC_API_KEY_WORK",
        "SSH_AUTH_SOCK": "SSH_AUTH_SOCK",
    }
    assert first.agents[0].runtimes[1].network_access is False
    assert first.agents[0].runtimes[1].options == {"provider_option": "kept"}
    assert first.agents[0].runtimes[1].env == {"CODEX_HOME": "BCN_CODEX_HOME_PERSONAL"}
    assert first.agents[1].runtimes[0].env == {}
    # an agent without a mode answers one conversation at a time
    assert first.agents[0].mode is Mode.SESSION
    assert first.agents[1].mode is Mode.DANGEROUS_INDIVIDUAL
    # an already current configuration is never rewritten
    assert config_path.read_text(encoding="utf-8") == original

    # serializing and re-reading preserves the whole contract
    round_trip_path = tmp_path / "round-trip.toml"
    round_trip_path.write_text(
        config_module._serialize_configuration(first), encoding="utf-8"
    )
    assert load_node_configuration(round_trip_path) == first


def test_v3_lang_is_kept_verbatim_and_must_be_non_empty(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"

    # a lang the node does not ship is still preserved
    config_path.write_text(
        'version = "3"\n\n[node]\nlang = "ja"\n',
        encoding="utf-8",
    )
    assert load_node_configuration(config_path).lang == "ja"

    # an empty lang is refused
    config_path.write_text(
        'version = "3"\n\n[node]\nlang = ""\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="node.lang must be non-empty text"):
        load_node_configuration(config_path)


def test_future_configuration_version_is_rejected_without_rewrite(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    original = 'version = "4"\n'
    config_path.write_text(original, encoding="utf-8")

    with pytest.raises(ConfigurationError, match="unsupported configuration version"):
        load_node_configuration(config_path)

    assert config_path.read_text(encoding="utf-8") == original


def test_failed_atomic_replace_preserves_legacy_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.toml"
    original = '[node]\nchannel = "telegram"\nruntime = "codex"\n'
    config_path.write_text(original, encoding="utf-8")

    def fail_replace(*_: object) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(config_module.os, "replace", fail_replace)

    with pytest.raises(ConfigurationError, match="cannot write"):
        load_node_configuration(config_path)

    assert config_path.read_text(encoding="utf-8") == original
    assert not tuple(tmp_path.glob(".config.toml.*.tmp"))


def test_v3_rejects_invalid_agent_tables() -> None:
    def agent(**overrides: object) -> dict[str, object]:
        return {
            "id": LEGACY_AGENT_ID,
            "name": "default",
            "channel": {"kind": "telegram"},
            "runtime": [{"kind": "codex"}],
        } | overrides

    def parse(*agents: dict[str, object]) -> None:
        config_module._parse_v3_configuration({"version": "3", "agent": list(agents)})

    # agent ids must be unique
    with pytest.raises(ConfigurationError, match="agent.id values must be unique"):
        parse(agent(name="first"), agent(name="second"))

    # agent names must be unique
    with pytest.raises(ConfigurationError, match="agent.name values must be unique"):
        parse(agent(), agent(id=FALLBACK_AGENT_ID))

    # an agent id must be a canonical UUIDv7
    with pytest.raises(ConfigurationError, match="agent.id must be a canonical UUIDv7"):
        parse(agent(id="00000000-0000-4000-8000-000000000000"))

    # environment names must be valid identifiers
    for value in ("bad-name", "", "1INVALID"):
        with pytest.raises(ConfigurationError, match="agent.channel.token_env"):
            parse(agent(channel={"kind": "telegram", "token_env": value}))

    # a mode names one of the execution models bcn knows
    with pytest.raises(ConfigurationError, match=r"agent #1\.mode"):
        parse(agent(mode="individual"))

    # the runtime array carries every runtime of one agent
    with pytest.raises(
        ConfigurationError, match=r"agent #1\.runtime must be an array of TOML tables"
    ):
        parse(agent(runtime={"kind": "codex"}))

    # an agent without any runtime cannot serve a session
    with pytest.raises(
        ConfigurationError, match="agent.runtime must define at least one runtime"
    ):
        parse(agent(runtime=[]))

    # env maps a child process name to the name read from the node process
    with pytest.raises(
        ConfigurationError,
        match=r"agent #1\.runtime #2\.env contains an invalid environment name: "
        "bad-name",
    ):
        parse(
            agent(
                runtime=[
                    {"kind": "codex"},
                    {"kind": "codex", "env": {"bad-name": "CODEX_HOME"}},
                ]
            )
        )

    with pytest.raises(
        ConfigurationError,
        match=r"agent #1\.runtime #1\.env contains an invalid environment name: "
        "1INVALID",
    ):
        parse(agent(runtime=[{"kind": "codex", "env": {"CODEX_HOME": "1INVALID"}}]))

    with pytest.raises(
        ConfigurationError, match=r"agent #1\.runtime #1\.env\.CODEX_HOME"
    ):
        parse(agent(runtime=[{"kind": "codex", "env": {"CODEX_HOME": 7}}]))

    with pytest.raises(
        ConfigurationError, match=r"agent #1\.runtime #1\.env must be a TOML table"
    ):
        parse(agent(runtime=[{"kind": "codex", "env": ["CODEX_HOME"]}]))


def test_v3_version_check_can_be_turned_off(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
version = "3"

[node]
storage = "sqlite"
audit = "logging"
version_check = false
""".lstrip(),
        encoding="utf-8",
    )

    configuration = load_node_configuration(config_path)

    # case: an operator can stop the node from reaching PyPI at all
    assert configuration.version_check is False

    # case: the setting survives a serialize and re-read round trip
    round_trip_path = tmp_path / "round-trip.toml"
    round_trip_path.write_text(
        config_module._serialize_configuration(configuration), encoding="utf-8"
    )
    assert load_node_configuration(round_trip_path) == configuration


def test_v3_version_check_must_be_a_boolean(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
version = "3"

[node]
storage = "sqlite"
audit = "logging"
version_check = "yes"
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="node.version_check"):
        load_node_configuration(config_path)
