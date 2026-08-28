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
from bazaar_compute_node.core.runtime import RuntimeSandboxMode

LEGACY_AGENT_ID = "0198d4e6-29c5-7465-b74b-88db31f0c118"
FALLBACK_AGENT_ID = "0198d4e7-2a28-7448-8228-388be1bf70b7"


def test_empty_legacy_config_is_upgraded_to_zero_agent_v2(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"

    configuration = load_node_configuration(config_path)

    assert configuration.version == "2"
    assert configuration.agents == ()
    assert configuration.lang is None
    assert tomllib.loads(config_path.read_text(encoding="utf-8")) == {
        "version": "2",
        "node": {"storage": "sqlite", "audit": "logging"},
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
    assert agent.runtime.kind == "codex"
    assert agent.runtime.model == "gpt-5.6-luna"
    assert agent.runtime.effort == "max"
    assert agent.runtime.sandbox_mode is RuntimeSandboxMode.DANGER_FULL_ACCESS
    assert agent.runtime.network_access is False
    assert agent.runtime.idle_timeout_seconds == 12.5
    assert agent.runtime.env_include == ("CUSTOM_CA",)
    written = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert written["version"] == "2"
    assert written["agent"][0]["id"] == LEGACY_AGENT_ID
    assert written["agent"][0]["name"] == "default"
    assert written["agent"][0]["channel"]["kind"] == "wecom"
    assert written["agent"][0]["runtime"]["kind"] == "codex"


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


def test_v2_configuration_round_trips_multiple_agents(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
version = "2"

[node]
storage = "sqlite"
audit = "logging"
lang = "zh-CN"

[[agent]]
id = "0198d4e6-29c5-7465-b74b-88db31f0c118"
name = "CloudStrife"

[agent.channel]
kind = "telegram"
token_env = "TELEGRAM_TOKEN"

[agent.runtime]
kind = "codex"
model = "gpt-5.6-luna"
idle_timeout = 60

[[agent]]
id = "0198d4e7-2a28-7448-8228-388be1bf70b7"
name = "Tifa"

[agent.channel]
kind = "wecom"
secret_env = "WECOM_SECRET"

[agent.runtime]
kind = "codex"
network_access = false
""".lstrip(),
        encoding="utf-8",
    )
    original = config_path.read_text(encoding="utf-8")

    first = load_node_configuration(config_path)
    second = load_node_configuration(config_path)

    assert first == second
    assert first.lang == "zh-CN"
    assert [agent.name for agent in first.agents] == ["CloudStrife", "Tifa"]
    assert first.agents[0].runtime.idle_timeout_seconds == 60
    assert first.agents[1].runtime.network_access is False
    assert config_path.read_text(encoding="utf-8") == original


def test_v2_lang_is_kept_verbatim_and_must_be_non_empty(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"

    # a lang the node does not ship is still preserved
    config_path.write_text(
        'version = "2"\n\n[node]\nlang = "ja"\n',
        encoding="utf-8",
    )
    assert load_node_configuration(config_path).lang == "ja"

    # an empty lang is refused
    config_path.write_text(
        'version = "2"\n\n[node]\nlang = ""\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="node.lang must be non-empty text"):
        load_node_configuration(config_path)


def test_future_configuration_version_is_rejected_without_rewrite(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    original = 'version = "3"\n'
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


def test_v2_rejects_invalid_agent_tables() -> None:
    def agent(**overrides: object) -> dict[str, object]:
        return {
            "id": LEGACY_AGENT_ID,
            "name": "default",
            "channel": {"kind": "telegram"},
            "runtime": {"kind": "codex"},
        } | overrides

    def parse(*agents: dict[str, object]) -> None:
        config_module._parse_v2_configuration({"version": "2", "agent": list(agents)})

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
