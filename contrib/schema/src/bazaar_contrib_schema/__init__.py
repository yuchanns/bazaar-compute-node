"""Transactional, version-based migrations registered by business plugins."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Annotated, Self, cast
lazy import asyncio
lazy from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
lazy from time import monotonic_ns, time_ns

from bazaar_contrib_storage import (
    DB,
    Column,
    ColumnDefinition,
    Compare,
    Copy,
    CreateTable,
    Delete,
    Identifier,
    In,
    Insert,
    MigrationError,
    Mutation,
    RenameTable,
    SchemaOperation,
    Select,
    Sort,
    Table,
    Update,
    Value,
)
from bazaar_core import CURRENT_CONTEXT, Context, Contrib, ServiceKey
from packaging.utils import canonicalize_name
from pydantic import BaseModel, ConfigDict, Field, model_validator
lazy from bazaar_contrib_storage import Database, Record, Session

__all__ = [
    "SCHEMA",
    "Dependency",
    "Migration",
    "ModuleSchema",
    "Progress",
    "Schema",
    "schema",
]

type Package = Annotated[str, Field(pattern=r"^[a-z0-9]+(?:[-.][a-z0-9]+)*$")]


class Migration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: int = Field(gt=0)
    name: str = Field(min_length=1)
    operations: tuple[SchemaOperation, ...] = ()
    apply: Callable[[Session], Awaitable[None]] | None = None

    @model_validator(mode="after")
    def implementation(self) -> Self:
        if not self.operations and self.apply is None:
            raise ValueError("a migration requires operations or an apply callback")
        return self


class Progress(BaseModel):
    """Identify a module's version records in an existing database."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    table: Identifier
    identity: str = Field(min_length=1)
    version: Identifier = "version"
    name: Identifier = "migration_name"
    package: Package | None = None


class Dependency(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    package: Package
    version: int = Field(gt=0)


def operation_tables(operation: SchemaOperation) -> tuple[str, ...]:
    if isinstance(operation, CreateTable):
        return (operation.table.name,)
    if isinstance(operation, RenameTable):
        return (operation.table, operation.name)
    return (operation.table,)


class ModuleSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tables: tuple[Identifier, ...] = Field(min_length=1)
    migrations: tuple[Migration, ...] = Field(min_length=1)
    baseline: Migration | None = None
    progress: Progress | None = None
    predecessor: Dependency | None = None
    dependencies: tuple[Dependency, ...] = ()

    @property
    def version(self) -> int:
        return self.migrations[-1].version

    @model_validator(mode="after")
    def ownership(self) -> Self:
        if tuple(item.version for item in self.migrations) != tuple(
            range(1, len(self.migrations) + 1)
        ):
            raise ValueError(
                "module migrations must have consecutive versions starting at 1"
            )
        if len(set(self.tables)) != len(self.tables):
            raise ValueError("module table names must be unique")
        if self.baseline is not None and self.baseline.version > self.version:
            raise ValueError("baseline version must belong to the module's migrations")
        if self.predecessor is not None and self.baseline is None:
            raise ValueError("a predecessor requires a baseline for ownership handoff")
        migrations = (
            (*self.migrations, self.baseline)
            if self.baseline is not None
            else self.migrations
        )
        for migration in migrations:
            for operation in migration.operations:
                for table in operation_tables(operation):
                    if table not in self.tables:
                        raise ValueError(
                            f"migration operation uses unowned table {table!r}"
                        )
        return self


_LEDGER = Table(
    name="contrib_schema_migrations",
    columns=(
        ColumnDefinition(name="package", type="text"),
        ColumnDefinition(name="version", type="integer"),
        ColumnDefinition(name="name", type="text"),
        ColumnDefinition(name="applied_at_ms", type="integer"),
        ColumnDefinition(name="duration_ms", type="integer"),
    ),
    primary_key=("package", "version"),
)
_OWNERS = Table(
    name="contrib_schema_tables",
    columns=(
        ColumnDefinition(name="name", type="text"),
        ColumnDefinition(name="package", type="text"),
    ),
    primary_key=("name",),
)


class MigrationSession:
    """Constrain migration writes to the module's declared tables."""

    def __init__(self, session: Session, package: str, tables: tuple[str, ...]) -> None:
        self._session = session
        self._package = package
        self._tables = tables

    def _require_table(self, table: str) -> None:
        if table not in self._tables:
            raise MigrationError(
                f"{self._package} migration uses unowned table {table!r}"
            )

    async def table_exists(self, name: str) -> bool:
        return await self._session.table_exists(name)

    async def select(self, request: Select) -> tuple[Record, ...]:
        return await self._session.select(request)

    async def insert(self, request: Insert) -> Mutation:
        self._require_table(request.table)
        return await self._session.insert(request)

    async def copy(self, request: Copy) -> Mutation:
        self._require_table(request.table)
        return await self._session.copy(request)

    async def update(self, request: Update) -> Mutation:
        self._require_table(request.table)
        return await self._session.update(request)

    async def delete(self, request: Delete) -> Mutation:
        self._require_table(request.table)
        return await self._session.delete(request)

    async def apply_schema(self, operations: tuple[SchemaOperation, ...]) -> None:
        for operation in operations:
            for table in operation_tables(operation):
                self._require_table(table)
        await self._session.apply_schema(operations)


class Schema:
    def __init__(self, database: Database) -> None:
        self._database = database
        self._modules: dict[str, ModuleSchema] = {}
        self._lock = asyncio.Lock()

    @property
    def modules(self) -> Mapping[str, ModuleSchema]:
        return dict(self._modules)

    async def start(self) -> None:
        await self._database.apply_schema(
            (
                CreateTable(table=_LEDGER, if_not_exists=True),
                CreateTable(table=_OWNERS, if_not_exists=True),
            )
        )

    async def _version(
        self, session: Session, package: str, progress: Progress | None
    ) -> int:
        for identity in (
            package,
            *((progress.package,) if progress is not None and progress.package else ()),
        ):
            applied = await session.select(
                Select(
                    table=_LEDGER.name,
                    where=Compare(
                        left=Column(name="package"),
                        operator="=",
                        right=Value(value=identity),
                    ),
                    order_by=(
                        Sort(expression=Column(name="version"), descending=True),
                    ),
                    limit=1,
                )
            )
            if applied:
                version = cast(int, applied[0]["version"])
                if identity != package:
                    await self._record(
                        session, package, version, cast(str, applied[0]["name"])
                    )
                return version
        if progress is not None and await session.table_exists(progress.table):
            history = await session.select(
                Select(
                    table=progress.table,
                    columns={
                        "version": Column(name=progress.version),
                        "name": Column(name=progress.name),
                    },
                    order_by=(Sort(expression=Column(name=progress.version)),),
                )
            )
            if history and history[0]["name"] == progress.identity:
                versions = tuple(row["version"] for row in history)
                if versions != tuple(range(1, len(history) + 1)):
                    raise MigrationError(
                        f"{package} existing migration ledger has a version gap"
                    )
                version = cast(int, history[-1]["version"])
                await self._record(
                    session, package, version, cast(str, history[-1]["name"])
                )
                return version
        return 0

    async def _record(
        self,
        session: Session,
        package: str,
        version: int,
        name: str,
        duration_ms: int = 0,
    ) -> None:
        await session.insert(
            Insert(
                table=_LEDGER.name,
                rows=(
                    {
                        "package": package,
                        "version": version,
                        "name": name,
                        "applied_at_ms": time_ns() // 1_000_000,
                        "duration_ms": duration_ms,
                    },
                ),
            )
        )

    @asynccontextmanager
    async def register(
        self, module: ModuleSchema, *, upgrade_only: bool = False
    ) -> AsyncIterator[None]:
        """Register one schema; upgrade-only registrations leave fresh databases alone."""
        ctx = CURRENT_CONTEXT.get()
        if ctx is None or ctx.source is None:
            raise MigrationError(
                "schema registration requires an installed contrib source"
            )
        package = canonicalize_name(ctx.source.package)
        async with self._lock:
            if package in self._modules:
                raise MigrationError(
                    f"schema package {package!r} is already registered"
                )
            for dependency in module.dependencies:
                prerequisite = self._modules.get(dependency.package)
                if prerequisite is None or prerequisite.version < dependency.version:
                    raise MigrationError(
                        f"{package} requires registered schema {dependency.package} >= {dependency.version}"
                    )

            async def apply(session: Session) -> None:
                version = await self._version(session, package, module.progress)
                if version > module.version:
                    raise MigrationError(
                        f"{package} has unknown applied migration version {version}"
                    )
                if upgrade_only and version in (0, module.version):
                    return
                inherited = False
                predecessor = module.predecessor
                origins: set[str] = set()
                if (
                    version
                    and module.progress is not None
                    and module.progress.package is not None
                ):
                    origins.add(module.progress.package)
                if predecessor is not None:
                    previous = await self._version(session, predecessor.package, None)
                    if previous and previous < predecessor.version:
                        raise MigrationError(
                            f"{package} requires predecessor {predecessor.package} >= {predecessor.version}"
                        )
                    inherited = previous >= predecessor.version
                    if inherited:
                        origins.add(predecessor.package)
                        declaration = self._modules.get(predecessor.package)
                        if (
                            declaration is not None
                            and declaration.progress is not None
                            and declaration.progress.package is not None
                        ):
                            origins.add(declaration.progress.package)
                owners = await session.select(
                    Select(
                        table=_OWNERS.name,
                        where=In(expression=Column(name="name"), values=module.tables),
                    )
                )
                owned = {row["name"] for row in owners}
                for row in owners:
                    if row["package"] == package:
                        continue
                    if row["package"] not in origins:
                        raise MigrationError(
                            f"table {row['name']!r} belongs to {row['package']!r}"
                        )
                    await session.update(
                        Update(
                            table=_OWNERS.name,
                            values={"package": Value(value=package)},
                            where=Compare(
                                left=Column(name="name"),
                                operator="=",
                                right=Value(value=row["name"]),
                            ),
                        )
                    )
                for table in module.tables:
                    if table in {_LEDGER.name, _OWNERS.name}:
                        raise MigrationError(
                            f"table {table!r} belongs to the schema service"
                        )
                    if table not in owned:
                        await session.insert(
                            Insert(
                                table=_OWNERS.name,
                                rows=({"name": table, "package": package},),
                            )
                        )
                if version == 0 and inherited:
                    baseline = module.baseline
                    assert baseline is not None
                    version = baseline.version
                    await self._record(session, package, version, baseline.name)
                pending = tuple(
                    migration
                    for migration in module.migrations
                    if migration.version > version
                )
                if version == 0 and module.baseline is not None:
                    pending = (
                        module.baseline,
                        *(m for m in pending if m.version > module.baseline.version),
                    )
                bound = MigrationSession(session, package, module.tables)
                for migration in pending:
                    started = monotonic_ns()
                    await bound.apply_schema(migration.operations)
                    if migration.apply is not None:
                        await migration.apply(bound)
                    await self._record(
                        session,
                        package,
                        migration.version,
                        migration.name,
                        (monotonic_ns() - started) // 1_000_000,
                    )

            await self._database.transaction(apply)
            self._modules[package] = module
        try:
            yield
        finally:
            async with self._lock:
                del self._modules[package]


SCHEMA = ServiceKey[Schema]("storage.schema")


@asynccontextmanager
async def lifespan(ctx: Context):
    service = Schema(ctx.accept(DB))
    await service.start()
    ctx.offer(SCHEMA, service)
    yield


schema = Contrib(accepts=[DB], offers=[SCHEMA], lifespan=lifespan)
