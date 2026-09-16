"""The server's own configuration file."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class ConfigurationError(ValueError):
    """Raised when the server configuration is invalid."""


class ServerConfiguration(BaseModel):
    """What `~/.bcs/config.toml` says; the file is the boundary, this checks it."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    listen: str = Field(default="127.0.0.1:8765", pattern=r"^.+:\d{1,5}$")
    retention_days: int = Field(default=30, ge=1, strict=True)
    lang: str | None = Field(default=None, min_length=1)
    storage: str = Field(default="sqlite", min_length=1)
    # a storage's own settings live in a table named after it
    storage_options: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _take_storage_table(cls, payload: Any) -> Any:
        if isinstance(payload, dict) and "storage_options" not in payload:
            storage = payload.get("storage", "sqlite")
            table = payload.get(storage, {}) if isinstance(storage, str) else {}
            return {**payload, "storage_options": table}
        return payload

    @property
    def listen_host(self) -> str:
        return self.listen.rpartition(":")[0]

    @property
    def listen_port(self) -> int:
        return int(self.listen.rpartition(":")[2])


def resolve_data_dir() -> Path:
    return Path.home() / ".bcs"


def resolve_config_path() -> Path:
    return resolve_data_dir() / "config.toml"


def load_configuration(path: Path | None = None) -> ServerConfiguration:
    """Read the configuration, writing the defaults first when there is none."""

    path = (path or resolve_config_path()).expanduser()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(_serialize(ServerConfiguration()))
        return ServerConfiguration()
    try:
        return ServerConfiguration.model_validate(
            tomllib.loads(path.read_text(encoding="utf-8"))
        )
    except (OSError, tomllib.TOMLDecodeError, ValidationError) as error:
        raise ConfigurationError(f"cannot read {path}: {error}") from error


def _serialize(configuration: ServerConfiguration) -> str:
    lines = [
        f'listen = "{configuration.listen}"',
        f"retention_days = {configuration.retention_days}",
        f'storage = "{configuration.storage}"',
    ]
    if configuration.lang is not None:
        lines.append(f'lang = "{configuration.lang}"')
    return "\n".join(lines) + "\n"


__all__ = [
    "ConfigurationError",
    "ServerConfiguration",
    "load_configuration",
    "resolve_config_path",
    "resolve_data_dir",
]
