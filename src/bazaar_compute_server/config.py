"""The server's own configuration file."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import SplitResult, urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class ConfigurationError(ValueError):
    """Raised when the server configuration is invalid."""


class ServerConfiguration(BaseModel):
    """What `~/.bcs/config.toml` says; the file is the boundary, this checks it."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    # host and port, read the way a URL is: `127.0.0.1:8765`, `[::1]:8765`,
    # or the same with a scheme in front
    listen: str = "127.0.0.1:8765"
    retention_days: int = Field(default=30, ge=1, strict=True)
    storage: str = Field(default="sqlite", min_length=1)
    # a storage's own settings live in a table named after it
    storage_options: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _listen_names_a_host_and_port(self) -> ServerConfiguration:
        try:
            parts = _parsed(self.listen)
            host, port = parts.hostname, parts.port
        except ValueError as error:
            raise ValueError(
                f"listen {self.listen!r} is not host:port: {error}"
            ) from error
        if not host or port is None:
            raise ValueError(f"listen {self.listen!r} is not host:port")
        return self

    @property
    def listen_host(self) -> str:
        return _parsed(self.listen).hostname or ""

    @property
    def listen_port(self) -> int:
        return _parsed(self.listen).port or 0


def _parsed(listen: str) -> SplitResult:
    return urlsplit(listen if "//" in listen else f"//{listen}")


def resolve_data_dir() -> Path:
    return Path.home() / ".bcs"


def resolve_config_path() -> Path:
    return resolve_data_dir() / "config.toml"


def load_configuration(path: Path | None = None) -> ServerConfiguration:
    """Read the configuration, writing the defaults first when there is none."""

    path = (path or resolve_config_path()).expanduser()
    if not path.exists():
        write_private(path, _serialize(ServerConfiguration()).encode("utf-8"))
        return ServerConfiguration()
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
        # a storage's own settings live in a table named after it
        storage = payload.get("storage", "sqlite")
        payload["storage_options"] = (
            payload.get(storage, {}) if isinstance(storage, str) else {}
        )
        return ServerConfiguration.model_validate(payload)
    except (OSError, tomllib.TOMLDecodeError, ValidationError) as error:
        raise ConfigurationError(f"cannot read {path}: {error}") from error


def write_private(path: Path, content: bytes) -> None:
    """Put the content at the path whole, readable by the owner alone: what
    is there is either the old file or the new one, never part of one."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _serialize(configuration: ServerConfiguration) -> str:
    lines = [
        f'listen = "{configuration.listen}"',
        f"retention_days = {configuration.retention_days}",
        f'storage = "{configuration.storage}"',
    ]
    return "\n".join(lines) + "\n"


__all__ = [
    "ConfigurationError",
    "ServerConfiguration",
    "load_configuration",
    "resolve_config_path",
    "resolve_data_dir",
]
