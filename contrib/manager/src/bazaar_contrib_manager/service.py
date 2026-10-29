"""Ordinary contrib services coordinating package and configuration operations."""

from __future__ import annotations

from contextlib import AsyncExitStack, asynccontextmanager
lazy import asyncio
lazy import os
lazy import shutil
lazy import sys
lazy from collections.abc import Awaitable, Callable, Mapping
lazy from hashlib import sha256
lazy from pathlib import Path
lazy from typing import Any
lazy from uuid import UUID

from bazaar_contrib_config import (
    CONFIG_PREPARE,
    CONFIGURATION,
    ConfigDocument,
    ContribPackage,
    EnabledContrib,
)
from bazaar_contrib_invoke import COMMANDS
from bazaar_contrib_sidecar import CLIENT
from bazaar_core import CURRENT_CONTEXT, Contrib, ServiceKey
from filelock import AsyncFileLock
lazy from bazaar_contrib_config import (
    ConfigCandidate,
    ConfigConflict,
    ConfigDependencyError,
    ConfigSnapshot,
    Configuration,
    ConfigurationApplication,
)
lazy from bazaar_contrib_sidecar import Client, RemoteError
lazy from bazaar_core import Context, ContribEntry
lazy from packaging.requirements import Requirement
lazy from packaging.specifiers import SpecifierSet
lazy from packaging.utils import canonicalize_name
lazy from pydantic import TypeAdapter

from .catalog import CONTRIB_CATALOG, ContribCatalog
from .contracts import (
    EnvironmentPlan,
    InstalledPackage,
    ManagementError,
    ManagerConfig,
    Operation,
    Package,
    Positions,
)
lazy from .cli import create_commands
lazy from .files import write
lazy from .packages import Packages


def merge(current: Any, changes: Any) -> Any:
    """Apply field updates while retaining other nested plugin parameters."""
    if isinstance(changes, dict):
        result = dict(current) if isinstance(current, dict) else {}
        for name, value in changes.items():
            result[name] = merge(result.get(name), value)
        return result
    if isinstance(changes, Positions):
        items = list(current) if isinstance(current, list) else []
        for index, item in enumerate(changes):
            while len(items) <= index:
                items.append(None)
            if item is not None:
                items[index] = merge(items[index], item)
        return items
    return changes


class ContribManager:
    def __init__(
        self,
        configuration: Configuration,
        catalog: ContribCatalog,
        packages: Packages,
        tasks: asyncio.TaskGroup,
        ctx: Context,
    ) -> None:
        self.configuration = configuration
        self.catalog = catalog
        self.packages = packages
        self.tasks = tasks
        self.ctx = ctx
        self.remote: Client | None = None
        self.operations: dict[UUID, Operation] = {}
        self.closed = False

    async def operation(self, id: UUID) -> Operation:
        if id in self.operations:
            return self.operations[id]
        path = self.packages.path / "operations" / f"{id}.json"
        return Operation.model_validate_json(await asyncio.to_thread(path.read_bytes))

    async def _record(self, id: UUID, **values: Any) -> None:
        operation = self.operations[id].model_copy(update=values)
        self.operations[id] = operation
        await asyncio.to_thread(
            write,
            self.packages.path / "operations" / f"{id}.json",
            operation.model_dump(mode="json"),
        )

    async def _execute(
        self,
        operation: Operation,
        action: Callable[[UUID], Awaitable[None]],
        *,
        remote: bool,
    ) -> Operation:
        self.operations[operation.id] = operation
        await self._record(operation.id)
        await asyncio.to_thread(self.packages.path.mkdir, parents=True, exist_ok=True)
        token = CURRENT_CONTEXT.set(self.ctx)
        online = self.configuration.start is None
        try:
            async with (
                AsyncFileLock(str(self.packages.path / "environment.lock"), mode=0o600),
                AsyncExitStack() as stack,
            ):
                try:
                    if remote and self.configuration.start is not None:
                        clients: list[Client] = []

                        @asynccontextmanager
                        async def consumer(ctx: Context):
                            clients.append(ctx.accept(CLIENT))
                            yield

                        candidate, _ = await self.configuration.read()
                        composition = await asyncio.to_thread(
                            self.configuration.build,
                            ConfigDocument.model_validate(candidate.payload),
                        )
                        entry = ContribEntry(
                            name=f"{self.ctx.id}:client",
                            definition=Contrib(accepts=[CLIENT], lifespan=consumer),
                        )
                        composition = composition.model_copy(
                            update={
                                "entries": (*composition.entries, entry),
                                "roots": (entry.name,),
                            }
                        )
                        if any(
                            CLIENT in entry.definition.offers
                            for entry in composition.entries
                        ):
                            preview = self.ctx.validate(composition)
                            Configuration.check(preview)
                            await stack.enter_async_context(self.ctx.enable(preview))
                        if clients:
                            client = clients[0]
                            try:
                                environment = await client.call(
                                    "configuration", "environment"
                                )
                            except FileNotFoundError, ConnectionRefusedError:
                                await stack.aclose()
                            else:
                                if environment != (
                                    self.configuration.environment.model_dump(
                                        mode="json"
                                    )
                                    if self.configuration.environment is not None
                                    else None
                                ):
                                    raise ManagementError(
                                        "the sidecar belongs to another configuration; "
                                        "select its configuration or endpoint"
                                    )
                                _, snapshot = TypeAdapter(
                                    tuple[ConfigCandidate, ConfigSnapshot | None]
                                ).validate_python(
                                    await client.call("configuration", "read")
                                )
                                current = await self.configuration.store.read()
                                if (
                                    snapshot.revision if snapshot is not None else None
                                ) != (
                                    current.revision if current is not None else None
                                ):
                                    raise ConfigConflict(
                                        "the sidecar configuration differs from the selected source"
                                    )
                                self.remote = client
                                online = True
                    await action(operation.id)
                finally:
                    self.remote = None
            await self._record(
                operation.id,
                phase="complete",
                pending_restart=online
                and self.operations[operation.id].pending_restart,
            )
        except asyncio.CancelledError:
            await self._record(
                operation.id, phase="cancelled", error="operation was cancelled"
            )
            raise
        except Exception as error:  # noqa: BLE001 - Preserve the committed stages of a failed management request.
            await self._record(
                operation.id,
                phase="failed",
                error=str(error) or type(error).__name__,
                pending_restart=online
                and self.operations[operation.id].pending_restart,
            )
        finally:
            CURRENT_CONTEXT.reset(token)
        return self.operations[operation.id]

    async def _submit(
        self,
        kind: str,
        action: Callable[[UUID], Awaitable[None]],
        *,
        package: str | None = None,
        remote: bool = True,
    ) -> Operation:
        if self.closed:
            raise ManagementError("contrib manager has closed")
        operation = Operation(
            kind=kind, target=str(self.packages.prefix), package=package
        )
        task = self.tasks.create_task(self._execute(operation, action, remote=remote))
        return await asyncio.shield(task)

    async def installed(self) -> tuple[InstalledPackage, ...]:
        info = await self.packages.inspect()
        return tuple(package for package in info.packages if package.entries)

    async def detail(self, name: str) -> InstalledPackage:
        names = {
            str(canonicalize_name(name)),
            f"bazaar-contrib-{canonicalize_name(name)}",
        }
        info = await self.packages.inspect()
        packages = [package for package in info.packages if package.package in names]
        if len(packages) != 1:
            raise ManagementError(f"select one installed package for {name!r}")
        return packages[0]

    async def _save(
        self,
        id: UUID,
        payload: Mapping[str, object],
        revision: str | None,
        *,
        force: bool = False,
    ) -> None:
        await self._record(id, phase="saving")
        snapshot = (
            await self.configuration.commit(
                payload, expected_revision=revision, force=force
            )
            if self.remote is None
            else TypeAdapter(ConfigSnapshot).validate_python(
                await self.remote.call(
                    "configuration",
                    "commit",
                    payload,
                    expected_revision=revision,
                    force=force,
                )
            )
        )
        await self._record(id, config_saved=True, applied=False)
        if not self.operations[id].pending_restart:
            await self._apply(id, snapshot)

    async def _apply(self, id: UUID, snapshot: ConfigSnapshot) -> None:
        await self._record(id, phase="applying")
        result = (
            await self.configuration.apply(snapshot)
            if self.remote is None
            else ConfigurationApplication.model_validate(
                await self.remote.call("configuration", "apply", snapshot)
            )
        )
        await self._record(
            id,
            applied=result.applied,
            pending_restart=self.operations[id].pending_restart
            or result.pending_restart,
        )
        if result.error:
            raise ManagementError(result.error)

    async def _enable(
        self,
        id: UUID,
        package: InstalledPackage,
        contrib: str | None,
        config: dict[str, Any] | None,
    ) -> None:
        candidate, snapshot = await self.configuration.read()
        document = ConfigDocument.model_validate(candidate.payload)
        packages = list(document.contribs)
        matches = [
            index
            for index, item in enumerate(packages)
            if item.distribution == package.package and item.applies
        ]
        if len(matches) > 1:
            raise ManagementError(
                f"select one applicable declaration for {package.package!r}"
            )
        index = matches[0] if matches else None
        if index is None:
            sources = {
                entry.source
                for entry in await self.catalog.entries()
                if entry.package == package.package
            }
            if len(sources) > 1:
                raise ManagementError(
                    f"select a source with contrib add for {package.package}"
                )
            declaration = ContribPackage(
                package=f"{package.package}=={package.version}",
                source=next(iter(sources), self.packages.source),
            )
        else:
            declaration = packages[index]
        if contrib is not None and not any(
            entry.name == contrib for entry in package.entries
        ):
            raise ManagementError(f"{package.package} does not provide {contrib!r}")
        entries = list(declaration.enabled)
        for entry in package.entries:
            if contrib is not None and entry.name != contrib:
                continue
            matching = [
                position
                for position, configured in enumerate(entries)
                if configured.entry == entry.name
            ]
            if len(matching) > 1:
                raise ManagementError("select a configured entry by its list position")
            if matching:
                position = matching[0]
                entries[position] = entries[position].model_copy(
                    update={"config": merge(entries[position].config, config or {})}
                )
            else:
                entries.append(EnabledContrib(entry=entry.name, config=config or {}))
        declaration = declaration.model_copy(update={"enabled": tuple(entries)})
        if index is None:
            packages.append(declaration)
        else:
            packages[index] = declaration
        await self._save(
            id,
            document.model_copy(update={"contribs": tuple(packages)}).model_dump(
                mode="json"
            ),
            snapshot.revision if snapshot is not None else None,
        )

    async def enable(
        self,
        package: str,
        *,
        contrib: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> Operation:
        async def action(id: UUID) -> None:
            await self._enable(id, await self.detail(package), contrib, config)

        return await self._submit("enable", action, package=package)

    async def edit(self, index: int, config: dict[str, Any]) -> Operation:
        async def action(id: UUID) -> None:
            candidate, snapshot = await self.configuration.read()
            document = ConfigDocument.model_validate(candidate.payload)
            packages = list(document.contribs)
            positions = [
                (package, position)
                for package, item in enumerate(packages)
                for position in range(len(item.enabled))
            ]
            if index < 0 or index >= len(positions):
                raise ManagementError("configured entry position is out of range")
            package, position = positions[index]
            entries = list(packages[package].enabled)
            entries[position] = entries[position].model_copy(
                update={"config": merge(entries[position].config, config)}
            )
            packages[package] = packages[package].model_copy(
                update={"enabled": tuple(entries)}
            )
            await self._save(
                id,
                document.model_copy(update={"contribs": tuple(packages)}).model_dump(
                    mode="json"
                ),
                snapshot.revision if snapshot is not None else None,
            )

        return await self._submit("edit", action)

    async def disable(self, index: int, *, force: bool = False) -> Operation:
        async def action(id: UUID) -> None:
            candidate, snapshot = await self.configuration.read()
            document = ConfigDocument.model_validate(candidate.payload)
            packages = list(document.contribs)
            positions = [
                (package, position)
                for package, item in enumerate(packages)
                for position in range(len(item.enabled))
            ]
            if index < 0 or index >= len(positions):
                raise ManagementError("configured entry position is out of range")
            package, position = positions[index]
            entries = list(packages[package].enabled)
            entry = entries.pop(position)
            if packages[package].package or entries:
                packages[package] = packages[package].model_copy(
                    update={"enabled": tuple(entries)}
                )
            else:
                packages.pop(package)
            try:
                await self._save(
                    id,
                    document.model_copy(
                        update={"contribs": tuple(packages)}
                    ).model_dump(mode="json"),
                    snapshot.revision if snapshot is not None else None,
                    force=force,
                )
            except (ConfigDependencyError, RemoteError) as error:
                if force or (
                    isinstance(error, RemoteError)
                    and error.type != ConfigDependencyError.__name__
                ):
                    raise
                raise ManagementError(
                    f"Cannot disable {entry.entry!r}: {error}\n\n"
                    "Add '--force' to disable it. Dependent contribs will stop and "
                    "cannot run until their required services are restored."
                ) from error

        return await self._submit("disable", action)

    async def index(self, location: str, *, remove: bool = False) -> Operation:
        async def action(id: UUID) -> None:
            candidate, snapshot = await self.configuration.read()
            document = ConfigDocument.model_validate(candidate.payload)
            indexes = list(document.contrib_indexes)
            if remove:
                indexes.remove(location)
            elif location not in indexes:
                indexes.append(location)
            await self._save(
                id,
                document.model_copy(
                    update={"contrib_indexes": tuple(indexes)}
                ).model_dump(mode="json"),
                snapshot.revision if snapshot is not None else None,
            )

        return await self._submit("index-remove" if remove else "index-add", action)

    async def _install(
        self,
        id: UUID,
        plan: EnvironmentPlan,
        previous: EnvironmentPlan,
        document: ConfigDocument,
        *,
        removed: str | None = None,
        pin: str | None = None,
        upgrade: bool = False,
    ) -> None:
        path = self.packages.path / "operations" / str(id)
        await self._record(id, phase="resolving")
        lock = await self.packages.resolve(plan, path, upgrade=upgrade)
        await self._record(id, phase="preflight")
        python = await self.packages.stage(lock)
        expected = await self.packages.inspect(python, definitions=False)
        if pin is not None:
            version = next(
                package.version
                for package in expected.packages
                if package.package == pin
            )
            entries = []
            for entry in document.contribs:
                if entry.distribution == pin:
                    requirement = Requirement(entry.package)
                    if requirement.url is None:
                        requirement.specifier = SpecifierSet(f"=={version}")
                    entry = entry.model_copy(update={"package": str(requirement)})
                entries.append(entry)
            document = document.model_copy(update={"contribs": tuple(entries)})
        preview = await self.configuration.preview(
            python, payload=document.model_dump(mode="json")
        )
        names = {package.package for package in expected.packages}
        missing = [
            package
            for package in preview.candidate.required_packages
            if str(canonicalize_name(package.package)) not in names
        ]
        if missing:
            packages = tuple(
                ContribPackage(
                    package=package.package + package.version, source=package.source
                )
                for package in missing
            )
            entries = [
                *document.contribs,
                *packages,
            ]
            document = document.model_copy(update={"contribs": tuple(entries)})
            plan = plan.model_copy(update={"packages": (*plan.packages, *packages)})
            await self._record(id, phase="resolving")
            lock = await self.packages.resolve(
                plan, path / "migration", upgrade=upgrade
            )
            python = await self.packages.stage(lock)
            expected = await self.packages.inspect(python, definitions=False)
            preview = await self.configuration.preview(
                python, payload=document.model_dump(mode="json")
            )
        if preview.error:
            raise ManagementError(preview.error)
        if removed is not None and any(
            package.package == removed for package in expected.packages
        ):
            raise ManagementError("another package still requires this distribution")
        current = await self.configuration.store.read()
        if (current.revision if current is not None else None) != preview.revision:
            raise ConfigConflict(
                "configuration changed before installation; retry the operation"
            )
        before = await self.packages.inspect()
        recovery = path / "before.json"
        await asyncio.to_thread(
            write,
            recovery,
            {
                "requirements": previous.model_dump(mode="json"),
                "environment": before.model_dump(mode="json"),
            },
        )
        try:
            await asyncio.to_thread(
                shutil.copyfile,
                self.packages.path / "pylock.toml",
                path / "before.pylock.toml",
            )
        except FileNotFoundError:
            instruction = (
                f"restore the package versions and sources recorded in {recovery}"
            )
        else:
            instruction = (
                f"reinstall the previous lock at {path / 'before.pylock.toml'}"
            )
        await self._record(id, phase="installing", recovery=instruction)
        try:
            await self.packages.install(plan, lock)
        except Exception:
            after = await self.packages.inspect()
            await self._record(
                id, environment_changed=before != after, pending_restart=before != after
            )
            raise
        installed = await self.packages.inspect()
        versions = {package.package: package.version for package in installed.packages}
        mismatched = [
            package.package
            for package in expected.packages
            if versions.get(package.package) != package.version
        ]
        if mismatched:
            await self._record(id, environment_changed=True, pending_restart=True)
            raise ManagementError(
                "installed environment differs from the candidate: "
                + ", ".join(mismatched)
            )
        await self._record(
            id,
            installed=True,
            environment_changed=before != installed,
            pending_restart=any(
                package
                != next(
                    (
                        item
                        for item in installed.packages
                        if item.package == package.package
                    ),
                    None,
                )
                for package in before.packages
            ),
            version=versions.get(self.operations[id].package or plan.host.package),
        )
        await asyncio.to_thread(
            shutil.copyfile, lock, self.packages.path / "pylock.toml"
        )
        if current is None or current.payload != preview.candidate.payload:
            await self._record(id, phase="saving")
            snapshot = (
                await self.configuration.commit_preview(preview)
                if self.remote is None
                else TypeAdapter(ConfigSnapshot).validate_python(
                    await self.remote.call("configuration", "commit_preview", preview)
                )
            )
            await self._record(id, config_saved=True, applied=False)
            if self.remote is not None and not self.operations[id].pending_restart:
                await self._apply(id, snapshot)
        await self._record(id, recovery=None)

    async def bootstrap(self) -> Operation:
        """Install bootstrap declarations before reading the persistent carrier."""

        async def action(id: UUID) -> None:
            candidate, _ = await self.configuration.read()
            document = ConfigDocument.model_validate(candidate.payload)
            await self.packages.initialize()
            await self._record(id, phase="installing")
            for package in document.contribs:
                if package.package and package.applies:
                    await self.packages.command(
                        str(self.packages.uv),
                        "pip",
                        "install",
                        "--python",
                        str(self.packages.python),
                        "--default-index",
                        package.source or self.packages.source,
                        "--prerelease",
                        "allow",
                        package.package,
                    )
            await self._record(id, installed=True)

        return await self._submit("bootstrap", action, remote=False)

    async def sync(
        self, *, only_missing: bool = False, upgrade: bool = False
    ) -> Operation:
        """Install the configured environment; startup can skip an already satisfied graph."""

        async def action(id: UUID) -> None:
            candidate, _ = await self.configuration.read()
            document = ConfigDocument.model_validate(candidate.payload)
            plan = await self.packages.plan(document, self.configuration.environment)
            if only_missing and await self.packages.satisfied(plan):
                return
            await self.packages.initialize()
            await self._install(id, plan, plan, document, upgrade=upgrade)

        return await self._submit("sync", action, remote=not only_missing)

    async def add(
        self,
        package: Package,
        *,
        enable: bool = False,
        contrib: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> Operation:
        async def action(id: UUID) -> None:
            await self.packages.initialize()
            candidate, _ = await self.configuration.read()
            document = ConfigDocument.model_validate(candidate.payload)
            plan = await self.packages.plan(document, self.configuration.environment)
            if package.package == plan.host.package:
                raise ManagementError("use upgrade to change the node distribution")
            declaration = ContribPackage(
                package=package.package + package.version, source=package.source
            )
            entries = []
            for entry in document.contribs:
                if entry.distribution == package.package:
                    requirement = Requirement(entry.package)
                    requirement.url = None
                    requirement.specifier = SpecifierSet(package.version)
                    entry = entry.model_copy(
                        update={
                            "package": str(requirement),
                            "source": declaration.source,
                        }
                    )
                entries.append(entry)
            if not any(entry.distribution == package.package for entry in entries):
                entries.append(declaration)
            document = document.model_copy(update={"contribs": tuple(entries)})
            await self._install(
                id,
                plan.model_copy(
                    update={
                        "packages": tuple(entry for entry in entries if entry.package)
                    }
                ),
                plan,
                document,
                pin=package.package if not package.version else None,
            )
            if enable:
                await self._enable(
                    id, await self.detail(package.package), contrib, config
                )

        return await self._submit("add", action, package=package.package)

    async def remove(self, package: str) -> Operation:
        package = str(canonicalize_name(package))

        async def action(id: UUID) -> None:
            await self.packages.initialize()
            candidate, _ = await self.configuration.read()
            document = ConfigDocument.model_validate(candidate.payload)
            plan = await self.packages.plan(document, self.configuration.environment)
            declarations = [
                entry for entry in document.contribs if entry.distribution == package
            ]
            if not declarations:
                raise ManagementError(
                    f"Cannot uninstall {package!r}: it is not listed in the contrib "
                    "configuration.\n\nRun 'bcn contrib configured' to see the packages "
                    "that 'bcn contrib remove' can manage."
                )
            contribs = ", ".join(
                entry.entry for package in declarations for entry in package.enabled
            )
            if contribs:
                raise ManagementError(
                    f"Cannot uninstall {package!r} while these contribs remain in "
                    f"the configuration: {contribs}.\n\n"
                    "Run 'bcn contrib configured' to find their indexes, disable "
                    "each with 'bcn contrib disable INDEX', then retry "
                    f"'bcn contrib remove {package}'."
                )
            entries = tuple(
                entry for entry in document.contribs if entry.distribution != package
            )
            document = document.model_copy(update={"contribs": entries})
            await self._install(
                id,
                plan.model_copy(
                    update={
                        "packages": tuple(entry for entry in entries if entry.package)
                    }
                ),
                plan,
                document,
                removed=package,
            )

        return await self._submit("remove", action, package=package)

    async def upgrade(self, version: str) -> Operation:
        async def action(id: UUID) -> None:
            await self.packages.initialize()
            candidate, _ = await self.configuration.read()
            document = ConfigDocument.model_validate(candidate.payload)
            plan = await self.packages.plan(document, self.configuration.environment)
            host = Package(
                package=plan.host.package,
                source=plan.host.source,
                version=f"=={version}",
            )
            await self._install(
                id,
                plan.model_copy(update={"host": host}),
                plan,
                document,
                upgrade=True,
            )
            await self._record(id, pending_restart=True)

        return await self._submit("upgrade", action, package="bazaar-compute-node")


CONTRIB_MANAGER = ServiceKey[ContribManager]("contrib.manager")


@asynccontextmanager
async def lifespan(ctx: Context, config: ManagerConfig):
    path = (
        config.path
        or Path(os.environ.get("BCN_HOME") or Path.home()) / ".bcn" / "contrib"
    )
    path = await asyncio.to_thread(lambda: path.expanduser().resolve())
    packages = Packages(
        path / "environments" / sha256(sys.prefix.encode()).hexdigest(),
        config.host_source,
    )
    configuration = ctx.accept(CONFIGURATION)
    catalog = ContribCatalog(configuration, path / "indexes")
    async with asyncio.TaskGroup() as tasks:
        service = ContribManager(configuration, catalog, packages, tasks, ctx)
        ctx.offer(CONTRIB_CATALOG, catalog)
        ctx.offer(CONTRIB_MANAGER, service)

        command = await asyncio.to_thread(create_commands, service)
        with ctx.accept(COMMANDS).register(command):
            try:
                yield
            finally:
                service.closed = True


async def prepare(ctx: Context, _: None) -> None:
    result = await ctx.accept(CONTRIB_MANAGER).sync(only_missing=True)
    if result.error:
        ctx.accept(CONFIGURATION).preparation_error = result.error


manager = Contrib(
    config=ManagerConfig,
    accepts=[CONFIGURATION, COMMANDS],
    offers=[CONTRIB_CATALOG, CONTRIB_MANAGER],
    listen={CONFIG_PREPARE: prepare},
    lifespan=lifespan,
)
