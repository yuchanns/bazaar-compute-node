"""Version services and the V4-to-V5 field and package mapping."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
lazy import locale
lazy from collections.abc import Mapping
lazy from copy import deepcopy

from bazaar_contrib_config import AgentConfig
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator
lazy from bazaar_contrib_config import (
    ConfigCandidate,
    ConfigConverter,
    ConfigDocument,
    ConfigFormatError,
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


class _V4(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal["4"]
    node: _Node = Field(default_factory=_Node)
    agent: tuple[AgentConfig, ...] = ()


def migrate_v5(payload: Mapping[str, object]) -> ConfigCandidate:
    old = _V4.model_validate(payload)
    node = old.node
    entries: dict[str, EnabledContrib] = {}
    packages: dict[str, PackageRequirement] = {}
    changes: list[FieldChange] = []

    def add(name: str, package: str, config: dict[str, Any] | None = None) -> None:
        entries[name] = EnabledContrib(contrib=name, config=config or {})
        package = "bazaar-contrib-" + package
        packages[package] = PackageRequirement(package=package)

    add("status", "status")
    add("storage", "storage")
    add("sqlite", "sqlite", {"path": str(resolve_data_dir() / node.database_name)})
    add("database", "storage", {"backend": "sqlite"})
    add("schema", "schema")
    add("contrib-manager", "manager")
    changes.extend(
        [
            FieldChange(
                source="node.storage", target="contribs[database].config.backend"
            ),
            FieldChange(
                source="node.database_name", target="contribs[sqlite].config.path"
            ),
        ]
    )
    if node.audit == "logging":
        add("logging", "logging", dict(node.logging))
        changes.append(
            FieldChange(source="node.logging", target="contribs[logging].config")
        )
    if node.audit == "server" or node.control == "server":
        add(
            "starlink",
            "starlink",
            dict(node.server)
            | {"events": node.audit == "server", "requests": node.control == "server"},
        )
        changes.append(
            FieldChange(source="node.server", target="contribs[starlink].config")
        )
    changes.extend(
        [
            FieldChange(
                source="node.audit",
                target="contribs[logging]"
                if node.audit == "logging"
                else "contribs[starlink].config.events",
            ),
            FieldChange(
                source="node.control",
                target="contribs[starlink].config.requests"
                if node.control == "server"
                else "contribs[local-rpc]",
            ),
        ]
    )
    lang = node.lang
    if lang is None:
        try:
            system_language, _ = locale.getlocale()
        except ValueError, locale.Error:
            system_language = None
        lang = "zh-CN" if system_language == "zh_CN" else "en"
    lang = "zh-CN" if lang == "zh-CN" else "en"
    add("languages", "language")
    if lang == "zh-CN":
        add("language-zh-cn", "language-zh-cn")
    add("language", "language", {"backend": lang})
    changes.append(
        FieldChange(source="node.lang", target="contribs[language].config.backend")
    )
    add("commands", "invoke")
    add("local-rpc", "invoke", {"endpoint": node.endpoint} if node.endpoint else {})
    add("node-management", "node-management")
    changes.extend(
        [
            FieldChange(
                source="node.endpoint", target="contribs[local-rpc].config.endpoint"
            ),
        ]
    )
    if old.agent:
        for name, package in (
            ("messages", "messages"),
            ("collaboration", "collaboration"),
            ("timer-wheel", "timer-wheel"),
            ("reminder", "reminder"),
            ("channel-builders", "channel"),
            ("runtime-builders", "runtime"),
            ("agent", "agent"),
        ):
            add(name, package)
    for index, agent in enumerate(old.agent):
        for kind, providers, supported in (
            (
                "channel",
                agent.channel,
                {"telegram": "telegram", "lark": "lark", "wecom": "wecom"},
            ),
            ("runtime", agent.runtime, {"codex": "codex", "claudecode": "claude-code"}),
        ):
            for provider in providers:
                if provider.kind not in supported:
                    raise ConfigFormatError(
                        f"agent[{index}].{kind}: provider {provider.kind!r} needs an explicit builder and entry mapping"
                    )
                add(provider.kind + "-builder", supported[provider.kind])
                add(provider.kind, supported[provider.kind])
        changes.append(FieldChange(source=f"agent[{index}]", target=f"agent[{index}]"))
    document = ConfigDocument(contribs=tuple(entries.values()), agent=old.agent)
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
