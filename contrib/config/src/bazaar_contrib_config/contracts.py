"""Configuration carriers, version converters, and the V5 document boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Literal, Protocol
lazy from collections.abc import Mapping
lazy from typing import Self

from bazaar_core import EventKey, ServiceKey
from pydantic import (
    AnyUrl,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
lazy from packaging.requirements import Requirement
lazy from packaging.specifiers import SpecifierSet
lazy from packaging.utils import canonicalize_name

CONFIG_PREPARE = EventKey[None]("config.prepare")


class ConfigConflict(ValueError):
    """The source changed since the caller's snapshot."""


class ConfigUnavailable(OSError):
    """The source cannot be accessed; its current content is unknown."""


class ConfigFormatError(ValueError):
    """A document or candidate does not satisfy its declared format."""


class ConfigDependencyError(ConfigFormatError):
    """New missing-service dependencies prevent a configuration commit."""


class ConfigCommitUnknown(OSError):
    """A write may have committed; read the source before retrying."""


@dataclass(frozen=True, slots=True)
class ConfigSnapshot:
    """A stored document and the revision used for conditional writes."""

    payload: Mapping[str, object]
    revision: str


class ConfigStore(Protocol):
    async def read(self) -> ConfigSnapshot | None: ...

    async def write(
        self, payload: Mapping[str, object], *, expected_revision: str | None
    ) -> ConfigSnapshot: ...


class FieldChange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source: str
    target: str


class PackageRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    package: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")]
    source: str = "https://pypi.org/simple/"
    version: str = "==0.3.0"

    @field_validator("version")
    @classmethod
    def requirement(cls, value: str) -> str:
        SpecifierSet(value)
        return value


class ConfigCandidate(BaseModel):
    """A converted document, its field changes, and required packages."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    payload: dict[str, Any]
    changes: tuple[FieldChange, ...] = ()
    required_packages: tuple[PackageRequirement, ...] = ()


class ConfigConverter(Protocol):
    def convert(self, payload: Mapping[str, object]) -> ConfigCandidate: ...


class EnabledContrib(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    entry: str = Field(min_length=1)
    config: dict[str, Any] = Field(default_factory=dict)


class ContribPackage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    package: str = ""
    source: str = ""
    enabled: tuple[EnabledContrib, ...] = ()

    @field_validator("package")
    @classmethod
    def dependency(cls, value: str) -> str:
        return str(Requirement(value)) if value else value

    @field_validator("source")
    @classmethod
    def index(cls, value: str) -> str:
        if value and AnyUrl(value).scheme not in ("http", "https", "file"):
            raise ValueError("package source must be an HTTP(S) or file URL")
        return value

    @model_validator(mode="after")
    def declaration(self) -> Self:
        if not self.enabled and not self.package:
            raise ValueError("a contrib entry or package dependency is required")
        return self

    @property
    def distribution(self) -> str:
        return (
            str(canonicalize_name(Requirement(self.package).name))
            if self.package
            else ""
        )

    @property
    def applies(self) -> bool:
        requirement = Requirement(self.package) if self.package else None
        return (
            requirement is None
            or requirement.marker is None
            or requirement.marker.evaluate()
        )


class ProviderConfig(BaseModel):
    """Provider parameters stay with their entity until its builder reads them."""

    model_config = ConfigDict(extra="allow", frozen=True)
    kind: str = Field(min_length=1)


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    mode: Literal["session", "dangerous_individual"] = "session"
    idle_timeout: float = Field(default=0, ge=0, allow_inf_nan=False)
    channel: tuple[ProviderConfig, ...] = ()
    runtime: tuple[ProviderConfig, ...] = ()


class ConfigDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal["5"] = "5"
    contrib_indexes: tuple[str, ...] = ()
    contribs: tuple[ContribPackage, ...] = ()
    agent: tuple[AgentConfig, ...] = ()

    @model_validator(mode="after")
    def identities(self) -> Self:
        for field in ("id", "name"):
            values = [getattr(agent, field) for agent in self.agent]
            if len(values) != len(set(values)):
                raise ValueError(f"agent.{field} values must be unique")
        return self


CONFIG_STORE = ServiceKey[ConfigStore]("config.store")
CONFIG_V1 = ServiceKey[ConfigConverter]("config.version.1")
CONFIG_V2 = ServiceKey[ConfigConverter]("config.version.2")
CONFIG_V3 = ServiceKey[ConfigConverter]("config.version.3")
CONFIG_V4 = ServiceKey[ConfigConverter]("config.version.4")
CONFIG_V5 = ServiceKey[ConfigConverter]("config.version.5")
CONFIG_CURRENT = CONFIG_V5
