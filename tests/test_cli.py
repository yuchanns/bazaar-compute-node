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


def test_help_and_version_output() -> None:
    # help shows the resolved data dir
    help_text = build_parser().format_help()

    assert str(resolve_data_dir()).replace(" ", "") in help_text.replace(
        " ", ""
    ).replace("\n", "")

    # help follows the selected translator
    translator = create_translator(SIMPLIFIED_CHINESE)

    root_help = build_parser(translator).format_help()
    agent_help = build_agent_parser(translator).format_help()
    service_help = build_system_service_parser(translator).format_help()

    assert "配置文件路径" in root_help
    assert "管理 bcn 配置文件中的 Agent 定义" in agent_help
    assert "管理 bcn 的用户级宿主机服务" in service_help

    # the reported version matches distribution metadata
    distribution_version = version("bazaar-compute-node")

    assert __version__ == distribution_version
    assert CLIENT_INFO.version == distribution_version


def test_cli_loads_v3_agent_configuration_and_defaults_node_options(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
version = "3"

[[agent]]
id = "0198d4e6-29c5-7465-b74b-88db31f0c118"
name = "default"

[agent.channel]
kind = "test"

[[agent.runtime]]
kind = "test"
""".lstrip(),
        encoding="utf-8",
    )
    parser = build_parser()
    args = parser.parse_args(["run", "--config", str(config_path)])
    _apply_runtime_configuration(args, parser)

    assert args.storage == "sqlite"
    assert args.audit == "logging"
    assert args.configuration.version == "3"
    assert args.configuration.agents[0].channel.kind == "test"
    (runtime,) = args.configuration.agents[0].runtimes
    assert runtime.kind == "test"
    assert runtime.sandbox_mode is RuntimeSandboxMode.WORKSPACE_WRITE
    assert runtime.network_access is True
    assert args.configuration.agents[0].idle_timeout_seconds == 0


def test_explicit_config_path_creates_default_configuration(tmp_path: Path) -> None:
    config_path = tmp_path / "nested" / "config.toml"

    configuration = load_node_configuration(config_path)

    assert config_path.is_file()
    assert configuration.version == "3"
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
version = "3"

[[agent]]
id = "0198d4e6-29c5-7465-b74b-88db31f0c118"
name = "default"

[agent.channel]
kind = "telegram"

[[agent.runtime]]
kind = "codex"
sandbox_mode = "host-unrestricted"
""".lstrip(),
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match=r"runtime #1\.sandbox_mode"):
        load_node_configuration()

    config_path.write_text(
        """
version = "3"

[[agent]]
id = "0198d4e6-29c5-7465-b74b-88db31f0c118"
name = "default"

[agent.channel]
kind = "telegram"

[[agent.runtime]]
kind = "codex"
network_access = "yes"
""".lstrip(),
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match=r"runtime #1\.network_access"):
        load_node_configuration()


@pytest.mark.parametrize(
    "value",
    ['"one"', "true", "nan", "inf", "-inf"],
)
def test_node_configuration_rejects_invalid_agent_idle_timeout(value: str) -> None:
    data_dir = resolve_data_dir()
    data_dir.mkdir(parents=True)
    (data_dir / "config.toml").write_text(
        f"""
version = "3"

[[agent]]
id = "0198d4e6-29c5-7465-b74b-88db31f0c118"
name = "default"
idle_timeout = {value}

[agent.channel]
kind = "telegram"

[[agent.runtime]]
kind = "codex"
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match=r"agent #1\.idle_timeout"):
        load_node_configuration()


@pytest.mark.parametrize(
    ("value", "expected_seconds"),
    [("0", 0), ("1", 1), ("0.0001", 0.0001)],
)
def test_node_configuration_parses_agent_idle_timeout(
    value: str,
    expected_seconds: float,
) -> None:
    data_dir = resolve_data_dir()
    data_dir.mkdir(parents=True)
    (data_dir / "config.toml").write_text(
        f"""
version = "3"

[[agent]]
id = "0198d4e6-29c5-7465-b74b-88db31f0c118"
name = "default"
idle_timeout = {value}

[agent.channel]
kind = "telegram"

[[agent.runtime]]
kind = "codex"
""".lstrip(),
        encoding="utf-8",
    )

    configuration = load_node_configuration()

    assert configuration.agents[0].idle_timeout_seconds == expected_seconds


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
    config_path.write_text('version = "3"\n', encoding="utf-8")

    parser = build_parser()
    args = parser.parse_args(["run", "--config", str(config_path), "--foreground"])
    _apply_runtime_configuration(args, parser)

    assert args.configuration.agents == ()


def test_agent_list_reports_empty_configuration(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('version = "3"\n', encoding="utf-8")

    assert main(["agent", "list", "--config", str(config_path)]) == 0

    assert capsys.readouterr().out == (
        f"{create_translator(None).text('cli.agent.empty')}\n"
    )


def test_agent_list_joins_runtime_kinds_in_configuration_order(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.toml"
    agent_id = "0198d4e6-29c5-7465-b74b-88db31f0c118"
    config_path.write_text(
        f"""
version = "3"

[[agent]]
id = "{agent_id}"
name = "Tifa"

[agent.channel]
kind = "telegram"

[[agent.runtime]]
kind = "claudecode"

[[agent.runtime]]
kind = "codex"
""".lstrip(),
        encoding="utf-8",
    )

    assert main(["agent", "list", "--config", str(config_path)]) == 0

    assert capsys.readouterr().out == (
        f"id={agent_id} name=Tifa channel=telegram runtime=claudecode,codex\n"
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
                "agent.idle_timeout=600",
                "--set",
                "runtime.env=CODEX_HOME=BCN_CODEX_HOME_WORK",
                "--set",
                "runtime.env=SSH_AUTH_SOCK=SSH_AUTH_SOCK",
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
    assert agent["idle_timeout"] == 600.0
    assert agent["channel"] == {
        "kind": "telegram",
        "bot_id": "bot-id",
        "token_env": "BCN_TELEGRAM_TIFA_TOKEN",
    }
    assert agent["runtime"] == [
        {
            "kind": "codex",
            "model": "gpt-5.6",
            "sandbox_mode": "workspace-write",
            "network_access": False,
            "env": {
                "CODEX_HOME": "BCN_CODEX_HOME_WORK",
                "SSH_AUTH_SOCK": "SSH_AUTH_SOCK",
            },
        }
    ]
    assert "runtime=codex" in output


def test_agent_add_converts_deprecated_env_include_to_env(
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
                'runtime.env_include=["CODEX_HOME", "CUSTOM_CA"]',
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert create_translator(None).text("cli.agent.env_include_deprecated") in (
        captured.err
    )
    document = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert document["agent"][0]["runtime"] == [
        {
            "kind": "codex",
            "sandbox_mode": "workspace-write",
            "network_access": True,
            "env": {"CODEX_HOME": "CODEX_HOME", "CUSTOM_CA": "CUSTOM_CA"},
        }
    ]

    # the deprecated spelling merges into the same table, and a repeated name
    # is overwritten by the last --set rather than refused
    merged_path = tmp_path / "merged.toml"
    assert (
        main(
            [
                "agent",
                "add",
                "--config",
                str(merged_path),
                "--name",
                "Aerith",
                "--channel",
                "telegram",
                "--runtime",
                "codex",
                "--set",
                "runtime.env=CODEX_HOME=BCN_CODEX_HOME_WORK",
                "--set",
                'runtime.env_include=["CODEX_HOME", "CUSTOM_CA"]',
            ]
        )
        == 0
    )
    capsys.readouterr()
    merged = tomllib.loads(merged_path.read_text(encoding="utf-8"))
    assert merged["agent"][0]["runtime"][0]["env"] == {
        "CODEX_HOME": "CODEX_HOME",
        "CUSTOM_CA": "CUSTOM_CA",
    }

    conflict_path = tmp_path / "conflict.toml"

    # both spellings are validated where they are redirected
    for invalid in (
        'runtime.env_include=["A", "A"]',
        "runtime.env_include=[1]",
        "runtime.env=CODEX_HOME",
        "runtime.env==BCN_CODEX_HOME_WORK",
        "runtime.env=bad-name=BCN_CODEX_HOME_WORK",
    ):
        with pytest.raises(SystemExit):
            main(
                [
                    "agent",
                    "add",
                    "--config",
                    str(conflict_path),
                    "--name",
                    "Aerith",
                    "--channel",
                    "telegram",
                    "--runtime",
                    "codex",
                    "--set",
                    invalid,
                ]
            )
    assert "agent" not in tomllib.loads(conflict_path.read_text(encoding="utf-8"))


def test_agent_add_accumulates_repeated_env_options(tmp_path: Path) -> None:
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
                "claudecode",
                "--set",
                "runtime.env=ANTHROPIC_API_KEY=ANTHROPIC_API_KEY_WORK",
                "--set",
                "runtime.env=SSH_AUTH_SOCK=SSH_AUTH_SOCK",
                "--set",
                "runtime.provider_option=kept",
            ]
        )
        == 0
    )

    text = config_path.read_text(encoding="utf-8")
    # the table is a sub-table of the runtime array element, written last so the
    # runtime's own keys are not swallowed by it
    assert "[agent.runtime.env]" in text
    assert text.index("provider_option") < text.index("[agent.runtime.env]")
    assert tomllib.loads(text)["agent"][0]["runtime"][0]["env"] == {
        "ANTHROPIC_API_KEY": "ANTHROPIC_API_KEY_WORK",
        "SSH_AUTH_SOCK": "SSH_AUTH_SOCK",
    }


def test_agent_add_rejects_invalid_options(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # duplicate and kind options are refused
    config_path = tmp_path / "config.toml"
    config_path.write_text('version = "3"\n', encoding="utf-8")

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

    # daemon options do not belong to agent commands
    config_path = tmp_path / "config.toml"
    config_path.write_text('version = "3"\n', encoding="utf-8")

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

    # option keys cannot carry edge whitespace, and nothing is written
    config_path = tmp_path / "never-written.toml"

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

    # a name conflict is refused before anything is written
    config_path = tmp_path / "conflict.toml"
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

    with pytest.raises(SystemExit):
        main(add_arguments)


def test_agent_remove(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # removing by name or id only rewrites the configuration
    config_path = tmp_path / "config.toml"
    config_path.write_text('version = "3"\n', encoding="utf-8")
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

    # an ambiguous selector is refused
    config_path = tmp_path / "config.toml"
    first_id = "0198d4e6-29c5-7465-b74b-88db31f0c118"
    second_id = "0198d4e7-2a28-7448-8228-388be1bf70b7"
    config_path.write_text(
        f"""
version = "3"

[[agent]]
id = "{first_id}"
name = "Tifa"

[agent.channel]
kind = "telegram"

[[agent.runtime]]
kind = "codex"

[[agent]]
id = "{second_id}"
name = "{first_id}"

[agent.channel]
kind = "wecom"

[[agent.runtime]]
kind = "codex"
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit):
        main(["agent", "remove", first_id, "--config", str(config_path)])


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
    assert document["version"] == "3"
    assert [agent["name"] for agent in document["agent"]] == ["default", "Tifa"]
