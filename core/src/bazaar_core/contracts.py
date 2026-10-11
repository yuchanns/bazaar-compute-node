"""Public contrib declarations, service contracts, and lifecycle snapshots."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Annotated, Any, Protocol, cast
lazy from importlib.metadata import EntryPoint
lazy from typing import Literal
lazy from uuid import UUID

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    InstanceOf,
    TypeAdapter,
    field_validator,
)
lazy from packaging.specifiers import InvalidSpecifier, SpecifierSet

lazy from .bazaar import ContribHub
lazy from .instances import Enablement, _ContribInstance, _Registration

# Public API contract; patch releases keep this version unchanged.
API_VERSION = "0.3"
_NAME = TypeAdapter(Annotated[str, Field(min_length=1)])


@dataclass(frozen=True, slots=True)
class Starting:
    """An instance is waiting for accepted services or starting its lifespan."""


@dataclass(frozen=True, slots=True)
class Running:
    """An instance has supplied its services and accepts event callbacks."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Stopping[E]:
    """An instance is unwinding its lifespan, retaining its original exit reason."""

    phase: Literal["stopping", "exiting", "aborting", "stopped"] = "stopping"
    error: E | None = None
    exit_error: E | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Stopped[E](Stopping[E]):
    """An instance has finished releasing its resources."""

    phase: Literal["stopped"] = field(default="stopped", init=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class Failed[E](Stopping[E]):
    """An instance fault, retained through cleanup and after its resources exit."""

    error: E = field()


@dataclass(frozen=True, slots=True, kw_only=True)
class Blocked[E](Stopping[E]):
    """An instance cannot run because a required service or parent is unavailable."""

    error: E = field()
    blocked_by: UUID | None = None


type ContribState[E] = (
    Starting | Running | Stopping[E] | Stopped[E] | Failed[E] | Blocked[E]
)


class _Key[T]:
    __slots__ = ("_name",)

    def __init__(self, name: str) -> None:
        self._name = _NAME.validate_python(name, strict=True)

    @property
    def name(self: _Key[T]) -> str:
        return self._name

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.name!r})"


class ServiceKey[T](_Key[T]):
    """The typed service key used in Contrib.offers, Contrib.accepts, and Context."""


class EventKey[T](_Key[T]):
    """Identity shared by publishers and listeners of one typed payload."""


_ACCEPTS = TypeAdapter(
    tuple[ServiceKey[Any], ...], config=ConfigDict(arbitrary_types_allowed=True)
)

_NO_CONFIG = TypeAdapter(Annotated[dict[str, Any], Field(max_length=0)])


@dataclass(frozen=True, slots=True)
class ContribStatus:
    """An instance's identity and lifecycle state at notification time."""

    id: UUID
    name: str
    location: str
    state: ContribState[str]
    parent: UUID | None = None


@dataclass(frozen=True, slots=True)
class ContribChange:
    snapshots: tuple[ContribStatus, ...] = ()
    removed: tuple[UUID, ...] = ()


CONTRIB_CHANGED = EventKey[ContribChange]("contrib.changed")


class ContribError(ValueError):
    """An assembly or service declaration error with entry context."""


class Context(Protocol):
    """An instance's declared services, events, and owned compositions."""

    @property
    def id(self) -> UUID: ...

    @property
    def name(self) -> str: ...

    @property
    def snapshots(self) -> tuple[ContribStatus, ...]: ...

    def offer[T](self, key: ServiceKey[T], service: T) -> None: ...

    def accept[T](self, key: ServiceKey[T]) -> T: ...

    async def announce[T](self, event: EventKey[T], value: T) -> None: ...

    def enable(
        self,
        composition: Composition | Candidate,
        *,
        local: Sequence[ServiceKey[Any] | EventKey[Any]] = (),
    ) -> AbstractAsyncContextManager[Enablement]: ...

    def validate(
        self,
        composition: Composition,
        *,
        local: Sequence[ServiceKey[Any] | EventKey[Any]] = (),
        replacing: Candidate | None = None,
    ) -> Candidate: ...


CURRENT_CONTEXT = ContextVar[Context | None]("bazaar_core.context", default=None)


type EventHandler[T] = Callable[[Context, T], None | Awaitable[None]]


type Lifespan[C: BaseModel | None] = (
    Callable[[Context], AbstractAsyncContextManager[None]]
    | Callable[[Context, C], AbstractAsyncContextManager[None]]
)


type Accepts[C: BaseModel | None] = (
    Sequence[ServiceKey[Any]] | Callable[[C], Sequence[ServiceKey[Any]]]
)


class Contrib[C: BaseModel | None = None](BaseModel):
    """A contrib definition; its lifespan creates and releases runtime resources."""

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
    )

    description: str = ""
    api: str = ">=0.3,<0.4"
    offers: Sequence[ServiceKey[Any]] = ()
    accepts: Accepts[C] = ()
    config: (
        Annotated[
            type[C], BeforeValidator(TypeAdapter(type[BaseModel]).validate_python)
        ]
        | None
    ) = None
    lifespan: Lifespan[C] | None = None
    listen: Mapping[EventKey[Any], EventHandler[Any]] = Field(default_factory=dict)

    def validate_config(self, values: dict[str, Any]) -> C:
        """Validate entry parameters, returning None when no model is declared."""
        if self.config is None:
            _NO_CONFIG.validate_python(values)
            return cast("C", None)
        return cast("C", cast("type[BaseModel]", self.config).model_validate(values))

    def dependencies(self, config: C) -> tuple[ServiceKey[Any], ...]:
        """Resolve declared dependencies from validated entry parameters."""
        accepts = self.accepts(config) if callable(self.accepts) else self.accepts
        return _ACCEPTS.validate_python(accepts)

    @field_validator("api")
    @classmethod
    def valid_api(cls, value: str) -> str:
        try:
            SpecifierSet(value)
        except InvalidSpecifier as error:
            raise ValueError(
                f"invalid contrib API version requirement: {value!r}"
            ) from error
        return value

    @field_validator("offers")
    @classmethod
    def unique_offers(
        cls, value: Sequence[ServiceKey[Any]]
    ) -> Sequence[ServiceKey[Any]]:
        names = [key.name for key in value]
        if len(set(names)) != len(names):
            raise ValueError(
                f"each service provided by a contrib must have a unique name: {names!r}"
            )
        return tuple(value)


@dataclass(frozen=True, slots=True)
class ContribSource:
    """The distribution and import reference supplying an installed entry."""

    package: str
    version: str
    name: str
    value: str
    entry_point: EntryPoint

    @property
    def label(self) -> str:
        return f"{self.package} {self.version}: {self.name} = {self.value}"


class ContribEntry(BaseModel):
    """One contrib definition, its parameters, and its configuration location."""

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
    )

    name: str = Field(min_length=1)
    definition: Contrib[Any]
    config: dict[str, Any] = Field(default_factory=dict)
    location: str = Field(default="<composition>", min_length=1)
    bindings: dict[ServiceKey[Any] | EventKey[Any], UUID] = Field(default_factory=dict)
    required_offers: Sequence[ServiceKey[Any]] = ()
    source: ContribSource | None = None


class Composition(BaseModel):
    """Configured contrib entries for the hub to start."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, revalidate_instances="always"
    )

    entries: Sequence[ContribEntry] = ()
    diagnostics: Sequence[InstanceOf[ContribStatus]] = ()
    isolate_errors: bool = False


@dataclass(frozen=True, slots=True)
class Candidate:
    """A validated composition, usable once before the hub changes."""

    _instances: tuple[_ContribInstance, ...]
    _hub: ContribHub
    _revision: int
    _registrations: tuple[_Registration, ...]
    _enablement: Enablement
    _diagnostics: tuple[ContribStatus, ...] = ()

    @property
    def snapshots(self) -> tuple[ContribStatus, ...]:
        return (*self._diagnostics, *(instance.status for instance in self._instances))


COMPOSITION = ServiceKey[Composition | Candidate]("contrib.composition")
