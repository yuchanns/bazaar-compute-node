"""Storage services and lifespan-bound backend registration."""

from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
from functools import cache
from typing import Annotated, Protocol
lazy from collections.abc import Awaitable, Callable, Iterator

from bazaar_core import Context, Contrib, ServiceKey
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

lazy from .models import (
    Copy,
    Delete,
    Insert,
    Mutation,
    Record,
    SchemaOperation,
    Select,
    Update,
)

type BackendName = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_-]*$")]
_NAME = TypeAdapter(BackendName)


class StorageError(RuntimeError):
    """A backend could not complete a public storage operation."""


class StorageClosedError(StorageError):
    """The backend has finished its lifecycle."""


class StorageUnavailableError(StorageError):
    """The underlying data source is unavailable or busy."""


class StorageIntegrityError(StorageError):
    """A write violates the declared data constraints."""


class StorageQueryError(StorageError):
    """The data source could not execute the declared operation."""


class MigrationError(StorageError):
    """The stored migration history cannot be safely advanced."""


class Session(Protocol):
    async def table_exists(self, name: str) -> bool: ...

    async def select(self, request: Select) -> tuple[Record, ...]: ...

    async def copy(self, request: Copy) -> Mutation: ...

    async def insert(self, request: Insert) -> Mutation: ...

    async def update(self, request: Update) -> Mutation: ...

    async def delete(self, request: Delete) -> Mutation: ...

    async def apply_schema(self, operations: tuple[SchemaOperation, ...]) -> None: ...


class Database(Session, Protocol):
    async def transaction[T](self, operation: Callable[[Session], Awaitable[T]]) -> T:
        """Commit the operation together, rolling it back on failure or cancellation."""
        ...


class BackendRegistry:
    def __init__(self) -> None:
        self._backends: dict[str, Database] = {}

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._backends)

    def get(self, name: str) -> Database:
        return self._backends[name]

    @contextmanager
    def register(self, name: str, database: Database) -> Iterator[None]:
        name = _NAME.validate_python(name)
        if name in self._backends:
            raise StorageError(
                f"storage backend {name!r} is already registered; choose a unique backend name"
            )
        self._backends[name] = database
        try:
            yield
        finally:
            del self._backends[name]


STORAGE = ServiceKey[BackendRegistry]("storage.backends")
DB = ServiceKey[Database]("storage.database")


@cache
def backend_key(name: str) -> ServiceKey[Database]:
    return ServiceKey[Database](f"storage.backend.{_NAME.validate_python(name)}")


class DatabaseConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    backend: BackendName


@asynccontextmanager
async def _storage(ctx: Context):
    ctx.offer(STORAGE, BackendRegistry())
    yield


@asynccontextmanager
async def _database(ctx: Context, config: DatabaseConfig):
    ctx.offer(DB, ctx.accept(STORAGE).get(config.backend))
    yield


storage = Contrib(offers=[STORAGE], lifespan=_storage)
database = Contrib(
    config=DatabaseConfig,
    accepts=lambda config: [STORAGE, backend_key(config.backend)],
    offers=[DB],
    lifespan=_database,
)
