"""Convert published V1 through V4 configuration documents."""

from __future__ import annotations

import re
from enum import StrEnum
lazy import math
lazy import os
lazy import sqlite3
lazy from collections.abc import Mapping, Sequence
lazy from pathlib import Path
lazy from uuid import uuid7

lazy from bazaar_contrib_config import ConfigFormatError as ConfigurationError

__all__ = ["_v1_to_v2_payload", "_v2_to_v3_payload", "_v3_to_v4_payload"]

DEFAULT_STORAGE = "sqlite"
DEFAULT_AUDIT = "logging"
DEFAULT_DATABASE_FILENAME = "bcn.sqlite3"
_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class RuntimeSandboxMode(StrEnum):
    WORKSPACE_WRITE = "workspace-write"
    DANGER_FULL_ACCESS = "danger-full-access"


def resolve_data_dir() -> Path:
    path = Path(os.environ.get("BCN_HOME") or Path.home())
    return path.expanduser().resolve() / ".bcn"


def _read_legacy_workspace_id(
    *,
    data_dir: Path,
    database_name: str | None,
) -> str | None:
    """Read the old workspace identity as one-time v1 migration input."""

    database_path = data_dir / (database_name or DEFAULT_DATABASE_FILENAME)
    if not database_path.is_file():
        return None
    try:
        connection = sqlite3.connect(
            f"{database_path.resolve().as_uri()}?mode=ro",
            uri=True,
        )
    except sqlite3.Error as error:
        raise ConfigurationError(
            f"cannot inspect legacy SQLite identity at {database_path}: {error}"
        ) from error
    try:
        table_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'node_state'"
        ).fetchone()
        if table_exists is None:
            return None
        row = connection.execute(
            "SELECT workspace_id FROM node_state WHERE singleton_key = 1"
        ).fetchone()
        if row is None:
            return None
        workspace_id = row[0]
        if not isinstance(workspace_id, str) or not workspace_id:
            raise ConfigurationError("legacy node_state.workspace_id is missing")
        return workspace_id
    except sqlite3.Error as error:
        raise ConfigurationError(
            f"cannot inspect legacy SQLite identity at {database_path}: {error}"
        ) from error
    finally:
        connection.close()


def _v1_to_v2_payload(payload: Mapping[str, object]) -> dict[str, object]:
    node = _table(payload.get("node", {}), "[node]")
    runtime = _table(payload.get("runtime", {}), "[runtime]")
    runtime_env = _table(runtime.get("env", {}), "[runtime.env]")
    channels = _table(payload.get("channel", {}), "[channel]")

    channel_kind = _optional_text(node.get("channel"), "node.channel")
    runtime_kind = _optional_text(node.get("runtime"), "node.runtime")
    if (channel_kind is None) != (runtime_kind is None):
        raise ConfigurationError(
            "legacy channel and runtime must be configured together"
        )

    storage = _optional_text(node.get("storage"), "node.storage") or DEFAULT_STORAGE
    audit = _optional_text(node.get("audit"), "node.audit") or DEFAULT_AUDIT
    endpoint = _optional_text(node.get("endpoint"), "node.endpoint")
    database_name = _optional_text(node.get("database_name"), "node.database_name")
    _validate_database_name(database_name)

    migrated_node: dict[str, object] = {"storage": storage, "audit": audit}
    if database_name is not None:
        migrated_node["database_name"] = database_name
    if endpoint is not None:
        migrated_node["endpoint"] = endpoint

    agents: list[dict[str, object]] = []
    if channel_kind is not None and runtime_kind is not None:
        channel_options = _table(
            channels.get(channel_kind, {}), f"[channel.{channel_kind}]"
        )
        migrated_channel = dict(channel_options)
        migrated_channel["kind"] = channel_kind
        if channel_kind == "telegram":
            migrated_channel.setdefault("token_env", "BCN_TELEGRAM_BOT_TOKEN")
        elif channel_kind == "wecom":
            migrated_channel.setdefault("secret_env", "BCN_WECOM_BOT_SECRET")

        raw_sandbox_mode = runtime.get(
            "sandbox_mode", RuntimeSandboxMode.WORKSPACE_WRITE.value
        )
        if not isinstance(raw_sandbox_mode, str):
            raise ConfigurationError("runtime.sandbox_mode must be text")
        try:
            sandbox_mode = RuntimeSandboxMode(raw_sandbox_mode)
        except ValueError as error:
            allowed = ", ".join(mode.value for mode in RuntimeSandboxMode)
            raise ConfigurationError(
                f"runtime.sandbox_mode must be one of: {allowed}"
            ) from error
        network_access = runtime.get("network_access", True)
        if not isinstance(network_access, bool):
            raise ConfigurationError("runtime.network_access must be a boolean")
        idle_timeout = runtime.get("idle_timeout", 0)
        if (
            isinstance(idle_timeout, bool)
            or not isinstance(idle_timeout, int | float)
            or not math.isfinite(idle_timeout)
            or idle_timeout < 0
        ):
            raise ConfigurationError(
                "runtime.idle_timeout must be a non-negative finite number"
            )
        env_include = _text_list(runtime_env.get("include", []), "runtime.env.include")
        _validate_environment_names(env_include, "runtime.env.include")

        migrated_runtime: dict[str, object] = {
            "kind": runtime_kind,
            "sandbox_mode": sandbox_mode.value,
            "network_access": network_access,
            "idle_timeout": float(idle_timeout),
        }
        model = _optional_text(runtime.get("model"), "runtime.model")
        if model is not None:
            migrated_runtime["model"] = model
        effort = _optional_text(runtime.get("effort"), "runtime.effort")
        if effort is not None:
            migrated_runtime["effort"] = effort
        if env_include:
            migrated_runtime["env_include"] = list(env_include)

        agent_id = (
            _read_legacy_workspace_id(
                data_dir=resolve_data_dir(),
                database_name=database_name,
            )
            if storage == "sqlite"
            else None
        )
        agents.append(
            {
                "id": agent_id or str(uuid7()),
                "name": "default",
                "channel": migrated_channel,
                "runtime": migrated_runtime,
            }
        )

    return {"version": "2", "node": migrated_node, "agent": agents}


def _v2_to_v3_payload(payload: Mapping[str, object]) -> dict[str, object]:
    raw_agents = payload.get("agent", [])
    if not isinstance(raw_agents, list):
        raise ConfigurationError("[[agent]] must be an array of TOML tables")

    agents: list[dict[str, object]] = []
    for index, item in enumerate(raw_agents, start=1):
        agent = dict(_table(item, f"agent #{index}"))
        runtime = dict(_table(agent.get("runtime"), f"agent #{index}.runtime"))
        idle_timeout = runtime.pop("idle_timeout", 0)
        if (
            isinstance(idle_timeout, bool)
            or not isinstance(idle_timeout, int | float)
            or not math.isfinite(idle_timeout)
            or idle_timeout < 0
        ):
            raise ConfigurationError(
                f"agent #{index}.runtime.idle_timeout "
                "must be a non-negative finite number"
            )
        agent["idle_timeout"] = float(idle_timeout)
        env_include = _text_list(
            runtime.pop("env_include", []), f"agent #{index}.runtime.env_include"
        )
        _validate_environment_names(env_include, f"agent #{index}.runtime.env_include")
        if env_include:
            included: dict[str, object] = {name: name for name in env_include}
            existing = runtime.get("env")
            # the two spellings merge instead of one dropping the other; a key
            # written both ways keeps the newer env value
            runtime["env"] = (
                included | existing if isinstance(existing, dict) else included
            )
        agent["runtime"] = [runtime]
        agents.append(agent)

    return dict(payload) | {"version": "3", "agent": agents}


def _v3_to_v4_payload(payload: Mapping[str, object]) -> dict[str, object]:
    raw_agents = payload.get("agent", [])
    if not isinstance(raw_agents, list):
        raise ConfigurationError("[[agent]] must be an array of TOML tables")

    agents: list[dict[str, object]] = []
    for index, item in enumerate(raw_agents, start=1):
        agent = dict(_table(item, f"agent #{index}"))
        agent["channel"] = [_table(agent.get("channel"), f"agent #{index}.channel")]
        agents.append(agent)

    return dict(payload) | {"version": "4", "agent": agents}


def _table(value: object, field_name: str) -> dict[str, object]:
    if value is None:
        raise ConfigurationError(f"{field_name} must be a TOML table")
    if not isinstance(value, dict):
        raise ConfigurationError(f"{field_name} must be a TOML table")
    return value


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigurationError(f"{field_name} must be non-empty text")
    return value


def _optional_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, field_name)


def _text_list(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ConfigurationError(f"{field_name} must be an array of non-empty text")
    if len(set(value)) != len(value):
        raise ConfigurationError(f"{field_name} cannot contain duplicates")
    return tuple(value)


def _validate_environment_names(values: Sequence[str], field_name: str) -> None:
    for value in values:
        if not _ENVIRONMENT_NAME.fullmatch(value):
            raise ConfigurationError(
                f"{field_name} contains an invalid environment name: {value}"
            )


def _validate_database_name(value: str | None) -> None:
    if value is None:
        return
    if value in {".", ".."} or "/" in value or "\\" in value:
        raise ConfigurationError("node.database_name must be a single path component")
