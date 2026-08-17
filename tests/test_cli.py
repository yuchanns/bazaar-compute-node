from __future__ import annotations

import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

import pytest

from bazaar_compute_node import __version__
from bazaar_compute_node.app.config import ConfigurationError, load_node_configuration
from bazaar_compute_node.cli import (
    _apply_runtime_configuration,
    _daemon_command,
    build_parser,
)
from bazaar_compute_node.core.client import CLIENT_INFO
from bazaar_compute_node.core.paths import resolve_data_dir
from bazaar_compute_node.core.runtime import RuntimeSandboxMode


def test_help_shows_the_resolved_data_dir() -> None:
    help_text = build_parser().format_help()

    assert str(resolve_data_dir()).replace(" ", "") in help_text.replace(
        " ", ""
    ).replace("\n", "")


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


def test_cli_forwards_explicit_config_and_database_name(tmp_path: Path) -> None:
    config_path = tmp_path / "task-config.toml"
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
    args = parser.parse_args(
        [
            "start",
            "--config",
            str(config_path),
            "--database-name",
            "task.sqlite3",
        ]
    )
    args.config = args.config.expanduser().resolve()
    _apply_runtime_configuration(args, parser)

    command = _daemon_command(args, tmp_path)

    assert command[command.index("--config") + 1] == str(config_path)
    assert command[command.index("--database-name") + 1] == "task.sqlite3"


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
