from __future__ import annotations

import os
import platform
import shlex
import stat
import subprocess
import sys
import tomllib
from importlib.metadata import version
from pathlib import Path
from uuid import UUID

import click
import pytest

import bazaar_compute_node.app.system_service as system_service_module
from bazaar_compute_node import __version__
from bazaar_compute_node.app.config import ConfigurationError, load_node_configuration
from bazaar_compute_node.cli import main
from bazaar_compute_node.cmd.bcn import build_cli
from bazaar_compute_node.cmd.bcn._runner import UsageReporter, arguments
from bazaar_compute_node.cmd.bcn.node import _apply_runtime_configuration
from bazaar_compute_node.core.client import CLIENT_INFO
from bazaar_compute_node.core.paths import resolve_data_dir
from bazaar_compute_node.core.runtime import RuntimeSandboxMode
from bazaar_compute_node.i18n import (
    SIMPLIFIED_CHINESE,
    Translator,
    create_translator,
)


def render_help(translator: Translator) -> str:
    cli = build_cli(translator)
    return cli.get_help(click.Context(cli, info_name="bcn"))


def render_command_help(cli: click.Group, name: str) -> str:
    command = cli.commands[name]
    return command.get_help(click.Context(command, info_name=name))


def test_a_node_option_is_accepted_wherever_it_is_written(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # --config belongs to the whole invocation, so it reads the same before the
    # group, between the group and its command, or after the command
    config_path = tmp_path / "config.toml"

    for argv in (
        ["--config", str(config_path), "agent", "list"],
        ["agent", "--config", str(config_path), "list"],
        ["agent", "list", "--config", str(config_path)],
    ):
        assert main(argv) == 0
        assert (
            create_translator(None).text("cli.agent.empty") in capsys.readouterr().out
        )


def test_a_group_asked_for_nothing_answers_with_its_own_help(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # naming a group and stopping there is a question about that group
    assert main(["agent"]) == 0
    assert capsys.readouterr().out.startswith("Usage: bcn agent")

    assert main(["system-service"]) == 0
    assert capsys.readouterr().out.startswith("Usage: bcn system-service")

    assert main([]) == 0
    assert capsys.readouterr().out.startswith("Usage: bcn ")


def test_help_and_version_output() -> None:
    # help shows the resolved data dir
    help_text = render_help(create_translator(None))

    assert str(resolve_data_dir()).replace(" ", "") in help_text.replace(
        " ", ""
    ).replace("\n", "")

    # help follows the selected translator
    translator = create_translator(SIMPLIFIED_CHINESE)

    root_help = render_help(translator)
    cli = build_cli(translator)
    agent_help = render_command_help(cli, "agent")
    service_help = render_command_help(cli, "system-service")

    assert translator.text("cli.bcn.config") in root_help
    assert translator.text("cli.agent.description") in agent_help
    assert translator.text("cli.system_service.description") in service_help

    # the reported version matches distribution metadata
    distribution_version = version("bazaar-compute-node")

    assert __version__ == distribution_version
    assert CLIENT_INFO.version == distribution_version


def test_cli_loads_v4_agent_configuration_and_defaults_node_options(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
version = "4"

[[agent]]
id = "0198d4e6-29c5-7465-b74b-88db31f0c118"
name = "default"

[[agent.channel]]
kind = "test"

[[agent.runtime]]
kind = "test"
""".lstrip(),
        encoding="utf-8",
    )
    args = arguments(
        storage=None,
        audit=None,
        config=config_path,
        database_name=None,
        endpoint=None,
        foreground=False,
    )
    _apply_runtime_configuration(args, UsageReporter())

    assert args.storage == "sqlite"
    assert args.audit == "logging"
    assert args.configuration.version == "4"
    assert args.configuration.agents[0].channels[0].kind == "test"
    (runtime,) = args.configuration.agents[0].runtimes
    assert runtime.kind == "test"
    assert runtime.sandbox_mode is RuntimeSandboxMode.WORKSPACE_WRITE
    assert runtime.network_access is True
    assert args.configuration.agents[0].idle_timeout_seconds == 0


def test_explicit_config_path_creates_default_configuration(tmp_path: Path) -> None:
    config_path = tmp_path / "nested" / "config.toml"

    configuration = load_node_configuration(config_path)

    assert config_path.is_file()
    assert configuration.version == "4"
    assert configuration.agents == ()
    assert configuration.storage == "sqlite"
    assert configuration.audit == "logging"
    assert configuration.database_name is None


@pytest.mark.parametrize("value", ["", ".", "..", "sub/task.sqlite3", "sub\\task"])
def test_database_name_rejects_paths(value: str) -> None:
    with pytest.raises(SystemExit):
        main(["run", "--database-name", value])


def test_node_configuration_rejects_invalid_runtime_sandbox_settings() -> None:
    data_dir = resolve_data_dir()
    data_dir.mkdir(parents=True)
    config_path = data_dir / "config.toml"
    config_path.write_text(
        """
version = "4"

[[agent]]
id = "0198d4e6-29c5-7465-b74b-88db31f0c118"
name = "default"

[[agent.channel]]
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
version = "4"

[[agent]]
id = "0198d4e6-29c5-7465-b74b-88db31f0c118"
name = "default"

[[agent.channel]]
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
version = "4"

[[agent]]
id = "0198d4e6-29c5-7465-b74b-88db31f0c118"
name = "default"
idle_timeout = {value}

[[agent.channel]]
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
version = "4"

[[agent]]
id = "0198d4e6-29c5-7465-b74b-88db31f0c118"
name = "default"
idle_timeout = {value}

[[agent.channel]]
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
    assert "Usage: bcn" in result.stdout


def test_run_accepts_a_zero_agent_configuration(tmp_path: Path) -> None:
    config_path = tmp_path / "empty-config.toml"
    config_path.write_text('version = "4"\n', encoding="utf-8")

    args = arguments(
        storage=None,
        audit=None,
        config=config_path,
        database_name=None,
        endpoint=None,
        foreground=True,
    )
    _apply_runtime_configuration(args, UsageReporter())

    assert args.configuration.agents == ()


def test_agent_list_reports_empty_configuration(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('version = "4"\n', encoding="utf-8")

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
version = "4"

[[agent]]
id = "{agent_id}"
name = "Tifa"

[[agent.channel]]
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
    assert agent["channel"] == [
        {
            "kind": "telegram",
            "bot_id": "bot-id",
            "token_env": "BCN_TELEGRAM_TIFA_TOKEN",
        }
    ]
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
    conflict = tomllib.loads(conflict_path.read_text(encoding="utf-8"))
    assert conflict.get("agent", []) == []


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
    config_path.write_text('version = "4"\n', encoding="utf-8")

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
    config_path.write_text('version = "4"\n', encoding="utf-8")

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
    config_path.write_text('version = "4"\n', encoding="utf-8")
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
version = "4"

[[agent]]
id = "{first_id}"
name = "Tifa"

[[agent.channel]]
kind = "telegram"

[[agent.runtime]]
kind = "codex"

[[agent]]
id = "{second_id}"
name = "{first_id}"

[[agent.channel]]
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
    assert document["version"] == "4"
    assert [agent["name"] for agent in document["agent"]] == ["default", "Tifa"]


def test_server_connect_records_the_server_and_keeps_the_token_out_of_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('version = "4"\n', encoding="utf-8")
    env_file = tmp_path / "env" / "runtime.env"
    token = "0198d4e6-29c5-7465-b74b-88db31f0c118:first"

    assert (
        main(
            [
                "server",
                "connect",
                "--config",
                str(config_path),
                "--url",
                "http://127.0.0.1:8765/",
                "--token",
                token,
                "--env-file",
                str(env_file),
            ]
        )
        == 0
    )

    # case: the configuration names the sink and where its token lives
    payload = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert payload["node"]["audit"] == "server"
    assert payload["node"]["server"] == {
        "url": "http://127.0.0.1:8765",
        "token_env": "BCN_SERVER_TOKEN",
    }
    assert token not in config_path.read_text(encoding="utf-8")

    # case: the token itself went to the environment file, readable only by
    # us, as a literal the shell will not expand
    assert env_file.read_text(encoding="utf-8") == f"BCN_SERVER_TOKEN='{token}'\n"
    if os.name != "nt":
        assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert "bcn system-service start" in capsys.readouterr().out

    # case: connecting again replaces the token and keeps other variables
    env_file.write_text(f"OTHER=kept\nBCN_SERVER_TOKEN={token}\n", encoding="utf-8")
    assert (
        main(
            [
                "server",
                "connect",
                "--config",
                str(config_path),
                "--url",
                "http://127.0.0.1:8765",
                "--token",
                "0198d4e6-29c5-7465-b74b-88db31f0c118:second",
                "--env-file",
                str(env_file),
            ]
        )
        == 0
    )
    assert env_file.read_text(encoding="utf-8") == (
        "OTHER=kept\nBCN_SERVER_TOKEN='0198d4e6-29c5-7465-b74b-88db31f0c118:second'\n"
    )
    # case: nothing of the write is left beside the file
    assert sorted(path.name for path in env_file.parent.iterdir()) == ["runtime.env"]

    # case: a token a shell could read as more than a value is refused
    for bad in ("a'b", "a\nb"):
        with pytest.raises(SystemExit):
            main(
                [
                    "server",
                    "connect",
                    "--config",
                    str(config_path),
                    "--url",
                    "http://127.0.0.1:8765",
                    "--token",
                    bad,
                    "--env-file",
                    str(env_file),
                ]
            )
    assert "second" in env_file.read_text(encoding="utf-8")

    # case: the configuration cannot be written: the token goes back too.
    # the disk "fills" between the two writes: a process-wide file size
    # limit that the short environment file fits under and the longer
    # configuration does not
    if os.name != "nt":
        kept = env_file.read_text(encoding="utf-8")
        limit = len(kept.encode("utf-8")) + 16
        assert limit < config_path.stat().st_size
        connect_under_limit = "\n".join(
            (
                "import resource, sys",
                f"resource.setrlimit(resource.RLIMIT_FSIZE, ({limit}, {limit}))",
                "from bazaar_compute_node.cli import main",
                "sys.exit(main(sys.argv[1:]))",
            )
        )
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                connect_under_limit,
                "server",
                "connect",
                "--config",
                str(config_path),
                "--url",
                "http://127.0.0.1:8765",
                "--token",
                "0198d4e6-29c5-7465-b74b-88db31f0c118:third",
                "--env-file",
                str(env_file),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode != 0, result.stdout
        assert "cannot write" in result.stderr
        assert env_file.read_text(encoding="utf-8") == kept
        assert 'version = "4"' in config_path.read_text(encoding="utf-8")


def test_server_connect_takes_the_env_file_from_the_registered_service(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('version = "4"\n', encoding="utf-8")
    arguments_ = [
        "server",
        "connect",
        "--config",
        str(config_path),
        "--url",
        "http://127.0.0.1:8765",
        "--token",
        "0198d4e6-29c5-7465-b74b-88db31f0c118:secret",
    ]

    # case: before any registration the default file is ours to define, and
    # the output says which file the install must be given
    assert system_service_module.installed_env_file() is None
    assert main(arguments_) == 0
    default_file = Path.home() / ".config" / "bcn" / "runtime.env"
    assert default_file.is_relative_to(tmp_path.parent)
    # the service reads that file without being told, so the hint says so
    out = capsys.readouterr().out
    assert "bcn system-service install`" in out and "--env-file" not in out
    assert default_file.read_text(encoding="utf-8") == (
        "BCN_SERVER_TOKEN='0198d4e6-29c5-7465-b74b-88db31f0c118:secret'\n"
    )

    # case: the file the service was installed with is the one that gets the
    # token; the unit sits where an install puts it, under the test's home
    if platform.system() != "Linux":
        pytest.skip("the registered file is read from a systemd unit here")
    capsys.readouterr()
    unit_path = Path.home() / ".config" / "systemd" / "user" / "bcn.service"
    unit_path.parent.mkdir(parents=True)
    env_file = tmp_path / "service env" / "vars.env"
    unit_path.write_text(
        f"[Service]\nEnvironmentFile=-{shlex.quote(str(env_file))}\n",
        encoding="utf-8",
    )
    # case: a unit at our path that is not ours is somebody else's; its file is left alone
    assert system_service_module.installed_env_file() is None
    unit_path.write_text(
        f"# {system_service_module.MANAGED_MARKER}\n[Service]\n"
        f"EnvironmentFile=-{shlex.quote(str(env_file))}\n",
        encoding="utf-8",
    )
    assert main(arguments_) == 0
    assert env_file.read_text(encoding="utf-8") == (
        "BCN_SERVER_TOKEN='0198d4e6-29c5-7465-b74b-88db31f0c118:secret'\n"
    )
    assert "bcn system-service start" in capsys.readouterr().out
