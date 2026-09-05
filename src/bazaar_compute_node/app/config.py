from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from uuid import UUID, uuid7

from ..core.actor import Mode
from ..core.paths import resolve_data_dir
from ..core.runtime import RuntimeSandboxMode

CONFIG_FILENAME = "config.toml"
CONFIG_VERSION = "3"
DEFAULT_AUDIT = "logging"
DEFAULT_STORAGE = "sqlite"
DEFAULT_DATABASE_FILENAME = "bcn.sqlite3"
_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_BARE_TOML_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True, slots=True)
class ChannelConfiguration:
    kind: str
    options: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        _required_text(self.kind, "agent.channel.kind")
        for key, value in self.options.items():
            if key.endswith("_env"):
                environment_name = _required_text(value, f"agent.channel.{key}")
                if not _ENVIRONMENT_NAME.fullmatch(environment_name):
                    raise ConfigurationError(
                        f"agent.channel.{key} must be a valid environment name"
                    )


@dataclass(frozen=True, slots=True)
class RuntimeConfiguration:
    kind: str
    model: str | None = None
    effort: str | None = None
    sandbox_mode: RuntimeSandboxMode = RuntimeSandboxMode.WORKSPACE_WRITE
    network_access: bool = True
    env: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    options: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        _required_text(self.kind, "agent.runtime.kind")
        _optional_text(self.model, "agent.runtime.model")
        _optional_text(self.effort, "agent.runtime.effort")
        if not isinstance(self.sandbox_mode, RuntimeSandboxMode):
            raise ConfigurationError("agent.runtime.sandbox_mode is invalid")
        if not isinstance(self.network_access, bool):
            raise ConfigurationError("agent.runtime.network_access must be a boolean")
        _validate_environment_names(
            (*self.env, *self.env.values()), "agent.runtime.env"
        )


@dataclass(frozen=True, slots=True)
class AgentConfiguration:
    id: str
    name: str
    channel: ChannelConfiguration
    runtimes: tuple[RuntimeConfiguration, ...]
    mode: Mode = Mode.SESSION
    idle_timeout_seconds: float = 0

    def __post_init__(self) -> None:
        _validate_agent_id(self.id, "agent.id")
        _required_text(self.name, "agent.name")
        if not self.runtimes:
            raise ConfigurationError("agent.runtime must define at least one runtime")
        if (
            isinstance(self.idle_timeout_seconds, bool)
            or not isinstance(self.idle_timeout_seconds, int | float)
            or not math.isfinite(self.idle_timeout_seconds)
            or self.idle_timeout_seconds < 0
        ):
            raise ConfigurationError(
                "agent.idle_timeout must be a non-negative finite number"
            )


@dataclass(frozen=True, slots=True)
class NodeConfiguration:
    """Version 3 persistent node configuration."""

    agents: tuple[AgentConfiguration, ...] = ()
    storage: str = DEFAULT_STORAGE
    audit: str = DEFAULT_AUDIT
    lang: str | None = None
    endpoint: str | None = None
    database_name: str | None = None
    version_check: bool = True
    version: str = CONFIG_VERSION

    def __post_init__(self) -> None:
        if self.version != CONFIG_VERSION:
            raise ConfigurationError(
                f"configuration version must be {CONFIG_VERSION!r}"
            )
        if not isinstance(self.version_check, bool):
            raise ConfigurationError("node.version_check must be a boolean")
        _required_text(self.storage, "node.storage")
        _required_text(self.audit, "node.audit")
        _optional_text(self.lang, "node.lang")
        _optional_text(self.endpoint, "node.endpoint")
        _optional_text(self.database_name, "node.database_name")
        _validate_database_name(self.database_name)
        ids = [agent.id for agent in self.agents]
        names = [agent.name for agent in self.agents]
        if len(set(ids)) != len(ids):
            raise ConfigurationError("agent.id values must be unique")
        if len(set(names)) != len(names):
            raise ConfigurationError("agent.name values must be unique")


@dataclass(frozen=True, slots=True)
class ControlConfiguration:
    endpoint: str | None = None


class ConfigurationError(ValueError):
    """Raised when the persistent bcn configuration is invalid."""


@dataclass(frozen=True, slots=True)
class _ConfigurationUpgrade:
    """The step that lifts a payload into the next configuration version."""

    apply: Callable[[Mapping[str, object]], dict[str, object]]
    state: _ConfigurationState


@dataclass(frozen=True, slots=True)
class _ConfigurationState:
    """One version in the configuration upgrade state machine.

    A state hands its payload to the next state, which is responsible for
    upgrading it. The state that has no upgrade left is the terminal one: a
    payload that reaches it is current.
    """

    version: str
    upgrade: _ConfigurationUpgrade | None

    @property
    def is_current(self) -> bool:
        return self.upgrade is None

    def advance(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        """Move the payload forward until it reaches the terminal state."""

        state = self
        while (upgrade := state.upgrade) is not None:
            payload = upgrade.apply(payload)
            state = upgrade.state
        return payload


def resolve_config_path() -> Path:
    return resolve_data_dir() / CONFIG_FILENAME


def load_node_configuration(
    path: Path | None = None,
) -> NodeConfiguration:
    """Load v3 configuration, advancing older input through the upgrade states."""

    path = (path or resolve_config_path()).expanduser()
    payload = _read_configuration(path)
    state = _configuration_state(_payload_version(payload))
    if state.is_current:
        return _parse_v3_configuration(payload)

    configuration = _parse_v3_configuration(state.advance(payload))
    _write_configuration(path, configuration)
    return configuration


def load_control_configuration(path: Path | None = None) -> ControlConfiguration:
    """Read node control settings without triggering a configuration upgrade."""

    path = (path or resolve_config_path()).expanduser()
    payload = _read_configuration(path)
    _configuration_state(_payload_version(payload))
    node = _table(payload.get("node", {}), "[node]")
    return ControlConfiguration(
        endpoint=_optional_text(node.get("endpoint"), "node.endpoint")
    )


def _payload_version(payload: Mapping[str, object]) -> str:
    version = payload.get("version", "1")
    if not isinstance(version, str):
        raise ConfigurationError("top-level version must be text")
    return version


def _read_configuration(path: Path) -> dict[str, object]:
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            path.parent.chmod(0o700)
        if not path.exists():
            return {}
        with path.open("rb") as config_file:
            payload = tomllib.load(config_file)
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ConfigurationError(f"cannot read {path}: {error}") from error
    return payload


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


def _parse_v3_configuration(payload: Mapping[str, object]) -> NodeConfiguration:
    node = _table(payload.get("node", {}), "[node]")
    if "channel" in node or "runtime" in node:
        raise ConfigurationError(
            "version 3 configuration cannot define node.channel or node.runtime"
        )
    raw_agents = payload.get("agent", [])
    if not isinstance(raw_agents, list):
        raise ConfigurationError("[[agent]] must be an array of TOML tables")
    agents = tuple(
        _parse_v3_agent(item, index=index)
        for index, item in enumerate(raw_agents, start=1)
    )
    version_check = node.get("version_check", True)
    if not isinstance(version_check, bool):
        raise ConfigurationError("node.version_check must be a boolean")
    return NodeConfiguration(
        version=CONFIG_VERSION,
        agents=agents,
        storage=_optional_text(node.get("storage"), "node.storage") or DEFAULT_STORAGE,
        audit=_optional_text(node.get("audit"), "node.audit") or DEFAULT_AUDIT,
        lang=_optional_text(node.get("lang"), "node.lang"),
        endpoint=_optional_text(node.get("endpoint"), "node.endpoint"),
        database_name=_optional_text(node.get("database_name"), "node.database_name"),
        version_check=version_check,
    )


def _parse_v3_agent(value: object, *, index: int) -> AgentConfiguration:
    table = _table(value, f"agent #{index}")
    channel = _table(table.get("channel"), f"agent #{index}.channel")
    channel_kind = _required_text(channel.get("kind"), f"agent #{index}.channel.kind")
    raw_runtimes = table.get("runtime")
    if not isinstance(raw_runtimes, list):
        raise ConfigurationError(
            f"agent #{index}.runtime must be an array of TOML tables"
        )
    runtimes: list[RuntimeConfiguration] = []
    for position, item in enumerate(raw_runtimes, start=1):
        runtime = _table(item, f"agent #{index}.runtime #{position}")
        runtime_kind = _required_text(
            runtime.get("kind"), f"agent #{index}.runtime #{position}.kind"
        )
        runtimes.append(
            _parse_runtime_configuration(
                runtime,
                runtime_kind,
                index=index,
                position=position,
            )
        )
    idle_timeout = table.get("idle_timeout", 0)
    if (
        isinstance(idle_timeout, bool)
        or not isinstance(idle_timeout, int | float)
        or not math.isfinite(idle_timeout)
        or idle_timeout < 0
    ):
        raise ConfigurationError(
            f"agent #{index}.idle_timeout must be a non-negative finite number"
        )
    raw_mode = table.get("mode", Mode.SESSION.value)
    try:
        mode = Mode(raw_mode)
    except ValueError as error:
        allowed = ", ".join(member.value for member in Mode)
        raise ConfigurationError(
            f"agent #{index}.mode must be one of: {allowed}"
        ) from error
    return AgentConfiguration(
        id=_required_text(table.get("id"), f"agent #{index}.id"),
        name=_required_text(table.get("name"), f"agent #{index}.name"),
        channel=ChannelConfiguration(
            kind=channel_kind,
            options=MappingProxyType(
                {key: item for key, item in channel.items() if key != "kind"}
            ),
        ),
        runtimes=tuple(runtimes),
        mode=mode,
        idle_timeout_seconds=float(idle_timeout),
    )


def _parse_runtime_configuration(
    runtime: Mapping[str, object],
    kind: str,
    *,
    index: int,
    position: int,
) -> RuntimeConfiguration:
    prefix = f"agent #{index}.runtime #{position}"
    sandbox_mode = runtime.get("sandbox_mode", RuntimeSandboxMode.WORKSPACE_WRITE.value)
    if not isinstance(sandbox_mode, str):
        raise ConfigurationError(f"{prefix}.sandbox_mode must be text")
    try:
        parsed_sandbox_mode = RuntimeSandboxMode(sandbox_mode)
    except ValueError as error:
        allowed = ", ".join(mode.value for mode in RuntimeSandboxMode)
        raise ConfigurationError(
            f"{prefix}.sandbox_mode must be one of: {allowed}"
        ) from error
    network_access = runtime.get("network_access", True)
    if not isinstance(network_access, bool):
        raise ConfigurationError(f"{prefix}.network_access must be a boolean")
    env = MappingProxyType(
        {
            key: _required_text(item, f"{prefix}.env.{key}")
            for key, item in _table(runtime.get("env", {}), f"{prefix}.env").items()
        }
    )
    _validate_environment_names((*env, *env.values()), f"{prefix}.env")
    standard_keys = {
        "kind",
        "model",
        "effort",
        "sandbox_mode",
        "network_access",
        "env",
    }
    return RuntimeConfiguration(
        kind=kind,
        model=_optional_text(runtime.get("model"), f"{prefix}.model"),
        effort=_optional_text(runtime.get("effort"), f"{prefix}.effort"),
        sandbox_mode=parsed_sandbox_mode,
        network_access=network_access,
        env=env,
        options=MappingProxyType(
            {key: item for key, item in runtime.items() if key not in standard_keys}
        ),
    )


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


# The upgrade states, newest first so each one can name the state it moves to.
# Adding a version means writing its upgrade, declaring it as the new terminal
# state, and giving the state that used to be terminal an upgrade into it.
_CONFIGURATION_V3 = _ConfigurationState(version="3", upgrade=None)
_CONFIGURATION_V2 = _ConfigurationState(
    version="2",
    upgrade=_ConfigurationUpgrade(apply=_v2_to_v3_payload, state=_CONFIGURATION_V3),
)
_CONFIGURATION_V1 = _ConfigurationState(
    version="1",
    upgrade=_ConfigurationUpgrade(apply=_v1_to_v2_payload, state=_CONFIGURATION_V2),
)
_CONFIGURATION_STATES = (
    _CONFIGURATION_V1,
    _CONFIGURATION_V2,
    _CONFIGURATION_V3,
)


def _configuration_state(version: str) -> _ConfigurationState:
    for state in _CONFIGURATION_STATES:
        if state.version == version:
            return state
    raise ConfigurationError(f"unsupported configuration version: {version}")


def _write_configuration(path: Path, configuration: NodeConfiguration) -> None:
    content = _serialize_configuration(configuration)
    temporary = path.with_name(f".{path.name}.{uuid7().hex}.tmp")
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            descriptor = None
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            path.chmod(0o600)
            parent_descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(parent_descriptor)
            finally:
                os.close(parent_descriptor)
    except OSError as error:
        raise ConfigurationError(f"cannot write {path}: {error}") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _serialize_configuration(configuration: NodeConfiguration) -> str:
    lines = [f"version = {_toml_value(configuration.version)}", "", "[node]"]
    lines.append(f"storage = {_toml_value(configuration.storage)}")
    lines.append(f"audit = {_toml_value(configuration.audit)}")
    if configuration.lang is not None:
        lines.append(f"lang = {_toml_value(configuration.lang)}")
    if configuration.database_name is not None:
        lines.append(f"database_name = {_toml_value(configuration.database_name)}")
    if configuration.endpoint is not None:
        lines.append(f"endpoint = {_toml_value(configuration.endpoint)}")
    lines.append(f"version_check = {_toml_value(configuration.version_check)}")

    for agent in configuration.agents:
        lines.extend(
            (
                "",
                "[[agent]]",
                f"id = {_toml_value(agent.id)}",
                f"name = {_toml_value(agent.name)}",
                f"mode = {_toml_value(agent.mode.value)}",
                f"idle_timeout = {_toml_value(agent.idle_timeout_seconds)}",
                "",
                "[agent.channel]",
                f"kind = {_toml_value(agent.channel.kind)}",
            )
        )
        for key in sorted(agent.channel.options):
            lines.append(
                f"{_toml_key(key)} = {_toml_value(agent.channel.options[key])}"
            )
        for runtime in agent.runtimes:
            lines.extend(
                (
                    "",
                    "[[agent.runtime]]",
                    f"kind = {_toml_value(runtime.kind)}",
                )
            )
            if runtime.model is not None:
                lines.append(f"model = {_toml_value(runtime.model)}")
            if runtime.effort is not None:
                lines.append(f"effort = {_toml_value(runtime.effort)}")
            lines.append(f"sandbox_mode = {_toml_value(runtime.sandbox_mode.value)}")
            lines.append(f"network_access = {_toml_value(runtime.network_access)}")
            for key in sorted(runtime.options):
                lines.append(f"{_toml_key(key)} = {_toml_value(runtime.options[key])}")
            if runtime.env:
                # a sub-table of the array element, so it has to come last or it
                # would swallow the runtime's own keys
                lines.extend(("", "[agent.runtime.env]"))
                for key in sorted(runtime.env):
                    lines.append(f"{_toml_key(key)} = {_toml_value(runtime.env[key])}")
    return "\n".join(lines) + "\n"


def _toml_key(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigurationError("configuration option names must be non-empty text")
    return value if _BARE_TOML_KEY.fullmatch(value) else json.dumps(value)


def _toml_value(value: object) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ConfigurationError("configuration cannot contain non-finite numbers")
        return repr(value)
    if isinstance(value, Mapping):
        rendered = ", ".join(
            f"{_toml_key(key)} = {_toml_value(item)}"
            for key, item in sorted(value.items())
        )
        return "{ " + rendered + " }"
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise ConfigurationError(
        f"unsupported configuration value type: {type(value).__name__}"
    )


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


def _validate_agent_id(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ConfigurationError(f"{field_name} must be a canonical UUIDv7")
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise ConfigurationError(f"{field_name} must be a UUIDv7") from error
    if parsed.version != 7 or str(parsed) != value:
        raise ConfigurationError(f"{field_name} must be a canonical UUIDv7")


__all__ = [
    "CONFIG_FILENAME",
    "CONFIG_VERSION",
    "DEFAULT_AUDIT",
    "DEFAULT_STORAGE",
    "AgentConfiguration",
    "ChannelConfiguration",
    "ConfigurationError",
    "ControlConfiguration",
    "NodeConfiguration",
    "RuntimeConfiguration",
    "load_control_configuration",
    "load_node_configuration",
    "resolve_config_path",
]
