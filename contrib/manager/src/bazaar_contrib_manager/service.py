"""Ordinary contrib services coordinating package and configuration operations."""

from __future__ import annotations

from contextlib import asynccontextmanager
lazy import asyncio
lazy import os
lazy import shutil
lazy import sys
lazy from collections.abc import Awaitable, Callable, Mapping
lazy from hashlib import sha256
lazy from pathlib import Path
lazy from typing import Any
lazy from uuid import UUID

from bazaar_contrib_config import CONFIGURATION, ConfigDocument, EnabledContrib
from bazaar_contrib_invoke import COMMANDS
from bazaar_core import CURRENT_CONTEXT, Contrib, ServiceKey
from filelock import AsyncFileLock
lazy from bazaar_contrib_config import ConfigConflict, Configuration
lazy from bazaar_core import Context
lazy from packaging.utils import canonicalize_name

from .catalog import CONTRIB_CATALOG, ContribCatalog
from .contracts import (
    InstalledPackage,
    ManagementError,
    ManagerConfig,
    Manifest,
    Operation,
    Package,
)
lazy from .cli import create_commands
lazy from .files import write
lazy from .packages import Packages


def merge(current: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
    """Apply field updates while retaining other nested plugin parameters."""
    result = dict(current)
    for name, value in changes.items():
        if isinstance(value, dict) and isinstance(result.get(name), dict):
            value = merge(result[name], value)
        elif (
            isinstance(value, list)
            and isinstance(result.get(name), list)
            and any(item is None or isinstance(item, dict) for item in value)
        ):
            items = list(result[name])
            for index, item in enumerate(value):
                while len(items) <= index:
                    items.append(None)
                if item is not None:
                    items[index] = (
                        merge(items[index], item)
                        if isinstance(items[index], dict) and isinstance(item, dict)
                        else item
                    )
            value = items
        result[name] = value
    return result


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
        self, operation: Operation, action: Callable[[UUID], Awaitable[None]]
    ) -> Operation:
        self.operations[operation.id] = operation
        await self._record(operation.id)
        await asyncio.to_thread(self.packages.path.mkdir, parents=True, exist_ok=True)
        token = CURRENT_CONTEXT.set(self.ctx)
        try:
            async with AsyncFileLock(
                str(self.packages.path / "environment.lock"), mode=0o600
            ):
                await action(operation.id)
            await self._record(operation.id, phase="complete")
        except asyncio.CancelledError:
            await self._record(
                operation.id, phase="cancelled", error="operation was cancelled"
            )
            raise
        except Exception as error:  # noqa: BLE001 - Preserve the committed stages of a failed management request.
            await self._record(operation.id, phase="failed", error=str(error))
        finally:
            CURRENT_CONTEXT.reset(token)
        return self.operations[operation.id]

    async def _submit(
        self,
        kind: str,
        action: Callable[[UUID], Awaitable[None]],
        *,
        package: str | None = None,
    ) -> Operation:
        if self.closed:
            raise ManagementError("contrib manager has closed")
        operation = Operation(
            kind=kind, target=str(self.packages.prefix), package=package
        )
        task = self.tasks.create_task(self._execute(operation, action))
        return await asyncio.shield(task)

    async def installed(self) -> tuple[InstalledPackage, ...]:
        info = await self.packages.inspect()
        return tuple(package for package in info.packages if package.entries)

    async def detail(self, name: str) -> InstalledPackage:
        names = {
            str(canonicalize_name(name)),
            f"bazaar-contrib-{canonicalize_name(name)}",
        }
        packages = [
            package for package in await self.installed() if package.package in names
        ]
        if len(packages) != 1:
            raise ManagementError(f"select one installed package for {name!r}")
        return packages[0]

    async def _save(
        self, id: UUID, payload: Mapping[str, object], revision: str | None
    ) -> None:
        await self._record(id, phase="saving")
        snapshot = await self.configuration.commit(payload, expected_revision=revision)
        await self._record(id, config_saved=True, phase="applying")
        result = await self.configuration.apply(snapshot)
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
        if contrib is None and package.defaults is not None:
            await self._record(id, phase="saving")
            snapshot = await self.configuration.import_defaults(package.defaults)
            await self._record(id, config_saved=True, phase="applying")
            result = await self.configuration.apply(snapshot)
            await self._record(
                id,
                applied=result.applied,
                pending_restart=self.operations[id].pending_restart
                or result.pending_restart,
            )
            if result.error:
                raise ManagementError(result.error)
            return
        if contrib is None:
            if len(package.entries) != 1:
                raise ManagementError(
                    "select an entry: "
                    + ", ".join(entry.name for entry in package.entries)
                )
            contrib = package.entries[0].name
        if not any(entry.name == contrib for entry in package.entries):
            raise ManagementError(f"{package.package} does not provide {contrib!r}")
        candidate, snapshot = await self.configuration.read()
        document = ConfigDocument.model_validate(candidate.payload)
        entries = list(document.contribs)
        matching = [
            index for index, entry in enumerate(entries) if entry.contrib == contrib
        ]
        if len(matching) > 1:
            raise ManagementError("select a configured entry by its list position")
        if matching:
            index = matching[0]
            entries[index] = EnabledContrib(
                contrib=contrib, config=merge(entries[index].config, config or {})
            )
        else:
            entries.append(EnabledContrib(contrib=contrib, config=config or {}))
        await self._save(
            id,
            document.model_copy(update={"contribs": tuple(entries)}).model_dump(
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
            entries = list(document.contribs)
            if index < 0 or index >= len(entries):
                raise ManagementError("configured entry position is out of range")
            entries[index] = EnabledContrib(
                contrib=entries[index].contrib,
                config=merge(entries[index].config, config),
            )
            await self._save(
                id,
                document.model_copy(update={"contribs": tuple(entries)}).model_dump(
                    mode="json"
                ),
                snapshot.revision if snapshot is not None else None,
            )

        return await self._submit("edit", action)

    async def disable(self, index: int) -> Operation:
        async def action(id: UUID) -> None:
            candidate, snapshot = await self.configuration.read()
            document = ConfigDocument.model_validate(candidate.payload)
            entries = list(document.contribs)
            if index < 0 or index >= len(entries):
                raise ManagementError("configured entry position is out of range")
            del entries[index]
            await self._save(
                id,
                document.model_copy(update={"contribs": tuple(entries)}).model_dump(
                    mode="json"
                ),
                snapshot.revision if snapshot is not None else None,
            )

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
        manifest: Manifest,
        previous: Manifest,
        *,
        removed: str | None = None,
    ) -> None:
        path = self.packages.path / "operations" / str(id)
        await self._record(id, phase="resolving")
        lock = await self.packages.resolve(manifest, path)
        await self._record(id, phase="preflight")
        python = await self.packages.stage(lock)
        preview = await self.configuration.preview(python)
        requirements = {package.package: package for package in manifest.packages}
        installed = await self.packages.inspect(python)
        names = {package.package for package in installed.packages}
        missing = [
            package
            for package in preview.candidate.required_packages
            if str(canonicalize_name(package.package)) not in names
        ]
        if missing:
            for package in missing:
                requirements[package.package] = Package(**package.model_dump())
            manifest = manifest.model_copy(
                update={"packages": tuple(requirements.values())}
            )
            await self._record(id, phase="resolving")
            lock = await self.packages.resolve(manifest, path / "migration")
            python = await self.packages.stage(lock)
            preview = await self.configuration.preview(python)
        if preview.error:
            raise ManagementError(preview.error)
        if removed is not None:
            installed = await self.packages.inspect(python)
            if any(package.package == removed for package in installed.packages):
                raise ManagementError(
                    "another package still requires this distribution"
                )
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
                "manifest": previous.model_dump(mode="json"),
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
        await self._record(
            id,
            phase="installing",
            recovery=instruction,
        )
        try:
            await self.packages.install(manifest, lock)
        except Exception:
            after = await self.packages.inspect()
            await self._record(
                id, environment_changed=before != after, pending_restart=before != after
            )
            raise
        installed = await self.packages.inspect()
        versions = {package.package: package.version for package in installed.packages}
        expected = await self.packages.inspect(python)
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
        sources = {
            package.package: package.source
            for package in (previous.host, *previous.packages)
        }
        changed = any(
            versions.get(package.package) != package.version
            for package in before.packages
        ) or any(
            package.package in sources and sources[package.package] != package.source
            for package in (manifest.host, *manifest.packages)
        )
        await self._record(
            id,
            installed=True,
            environment_changed=before != installed or changed,
            pending_restart=changed,
            version=versions.get(self.operations[id].package or manifest.host.package),
        )
        manifest = manifest.model_copy(update={"installed": versions})
        await asyncio.to_thread(
            shutil.copyfile, lock, self.packages.path / "pylock.toml"
        )
        await asyncio.to_thread(
            write,
            self.packages.path / "manifest.json",
            manifest.model_dump(mode="json"),
        )
        await self._record(
            id,
            recovery=None,
        )
        if current is None or current.payload != preview.candidate.payload:
            await self._record(id, phase="saving")
            await self.configuration.commit_preview(preview)
            await self._record(id, config_saved=True, pending_restart=True)

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
            manifest = await self.packages.manifest(await self.catalog.entries())
            requirements = {item.package: item for item in manifest.packages}
            if package.package == manifest.host.package:
                raise ManagementError("use upgrade to change the node distribution")
            requirements[package.package] = package
            await self._install(
                id,
                manifest.model_copy(update={"packages": tuple(requirements.values())}),
                manifest,
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
            manifest = await self.packages.manifest(await self.catalog.entries())
            if not any(item.package == package for item in manifest.packages):
                raise ManagementError(
                    "package is part of the node or has no recorded extra requirement"
                )
            requirements = tuple(
                item for item in manifest.packages if item.package != package
            )
            await self._install(
                id,
                manifest.model_copy(update={"packages": requirements}),
                manifest,
                removed=package,
            )

        return await self._submit("remove", action, package=package)

    async def upgrade(self, version: str) -> Operation:
        async def action(id: UUID) -> None:
            await self.packages.initialize()
            manifest = await self.packages.manifest(await self.catalog.entries())
            host = Package(
                package=manifest.host.package,
                source=manifest.host.source,
                version=f"=={version}",
            )
            await self._install(
                id, manifest.model_copy(update={"host": host}), manifest
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


manager = Contrib(
    config=ManagerConfig,
    accepts=[CONFIGURATION, COMMANDS],
    offers=[CONTRIB_CATALOG, CONTRIB_MANAGER],
    lifespan=lifespan,
)
