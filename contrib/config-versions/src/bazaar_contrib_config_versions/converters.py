"""Version services and the V4-to-V5 field and package mapping."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
lazy import sys
lazy from collections.abc import Mapping
lazy from copy import deepcopy
lazy from hashlib import sha256
lazy from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator
lazy from bazaar_contrib_config import (
    ConfigCandidate,
    ConfigConverter,
    ConfigDocument,
    ConfigFormatError,
    ContribPackage,
    EnabledContrib,
    FieldChange,
    PackageRequirement,
)

lazy from .legacy import (
    _v1_to_v2_payload,
    _v2_to_v3_payload,
    _v3_to_v4_payload,
    resolve_data_dir,
)

_VERSION = TypeAdapter(Literal["1", "2", "3", "4", "5"])


class _Node(BaseModel):
    model_config = ConfigDict(extra="forbid")
    storage: str = "sqlite"
    database_name: str = "bcn.sqlite3"
    audit: Literal["logging", "server"] = "logging"
    control: Literal["none", "server"] = "none"
    logging: dict[str, Any] = Field(default_factory=dict)
    server: dict[str, Any] = Field(default_factory=dict)
    lang: str | None = Field(default=None, min_length=1)
    endpoint: str | None = None
    version_check: bool = True

    @field_validator("database_name")
    @classmethod
    def database_file(cls, value: str) -> str:
        if value in {".", ".."} or "/" in value or "\\" in value:
            raise ValueError("node.database_name must be a single path component")
        return value


class _ProviderV4(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)
    kind: str = Field(min_length=1)


class _AgentV4(BaseModel):
    """Agent settings in the V4 configuration format."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    mode: Literal["session", "dangerous_individual"] = "session"
    idle_timeout: float = Field(default=0, ge=0, allow_inf_nan=False)
    channel: tuple[_ProviderV4, ...] = ()
    runtime: tuple[_ProviderV4, ...] = ()


class _V4(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal["4"]
    node: _Node = Field(default_factory=_Node)
    agent: tuple[_AgentV4, ...] = ()


def migrate_v5(payload: Mapping[str, object]) -> ConfigCandidate:
    old = _V4.model_validate(payload)
    node = old.node
    entries: dict[str, dict[str, EnabledContrib]] = {}
    packages: dict[str, PackageRequirement] = {}
    changes: list[FieldChange] = []

    def add(name: str, package: str, config: dict[str, Any] | None = None) -> None:
        package = "bazaar-contrib-" + package
        requirement = PackageRequirement(package=package)
        entries.setdefault(package, {})[name] = EnabledContrib(
            entry=name, config=config or {}
        )
        packages[package] = requirement

    add("status", "status")
    add("storage", "storage")
    add("sqlite", "sqlite", {"path": str(resolve_data_dir() / node.database_name)})
    add("database", "storage", {"backend": "sqlite"})
    add("schema", "schema")
    if old.agent:
        add("channel-builders", "channel")
        add("harness-builders", "harness")
        add("node-migrations", "node-migrations")
    add("timer-wheel", "timer-wheel")
    add("contrib-manager", "manager")
    add("audit", "audit")
    changes.extend(
        [
            FieldChange(
                source="node.storage",
                target="contribs[bazaar-contrib-storage].enabled[database].config.backend",
            ),
            FieldChange(
                source="node.database_name",
                target="contribs[bazaar-contrib-sqlite].enabled[sqlite].config.path",
            ),
        ]
    )
    if node.audit == "logging":
        add("logging", "logging", dict(node.logging))
        changes.extend(
            [
                FieldChange(
                    source="node.logging",
                    target="contribs[bazaar-contrib-logging].enabled[logging].config",
                ),
                FieldChange(
                    source="node.audit",
                    target="contribs[bazaar-contrib-logging].enabled[logging]",
                ),
            ]
        )
    add("commands", "invoke")
    endpoint = node.endpoint
    if endpoint and sys.platform == "win32":
        # V4 identified its native pipe by a configured filesystem path.
        identity = str(Path(endpoint).expanduser().absolute()).casefold()
        endpoint = rf"\\.\pipe\bcn-{sha256(identity.encode()).hexdigest()[:32]}"
    endpoint = {"endpoint": endpoint} if endpoint else {}
    add("sidecar", "sidecar")
    transport = "sidecar-windows" if sys.platform == "win32" else "sidecar-unix"
    add(transport, transport, endpoint)
    add("node-management", "node-management")
    changes.extend(
        [
            FieldChange(
                source="node.endpoint",
                target=f"contribs[bazaar-contrib-{transport}].enabled[{transport}].config.endpoint",
            ),
        ]
    )
    agents = []
    for index, agent in enumerate(old.agent):
        for provider in agent.channel:
            add(f"{provider.kind}-builder", provider.kind)
        for provider in agent.runtime:
            package = "claude-code" if provider.kind == "claudecode" else provider.kind
            add(f"{provider.kind}-builder", package)
        item = agent.model_dump(mode="json")
        item["harness"] = item.pop("runtime")
        agents.append(item)
        changes.append(
            FieldChange(
                source=f"agent[{index}]",
                target=f"contribs[bazaar-contrib-agent].enabled[agent].config.agents[{index}]",
            )
        )
    if agents:
        add("agent", "agent", {"agents": agents})
    document = ConfigDocument(
        contribs=tuple(
            ContribPackage(
                package=package + packages[package].version,
                enabled=tuple(values.values()),
            )
            for package, values in entries.items()
        ),
    )
    return ConfigCandidate(
        payload=document.model_dump(mode="json", exclude_none=True),
        changes=tuple(changes),
        required_packages=tuple(packages.values()),
    )


@dataclass(frozen=True, slots=True)
class Converter:
    version: str
    previous: ConfigConverter | None = None

    def convert(self, payload: Mapping[str, object]) -> ConfigCandidate:
        input_version = _VERSION.validate_python(payload.get("version", "1"))
        if input_version == self.version:
            if self.version == "5":
                ConfigDocument.model_validate(payload)
            return ConfigCandidate(
                payload=deepcopy(dict(payload)) | {"version": input_version}
            )
        if int(input_version) > int(self.version) or self.previous is None:
            raise ConfigFormatError(
                f"version {input_version} cannot be read by config-v{self.version}"
            )
        candidate = self.previous.convert(payload)
        if self.version == "5":
            result = migrate_v5(candidate.payload)
        else:
            apply = {
                "2": _v1_to_v2_payload,
                "3": _v2_to_v3_payload,
                "4": _v3_to_v4_payload,
            }[self.version]
            result = ConfigCandidate(
                payload=apply(candidate.payload),
                changes=(
                    FieldChange(
                        source=f"version {int(self.version) - 1}",
                        target=f"version {self.version}",
                    ),
                ),
            )
        return result.model_copy(
            update={
                "changes": (*candidate.changes, *result.changes),
                "required_packages": (
                    *candidate.required_packages,
                    *result.required_packages,
                ),
            }
        )
