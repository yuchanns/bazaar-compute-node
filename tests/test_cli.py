from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from bazaar_compute_node.app.config import ConfigurationError, load_node_configuration
from bazaar_compute_node.cli import (
    _apply_runtime_configuration,
    _daemon_command,
    build_parser,
)
from bazaar_compute_node.core.paths import resolve_data_dir
from bazaar_compute_node.core.runtime import RuntimeSandboxMode


def test_help_shows_the_resolved_data_dir() -> None:
    help_text = build_parser().format_help()

    assert str(resolve_data_dir()).replace(" ", "") in help_text.replace(
        " ", ""
    ).replace("\n", "")


def test_cli_defaults_to_sqlite_storage_and_logging_audit() -> None:
    parser = build_parser()
    args = parser.parse_args(["run", "--channel", "test", "--runtime", "test"])
    _apply_runtime_configuration(args, parser)

    assert args.storage == "sqlite"
    assert args.audit == "logging"
    assert args.sandbox_mode is RuntimeSandboxMode.WORKSPACE_WRITE
    assert args.network_access is True


def test_daemon_command_forwards_optional_runtime_configuration(tmp_path: Path) -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "start",
            "--channel",
            "codex",
            "--runtime",
            "codex",
            "--model",
            "gpt-5.6-luna",
            "--effort",
            "max",
            "--sandbox-mode",
            "danger-full-access",
            "--no-network-access",
        ]
    )
    _apply_runtime_configuration(args, parser)

    command = _daemon_command(args, tmp_path)

    assert command[-7:] == [
        "--model",
        "gpt-5.6-luna",
        "--effort",
        "max",
        "--sandbox-mode",
        "danger-full-access",
        "--no-network-access",
    ]


def test_cli_loads_node_configuration_and_preserves_flag_precedence() -> None:
    data_dir = resolve_data_dir()
    data_dir.mkdir(parents=True)
    (data_dir / "config.toml").write_text(
        '[node]\nchannel = "config-channel"\nruntime = "config-runtime"\n'
        'storage = "config-storage"\naudit = "config-audit"\n'
        'endpoint = "config.sock"\n\n'
        '[runtime]\nmodel = "config-model"\neffort = "config-effort"\n'
        'sandbox_mode = "danger-full-access"\nnetwork_access = false\n\n'
        '[runtime.env]\ninclude = ["CUSTOM_CA"]\n\n'
        '[channel.wecom]\nbot_id = "test-bot"\n'
        'websocket_url = "wss://wecom.example.test"\n',
        encoding="utf-8",
    )
    parser = build_parser()
    config_args = parser.parse_args(["run"])
    _apply_runtime_configuration(config_args, parser)
    assert config_args.channel == "config-channel"
    assert config_args.runtime == "config-runtime"
    assert config_args.storage == "config-storage"
    assert config_args.audit == "config-audit"
    assert config_args.endpoint == Path("config.sock")
    assert config_args.model == "config-model"
    assert config_args.effort == "config-effort"
    assert config_args.sandbox_mode is RuntimeSandboxMode.DANGER_FULL_ACCESS
    assert config_args.network_access is False
    assert config_args.runtime_env_include == ("CUSTOM_CA",)
    assert config_args.channel_options == {
        "bot_id": "test-bot",
        "websocket_url": "wss://wecom.example.test",
    }

    flag_args = parser.parse_args(
        [
            "run",
            "--channel",
            "codex",
            "--runtime",
            "codex",
            "--storage",
            "flag-storage",
            "--audit",
            "flag-audit",
            "--endpoint",
            "flag.sock",
            "--model",
            "flag-model",
            "--effort",
            "flag-effort",
        ]
    )
    _apply_runtime_configuration(flag_args, parser)

    assert flag_args.model == "flag-model"
    assert flag_args.effort == "flag-effort"
    assert flag_args.channel == "codex"
    assert flag_args.runtime == "codex"
    assert flag_args.storage == "flag-storage"
    assert flag_args.audit == "flag-audit"
    assert flag_args.endpoint == Path("flag.sock")


def test_node_configuration_rejects_invalid_runtime_sandbox_settings() -> None:
    data_dir = resolve_data_dir()
    data_dir.mkdir(parents=True)
    config_path = data_dir / "config.toml"
    config_path.write_text(
        '[runtime]\nsandbox_mode = "host-unrestricted"\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="runtime.sandbox_mode"):
        load_node_configuration()

    config_path.write_text(
        '[runtime]\nnetwork_access = "yes"\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="runtime.network_access"):
        load_node_configuration()


def test_help_works_in_a_real_process() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "bazaar_compute_node.cli", "--help"],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0
    assert "usage: bcn" in result.stdout


def test_run_requires_explicit_channel_and_runtime() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "bazaar_compute_node.cli", "run"],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 2
    assert "--channel and --runtime must be provided together" in result.stderr
