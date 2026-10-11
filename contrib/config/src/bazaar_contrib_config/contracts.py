"""Configuration carriers, version converters, and the V5 document boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Literal, Protocol
lazy from collections.abc import Mapping
lazy from typing import Self

from bazaar_core import ServiceKey
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
lazy from packaging.specifiers import SpecifierSet


class ConfigConflict(ValueError):
    """The source changed since the caller's snapshot."""


class ConfigUnavailable(OSError):
    """The source cannot be accessed; its current content is unknown."""


class ConfigFormatError(ValueError):
    """A document or candidate does not satisfy its declared format."""


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
    contrib: str = Field(min_length=1)
    config: dict[str, Any] = Field(default_factory=dict)


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
    contribs: tuple[EnabledContrib, ...] = ()
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
