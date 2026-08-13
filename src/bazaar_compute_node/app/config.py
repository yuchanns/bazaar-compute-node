from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from ..core.paths import resolve_data_dir
from ..core.runtime import RuntimeSandboxMode

CONFIG_FILENAME = "config.toml"


@dataclass(frozen=True, slots=True)
class NodeConfiguration:
    """Optional startup settings loaded from the node's persistent config."""

    channel: str | None = None
    runtime: str | None = None
    storage: str | None = None
    audit: str | None = None
    endpoint: str | None = None
    database_name: str | None = None
    model: str | None = None
    effort: str | None = None
    sandbox_mode: RuntimeSandboxMode = RuntimeSandboxMode.WORKSPACE_WRITE
    network_access: bool = True
    runtime_env_include: tuple[str, ...] = ()
    wecom_bot_id: str | None = None
    wecom_websocket_url: str | None = None


class ConfigurationError(ValueError):
    """Raised when the persistent bcn configuration is invalid."""


def resolve_config_path() -> Path:
    return resolve_data_dir() / CONFIG_FILENAME


def load_node_configuration(path: Path | None = None) -> NodeConfiguration:
    path = path or resolve_config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        with path.open("rb") as config_file:
            payload = tomllib.load(config_file)
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ConfigurationError(f"cannot read {path}: {error}") from error

    node = payload.get("node", {})
    if not isinstance(node, dict):
        raise ConfigurationError("[node] must be a TOML table")
    runtime = payload.get("runtime", {})
    if not isinstance(runtime, dict):
        raise ConfigurationError("[runtime] must be a TOML table")
    runtime_env = runtime.get("env", {})
    if not isinstance(runtime_env, dict):
        raise ConfigurationError("[runtime.env] must be a TOML table")
    channel = payload.get("channel", {})
    if not isinstance(channel, dict):
        raise ConfigurationError("[channel] must be a TOML table")
    wecom = channel.get("wecom", {})
    if not isinstance(wecom, dict):
        raise ConfigurationError("[channel.wecom] must be a TOML table")
    sandbox_mode = runtime.get("sandbox_mode", RuntimeSandboxMode.WORKSPACE_WRITE.value)
    if not isinstance(sandbox_mode, str):
        raise ConfigurationError("runtime.sandbox_mode must be text")
    try:
        parsed_sandbox_mode = RuntimeSandboxMode(sandbox_mode)
    except ValueError as error:
        allowed = ", ".join(mode.value for mode in RuntimeSandboxMode)
        raise ConfigurationError(
            f"runtime.sandbox_mode must be one of: {allowed}"
        ) from error
    network_access = runtime.get("network_access", True)
    if not isinstance(network_access, bool):
        raise ConfigurationError("runtime.network_access must be a boolean")
    return NodeConfiguration(
        channel=_optional_text(node.get("channel"), "node.channel"),
        runtime=_optional_text(node.get("runtime"), "node.runtime"),
        storage=_optional_text(node.get("storage"), "node.storage"),
        audit=_optional_text(node.get("audit"), "node.audit"),
        endpoint=_optional_text(node.get("endpoint"), "node.endpoint"),
        database_name=_optional_text(node.get("database_name"), "node.database_name"),
        model=_optional_text(runtime.get("model"), "runtime.model"),
        effort=_optional_text(runtime.get("effort"), "runtime.effort"),
        sandbox_mode=parsed_sandbox_mode,
        network_access=network_access,
        runtime_env_include=_text_list(
            runtime_env.get("include", []), "runtime.env.include"
        ),
        wecom_bot_id=_optional_text(wecom.get("bot_id"), "channel.wecom.bot_id"),
        wecom_websocket_url=_optional_text(
            wecom.get("websocket_url"), "channel.wecom.websocket_url"
        ),
    )


def _optional_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ConfigurationError(f"{field_name} must be non-empty text")
    return value


def _text_list(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ConfigurationError(f"{field_name} must be an array of non-empty text")
    if len(set(value)) != len(value):
        raise ConfigurationError(f"{field_name} cannot contain duplicates")
    return tuple(value)


__all__ = [
    "CONFIG_FILENAME",
    "ConfigurationError",
    "NodeConfiguration",
    "load_node_configuration",
    "resolve_config_path",
]
