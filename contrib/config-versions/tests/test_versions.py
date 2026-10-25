from __future__ import annotations

lazy import asyncio
lazy import sqlite3
lazy from contextlib import asynccontextmanager, closing
lazy from pathlib import Path
lazy from uuid import uuid4, uuid7

from bazaar_contrib_config import CONFIG_CURRENT, CONFIG_V4, ConfigConverter
from bazaar_core import (
    Composition,
    Context,
    Contrib,
    ContribEntry,
    ContribHub,
    load,
)

from bazaar_contrib_config_versions import VERSIONS


async def test_installed_chain_preserves_legacy_identity_and_provider_settings(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("BCN_HOME", str(tmp_path))
    id = str(uuid7())

    def prepare():
        directory = tmp_path / ".bcn"
        directory.mkdir()
        with closing(sqlite3.connect(directory / "bcn.sqlite3")) as database, database:
            database.execute(
                "CREATE TABLE node_state (singleton_key INTEGER PRIMARY KEY, workspace_id TEXT)"
            )
            database.execute("INSERT INTO node_state VALUES (1, ?)", (id,))

    await asyncio.to_thread(prepare)
    converters: dict[str, ConfigConverter] = {}

    @asynccontextmanager
    async def consumer(ctx: Context):
        converters.update(v4=ctx.accept(CONFIG_V4), current=ctx.accept(CONFIG_CURRENT))
        yield

    entries = await asyncio.to_thread(load, tuple(reversed(VERSIONS)))
    async with (
        ContribHub() as hub,
        hub.enable(entries),
        hub.enable(
            Composition(
                entries=[
                    ContribEntry(
                        name="conversion",
                        definition=Contrib(
                            accepts=[CONFIG_V4, CONFIG_CURRENT], lifespan=consumer
                        ),
                    )
                ]
            )
        ),
    ):
        legacy = {
            "node": {
                "channel": "wecom",
                "runtime": "codex",
                "lang": "en-US",
                "server": {"url": "https://bcs.example.test", "token_env": "BCS_TOKEN"},
            },
            "channel": {"wecom": {"secret_env": "WECOM_SECRET"}},
            "runtime": {
                "model": "gpt-6.1-sol",
                "effort": "max",
                "idle_timeout": 12.5,
                "network_access": False,
                "env": {"include": ["CUSTOM_CA"]},
            },
        }
        historical = await asyncio.to_thread(converters["v4"].convert, legacy)
        candidate = await asyncio.to_thread(converters["current"].convert, legacy)
        agent = next(
            entry["config"]["agents"][0]
            for package in candidate.payload["contribs"]
            for entry in package["enabled"]
            if entry["entry"] == "agent"
        )
        assert agent["id"] == id
        assert agent["channel"][0]["secret_env"] == "WECOM_SECRET"
        harness = agent["harness"][0]
        assert harness["model"] == "gpt-6.1-sol"
        assert harness["effort"] == "max"
        assert harness["env"]["CUSTOM_CA"] == "CUSTOM_CA"
        assert agent["session"]["config"]["sandbox"]["policy"] == "workspace-write"
        assert agent["idle_timeout"] == 12.5
        assert historical.payload["version"] == "4"
        current = await asyncio.to_thread(
            converters["current"].convert, candidate.payload
        )
        assert current.payload == candidate.payload
        assert not current.changes
        assert not current.required_packages
        assert any(
            package.package == "bazaar-contrib-agent"
            for package in candidate.required_packages
        )
        assert any(
            change.source == "node.database_name" for change in candidate.changes
        )


async def test_v3_and_v4_keep_multiple_entities_data_and_provider_settings(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("BCN_HOME", str(tmp_path))
    from bazaar_contrib_config_versions import Converter

    converters = []
    for version in range(1, 6):
        converters.append(
            Converter(str(version), converters[-1] if converters else None)
        )
    agent = {
        "id": str(uuid7()),
        "name": "Cloud",
        "channel": {"kind": "telegram", "token_env": "TELEGRAM_TOKEN"},
        "runtime": [
            {
                "kind": "claudecode",
                "model": "opus",
                "sandbox_mode": "danger-full-access",
                "env": {"ANTHROPIC_API_KEY": "KEY_WORK"},
            }
        ],
    }
    v3 = {
        "version": "3",
        "node": {
            "lang": "zh-CN",
            "version_check": False,
            "database_name": "history.sqlite3",
        },
        "agent": [agent],
    }
    candidate = await asyncio.to_thread(converters[-1].convert, v3)
    entries = {
        entry["entry"]: entry.get("config", {})
        for package in candidate.payload["contribs"]
        for entry in package["enabled"]
    }
    assert "legacy-node-migrations" in entries
    assert "claudecode-builder" in entries
    assert "telegram-builder" in entries
    assert any(
        package.package == "bazaar-contrib-claude-code"
        for package in candidate.required_packages
    )
    assert entries["sqlite"]["path"] == str(tmp_path / ".bcn" / "history.sqlite3")
    assert (
        entries["agent"]["agents"][0]["harness"][0]["env"]["ANTHROPIC_API_KEY"]
        == "KEY_WORK"
    )
    v4 = await asyncio.to_thread(converters[-2].convert, v3)
    v4.payload["agent"][0]["id"] = str(uuid4())
    v4.payload["node"].update(
        audit="server",
        control="server",
        server={"url": "https://bcs.example.test", "token_env": "BCS_TOKEN"},
        logging={"level": "INFO"},
    )
    v4.payload["agent"][0]["channel"].append({"kind": "lark", "app_id": "lark-work"})
    v4.payload["agent"][0]["runtime"].append(
        {
            "kind": "codex",
            "sandbox_mode": "workspace-write",
            "env": {"CODEX_HOME": "CODEX_HOME_WORK"},
            "custom_setting": "retained",
        }
    )
    candidate = await asyncio.to_thread(converters[-1].convert, v4.payload)
    assert any(
        entry["entry"] == "codex-builder"
        for package in candidate.payload["contribs"]
        for entry in package["enabled"]
    )
    agent = next(
        entry["config"]["agents"][0]
        for package in candidate.payload["contribs"]
        for entry in package["enabled"]
        if entry["entry"] == "agent"
    )
    assert any(channel["kind"] == "lark" for channel in agent["channel"])
    assert agent["harness"][-1]["custom_setting"] == "retained"
    assert agent["session"]["config"]["sandbox"]["policy"] == "danger-full-access"


def test_v4_node_timer_mapping_and_v5_provider_selection(tmp_path: Path, monkeypatch):
    from bazaar_contrib_config import ConfigDocument

    from bazaar_contrib_config_versions import Converter

    monkeypatch.setenv("BCN_HOME", str(tmp_path))
    converter = Converter("5", Converter("4"))
    candidate = converter.convert(
        {"version": "4", "node": {"version_check": False}, "agent": []}
    )
    document = ConfigDocument.model_validate(candidate.payload)
    package = next(
        package
        for package in document.contribs
        if any(entry.entry == "timer-wheel" for entry in package.enabled)
    )
    assert any(
        requirement.package == package.distribution
        for requirement in candidate.required_packages
    )
    payload = {
        "version": "5",
        "contribs": [
            {
                "package": "bcn-asyncio-timer>=0.1.0",
                "enabled": [{"entry": "asyncio-timer"}],
            }
        ],
    }
    assert converter.convert(payload).payload == payload
