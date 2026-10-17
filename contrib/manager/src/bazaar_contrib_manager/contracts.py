"""Catalog, installed package, and management operation boundaries."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal
lazy from uuid import UUID, uuid7

from pydantic import (
    AnyUrl,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    UrlConstraints,
    field_validator,
)
lazy from bazaar_contrib_config import ContribPackage
lazy from packaging.specifiers import SpecifierSet
lazy from packaging.utils import canonicalize_name

SOURCE = TypeAdapter(
    Annotated[AnyUrl, UrlConstraints(allowed_schemes=["http", "https", "file"])]
)


class Package(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    package: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")]
    source: str
    version: str = ""

    @field_validator("package")
    @classmethod
    def name(cls, value: str) -> str:
        return str(canonicalize_name(value))

    @field_validator("source")
    @classmethod
    def url(cls, value: str) -> str:
        SOURCE.validate_python(value)
        return value

    @field_validator("version")
    @classmethod
    def specifier(cls, value: str) -> str:
        SpecifierSet(value)
        return value


class CatalogEntry(Package):
    indexes: tuple[str, ...] = ()


class IndexDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    contribs: tuple[Package, ...]


class IndexStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    location: str
    read_at: str | None = None
    cached: bool = False
    error: str | None = None
    entries: tuple[Package, ...] = ()


class EntryInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    value: str
    config_schema: dict[str, Any] | None = None
    error: str | None = None


class InstalledPackage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    package: str
    version: str
    summary: str = ""
    url: str | None = None
    entries: tuple[EntryInfo, ...] = ()
    requirements: tuple[str, ...] = ()


class EnvironmentInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    prefix: Path
    python: Path
    base_python: Path
    packages: tuple[InstalledPackage, ...]


class EnvironmentPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    host: Package
    packages: tuple[ContribPackage, ...] = ()


class Operation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID = Field(default_factory=uuid7)
    kind: str
    target: str
    phase: Literal[
        "queued",
        "resolving",
        "preflight",
        "installing",
        "saving",
        "applying",
        "complete",
        "failed",
        "cancelled",
    ] = "queued"
    package: str | None = None
    version: str | None = None
    installed: bool = False
    config_saved: bool = False
    applied: bool = False
    pending_restart: bool = False
    environment_changed: bool = False
    error: str | None = None
    recovery: str | None = None


class ManagerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: Path | None = None
    host_source: str = "https://pypi.org/simple/"

    @field_validator("host_source")
    @classmethod
    def source(cls, value: str) -> str:
        SOURCE.validate_python(value)
        return value


class ManagementError(ValueError):
    """A request could not be completed; the operation retains committed stages."""
