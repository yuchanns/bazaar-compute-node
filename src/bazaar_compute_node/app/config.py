from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from ..core.paths import resolve_data_dir

CONFIG_FILENAME = "config.toml"


@dataclass(frozen=True, slots=True)
class NodeConfiguration:
    """Optional startup settings loaded from the node's persistent config."""

    channel: str | None = None
    runtime: str | None = None
    storage: str | None = None
    audit: str | None = None
    endpoint: str | None = None
    model: str | None = None
    effort: str | None = None


class ConfigurationError(ValueError):
    """Raised when the persistent bcn configuration is invalid."""


def resolve_config_path() -> Path:
    return resolve_data_dir() / CONFIG_FILENAME


def load_node_configuration() -> NodeConfiguration:
    path = resolve_config_path()
    try:
        with path.open("rb") as config_file:
            payload = tomllib.load(config_file)
    except FileNotFoundError:
        return NodeConfiguration()
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ConfigurationError(f"cannot read {path}: {error}") from error

    node = payload.get("node", {})
    if not isinstance(node, dict):
        raise ConfigurationError("[node] must be a TOML table")
    runtime = payload.get("runtime", {})
    if not isinstance(runtime, dict):
        raise ConfigurationError("[runtime] must be a TOML table")
    return NodeConfiguration(
        channel=_optional_text(node.get("channel"), "node.channel"),
        runtime=_optional_text(node.get("runtime"), "node.runtime"),
        storage=_optional_text(node.get("storage"), "node.storage"),
        audit=_optional_text(node.get("audit"), "node.audit"),
        endpoint=_optional_text(node.get("endpoint"), "node.endpoint"),
        model=_optional_text(runtime.get("model"), "runtime.model"),
        effort=_optional_text(runtime.get("effort"), "runtime.effort"),
    )


def _optional_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ConfigurationError(f"{field_name} must be non-empty text")
    return value


__all__ = [
    "CONFIG_FILENAME",
    "ConfigurationError",
    "NodeConfiguration",
    "load_node_configuration",
    "resolve_config_path",
]
