from __future__ import annotations

import subprocess
import sys
import tomllib
from importlib.metadata import version
from pathlib import Path
from uuid import UUID

import pytest

from bazaar_compute_node import __version__
from bazaar_compute_node.app.agent_management import build_agent_parser
from bazaar_compute_node.app.config import ConfigurationError, load_node_configuration
from bazaar_compute_node.app.system_service import build_system_service_parser
from bazaar_compute_node.cli import (
    _apply_runtime_configuration,
    build_parser,
    main,
)
from bazaar_compute_node.core.client import CLIENT_INFO
from bazaar_compute_node.core.paths import resolve_data_dir
from bazaar_compute_node.core.runtime import RuntimeSandboxMode
from bazaar_compute_node.i18n import SIMPLIFIED_CHINESE, create_translator


def test_help_shows_the_resolved_data_dir() -> None:
    help_text = build_parser().format_help()

    assert str(resolve_data_dir()).replace(" ", "") in help_text.replace(
        " ", ""
    ).replace("\n", "")


def test_cli_help_uses_the_selected_translator() -> None:
    translator = create_translator(SIMPLIFIED_CHINESE)

    root_help = build_parser(translator).format_help()
    agent_help = build_agent_parser(translator).format_help()
    service_help = build_system_service_parser(translator).format_help()

    assert "配置文件路径" in root_help
    assert "管理 bcn 配置文件中的 Agent 定义" in agent_help
    assert "管理 bcn 的用户级宿主机服务" in service_help


def test_runtime_version_matches_distribution_metadata() -> None:
    distribution_version = version("bazaar-compute-node")

    assert __version__ == distribution_version
    assert CLIENT_INFO.version == distribution_version


def test_cli_loads_v2_agent_configuration_and_defaults_node_options(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
version = "2"

[[agent]]
id = "0198d4e6-29c5-7465-b74b-88db31f0c118"
name = "default"

[agent.channel]
kind = "test"

[agent.runtime]
kind = "test"
""".lstrip(),
        encoding="utf-8",
    )
    parser = build_parser()
    args = parser.parse_args(["run", "--config", str(config_path)])
    _apply_runtime_configuration(args, parser)

    assert args.storage == "sqlite"
    assert args.audit == "logging"
    assert args.configuration.version == "2"
    assert args.configuration.agents[0].channel.kind == "test"
    assert args.configuration.agents[0].runtime.kind == "test"
    runtime = args.configuration.agents[0].runtime
    assert runtime.sandbox_mode is RuntimeSandboxMode.WORKSPACE_WRITE
    assert runtime.network_access is True
    assert runtime.idle_timeout_seconds == 0


def test_explicit_config_path_creates_default_configuration(tmp_path: Path) -> None:
    config_path = tmp_path / "nested" / "config.toml"

    configuration = load_node_configuration(config_path)

    assert config_path.is_file()
    assert configuration.version == "2"
    assert configuration.agents == ()
    assert configuration.storage == "sqlite"
    assert configuration.audit == "logging"
    assert configuration.database_name is None


@pytest.mark.parametrize("value", ["", ".", "..", "sub/task.sqlite3", "sub\\task"])
def test_database_name_rejects_paths(value: str) -> None:
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["run", "--database-name", value])


def test_node_configuration_rejects_invalid_runtime_sandbox_settings() -> None:
    data_dir = resolve_data_dir()
    data_dir.mkdir(parents=True)
    config_path = data_dir / "config.toml"
    config_path.write_text(
        """
version = "2"

[[agent]]
id = "0198d4e6-29c5-7465-b74b-88db31f0c118"
name = "default"

[agent.channel]
kind = "telegram"

[agent.runtime]
kind = "codex"
sandbox_mode = "host-unrestricted"
""".lstrip(),
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="runtime.sandbox_mode"):
        load_node_configuration()

    config_path.write_text(
        """
version = "2"

[[agent]]
id = "0198d4e6-29c5-7465-b74b-88db31f0c118"
name = "default"

[agent.channel]
kind = "telegram"

[agent.runtime]
kind = "codex"
network_access = "yes"
""".lstrip(),
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="runtime.network_access"):
        load_node_configuration()


@pytest.mark.parametrize(
    "value",
    ['"one"', "true", "nan", "inf", "-inf"],
)
def test_node_configuration_rejects_invalid_runtime_idle_timeout(value: str) -> None:
    data_dir = resolve_data_dir()
    data_dir.mkdir(parents=True)
    (data_dir / "config.toml").write_text(
        f"""
version = "2"

[[agent]]
id = "0198d4e6-29c5-7465-b74b-88db31f0c118"
name = "default"

[agent.channel]
kind = "telegram"

[agent.runtime]
kind = "codex"
idle_timeout = {value}
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="runtime.idle_timeout"):
        load_node_configuration()


@pytest.mark.parametrize(
    ("value", "expected_seconds"),
    [("0", 0), ("1", 1), ("0.0001", 0.0001)],
)
def test_node_configuration_parses_runtime_idle_timeout(
    value: str,
    expected_seconds: float,
) -> None:
    data_dir = resolve_data_dir()
    data_dir.mkdir(parents=True)
    (data_dir / "config.toml").write_text(
        f"""
version = "2"

[[agent]]
id = "0198d4e6-29c5-7465-b74b-88db31f0c118"
name = "default"

[agent.channel]
kind = "telegram"

[agent.runtime]
kind = "codex"
idle_timeout = {value}
""".lstrip(),
        encoding="utf-8",
    )

    configuration = load_node_configuration()

    assert configuration.agents[0].runtime.idle_timeout_seconds == expected_seconds


def test_help_works_in_a_real_process() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "bazaar_compute_node.cli", "--help"],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0
    assert "usage: bcn" in result.stdout


def test_run_accepts_a_zero_agent_configuration(tmp_path: Path) -> None:
    config_path = tmp_path / "empty-config.toml"
    config_path.write_text('version = "2"\n', encoding="utf-8")

    parser = build_parser()
    args = parser.parse_args(["run", "--config", str(config_path), "--foreground"])
    _apply_runtime_configuration(args, parser)

    assert args.configuration.agents == ()


def test_agent_list_reports_empty_configuration(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('version = "2"\n', encoding="utf-8")

    assert main(["agent", "list", "--config", str(config_path)]) == 0

    assert capsys.readouterr().out == (
        f"{create_translator(None).text('cli.agent.empty')}\n"
    )


def test_agent_add_preserves_typed_options_and_round_trips(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.toml"

    assert (
        main(
            [
                "agent",
                "add",
                "--config",
                str(config_path),
                "--name",
                "Tifa",
                "--channel",
                "telegram",
                "--runtime",
                "codex",
                "--set",
                "channel.token_env=BCN_TELEGRAM_TIFA_TOKEN",
                "--set",
                "channel.bot_id=bot-id",
                "--set",
                "runtime.model=gpt-5.6",
                "--set",
                "runtime.network_access=false",
                "--set",
                "runtime.idle_timeout=600",
                "--set",
                'runtime.env_include=["CODEX_HOME"]',
            ]
        )
        == 0
    )

    output = capsys.readouterr().out
    assert "Agent added id=" in output
    assert "Run `bcn restart` to apply." in output
    document = tomllib.loads(config_path.read_text(encoding="utf-8"))
    agent = document["agent"][0]
    assert UUID(agent["id"]).version == 7
    assert agent["name"] == "Tifa"
    assert agent["channel"] == {
        "kind": "telegram",
        "bot_id": "bot-id",
        "token_env": "BCN_TELEGRAM_TIFA_TOKEN",
    }
    assert agent["runtime"] == {
        "kind": "codex",
        "model": "gpt-5.6",
        "sandbox_mode": "workspace-write",
        "network_access": False,
        "idle_timeout": 600.0,
        "env_include": ["CODEX_HOME"],
    }


def test_agent_add_rejects_duplicate_and_kind_options(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('version = "2"\n', encoding="utf-8")
    original = config_path.read_text(encoding="utf-8")

    for option in ("runtime.model=first", "runtime.kind=codex"):
        with pytest.raises(SystemExit):
            main(
                [
                    "agent",
                    "add",
                    "--config",
                    str(config_path),
                    "--name",
                    "Tifa",
                    "--channel",
                    "telegram",
                    "--runtime",
                    "codex",
                    "--set",
                    option,
                    "--set",
                    "runtime.model=second"
                    if option.startswith("runtime.model")
                    else "runtime.kind=other",
                ]
            )

    assert config_path.read_text(encoding="utf-8") == original


def test_agent_commands_reject_daemon_options(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('version = "2"\n', encoding="utf-8")

    with pytest.raises(SystemExit):
        main(
            [
                "agent",
                "list",
                "--config",
                str(config_path),
                "--storage",
                "test",
            ]
        )


def test_agent_add_rejects_option_key_edge_whitespace(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"

    with pytest.raises(SystemExit):
        main(
            [
                "agent",
                "add",
                "--config",
                str(config_path),
                "--name",
                "Tifa",
                "--channel",
                "telegram",
                "--runtime",
                "codex",
                "--set",
                "runtime.model =gpt-5.6",
            ]
        )

    assert not config_path.exists()


def test_agent_add_rejects_name_conflict_without_writing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.toml"
    add_arguments = [
        "agent",
        "add",
        "--config",
        str(config_path),
        "--name",
        "Tifa",
        "--channel",
        "telegram",
        "--runtime",
        "codex",
    ]
    assert main(add_arguments) == 0
    capsys.readouterr()
    original = config_path.read_text(encoding="utf-8")

    with pytest.raises(SystemExit):
        main(add_arguments)

    assert config_path.read_text(encoding="utf-8") == original


def test_agent_remove_by_name_and_id_only_changes_configuration(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('version = "2"\n', encoding="utf-8")
    add_arguments = [
        "agent",
        "add",
        "--config",
        str(config_path),
        "--name",
        "Tifa",
        "--channel",
        "telegram",
        "--runtime",
        "codex",
    ]
    assert main(add_arguments) == 0
    capsys.readouterr()
    first_id = tomllib.loads(config_path.read_text(encoding="utf-8"))["agent"][0]["id"]
    add_arguments[add_arguments.index("Tifa")] = "Aerith"
    assert main(add_arguments) == 0
    capsys.readouterr()

    assert main(["agent", "remove", "Aerith", "--config", str(config_path)]) == 0
    remove_output = capsys.readouterr().out
    assert "Workspace and durable data were preserved." in remove_output
    assert main(["agent", "remove", first_id, "--config", str(config_path)]) == 0
    assert tomllib.loads(config_path.read_text(encoding="utf-8")).get("agent", []) == []


def test_agent_remove_rejects_ambiguous_id_or_name_selector(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    first_id = "0198d4e6-29c5-7465-b74b-88db31f0c118"
    second_id = "0198d4e7-2a28-7448-8228-388be1bf70b7"
    config_path.write_text(
        f"""
version = "2"

[[agent]]
id = "{first_id}"
name = "Tifa"

[agent.channel]
kind = "telegram"

[agent.runtime]
kind = "codex"

[[agent]]
id = "{second_id}"
name = "{first_id}"

[agent.channel]
kind = "wecom"

[agent.runtime]
kind = "codex"
""".lstrip(),
        encoding="utf-8",
    )
    original = config_path.read_text(encoding="utf-8")

    with pytest.raises(SystemExit):
        main(["agent", "remove", first_id, "--config", str(config_path)])

    assert config_path.read_text(encoding="utf-8") == original


def test_agent_add_upgrades_legacy_configuration_before_mutation(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[node]
channel = "wecom"
runtime = "codex"

[channel.wecom]
bot_id = "bot-id"

[runtime]
model = "gpt-5.6"
""".lstrip(),
        encoding="utf-8",
    )

    assert (
        main(
            [
                "agent",
                "add",
                "--config",
                str(config_path),
                "--name",
                "Tifa",
                "--channel",
                "telegram",
                "--runtime",
                "codex",
            ]
        )
        == 0
    )

    document = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert document["version"] == "2"
    assert [agent["name"] for agent in document["agent"]] == ["default", "Tifa"]
