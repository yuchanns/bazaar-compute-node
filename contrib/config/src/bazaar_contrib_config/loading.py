"""Shared loading, validation, conditional saving, and composition imports."""

from __future__ import annotations

lazy import asyncio
lazy import importlib
lazy import json
lazy from collections import Counter
lazy from collections.abc import Callable, Mapping
lazy from contextlib import asynccontextmanager
lazy from copy import deepcopy
lazy from pathlib import Path
lazy from tempfile import TemporaryDirectory
lazy from typing import Any

from bazaar_core import ServiceKey
from pydantic import BaseModel, ConfigDict
lazy from bazaar_core import (
    COMPOSITION,
    Blocked,
    Candidate,
    Composition,
    Context,
    Contrib,
    ContribEntry,
    ContribHub,
    Stopping,
)

lazy from .contracts import (
    CONFIG_CURRENT,
    CONFIG_STORE,
    ConfigCandidate,
    ConfigCommitUnknown,
    ConfigConflict,
    ConfigConverter,
    ConfigDependencyError,
    ConfigDocument,
    ConfigFormatError,
    ConfigSnapshot,
    ConfigStore,
    EnabledContrib,
)


class ConfigurationEnvironment(BaseModel):
    """Installed application factories and the selected bootstrap declarations."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    hub: str
    entries: tuple[EnabledContrib, ...]


class ConfigurationPreview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    candidate: ConfigCandidate
    revision: str | None
    error: str | None = None


class ConfigurationApplication(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    saved: bool = True
    applied: bool = False
    pending_restart: bool = False
    error: str | None = None


class Configuration:
    """Read, convert, validate, save, and apply configuration with the selected store."""

    def __init__(
        self,
        ctx: Context,
        store: ConfigStore,
        converter: ConfigConverter,
        defaults: Mapping[str, object],
        build: Callable[[ConfigDocument], Composition],
        *,
        environment: ConfigurationEnvironment | None = None,
        start: Callable[[ConfigDocument], Composition] | None = None,
    ) -> None:
        self.ctx = ctx
        self.store = store
        self.converter = converter
        self.defaults = deepcopy(dict(defaults))
        self.build = build
        self.environment = environment
        self.start = start
        self.snapshot: ConfigSnapshot | None = None
        self.candidate: ConfigCandidate | None = None
        self._composition: Candidate | None = None
        self._startup: tuple[ContribEntry, ...] = ()
        self._lock = asyncio.Lock()

    async def read(self) -> tuple[ConfigCandidate, ConfigSnapshot | None]:
        """Read the selected source and convert its document without opening resources."""
        snapshot = await self.store.read()
        candidate = await asyncio.to_thread(
            self.converter.convert,
            snapshot.payload if snapshot is not None else self.defaults,
        )
        return candidate, snapshot

    async def _build(self, document: ConfigDocument) -> Composition:
        """Keep startup roots available while validating or updating the full document."""
        composition = await asyncio.to_thread(self.build, document)
        if self._startup:
            names = {entry.name for entry in composition.entries}
            composition = composition.model_copy(
                update={
                    "entries": (
                        *composition.entries,
                        *(entry for entry in self._startup if entry.name not in names),
                    )
                }
            )
        return composition

    async def prepare(
        self, payload: Mapping[str, object]
    ) -> tuple[ConfigCandidate, Candidate]:
        """Convert the document and validate its composition without starting it."""
        candidate = await asyncio.to_thread(self.converter.convert, payload)
        document = ConfigDocument.model_validate(candidate.payload)
        await asyncio.to_thread(importlib.invalidate_caches)
        composition = await self._build(document)
        return candidate, self.ctx.validate(composition, replacing=self._composition)

    async def _write(
        self, candidate: ConfigCandidate, expected_revision: str | None
    ) -> ConfigSnapshot:
        try:
            return await self.store.write(
                candidate.payload, expected_revision=expected_revision
            )
        except ConfigCommitUnknown as error:
            try:
                snapshot = await self.store.read()
            except (OSError, ValueError) as cause:
                raise error from cause
            if snapshot is not None and snapshot.payload == candidate.payload:
                return snapshot
            raise ConfigConflict(
                "write response was lost; the source now contains another document"
            ) from error

    async def load(self) -> Candidate:
        """Read and save the document, validating the caller's initial composition."""
        snapshot = await self.store.read()
        payload = snapshot.payload if snapshot is not None else self.defaults
        if self.start is not None:
            candidate = await asyncio.to_thread(self.converter.convert, payload)
            selected = await asyncio.to_thread(
                self.start, ConfigDocument.model_validate(candidate.payload)
            )
            composition = self.ctx.validate(selected)
            self._startup = tuple(
                entry
                for entry in selected.entries
                if selected.roots is not None and entry.name in selected.roots
            )
        else:
            candidate, composition = await self.prepare(payload)
        if (
            self.start is not None
            or snapshot is None
            or payload.get("version") != candidate.payload["version"]
        ):
            self.check(composition)
        if snapshot is None or payload.get("version") != candidate.payload["version"]:
            snapshot = await self._write(
                candidate, snapshot.revision if snapshot else None
            )
        self.snapshot, self.candidate = snapshot, candidate
        self._composition = composition
        return composition

    async def select(
        self, start: Callable[[ConfigDocument], Composition] | None = None
    ) -> None:
        """Switch from preparation to the caller's selected saved composition."""
        async with self._lock:
            candidate, snapshot = await self.read()
            document = ConfigDocument.model_validate(candidate.payload)
            composition = await asyncio.to_thread(start or self.build, document)
            preview = self.ctx.validate(composition, replacing=self._composition)
            if start is not None:
                self.check(preview)
            await self.ctx.update(composition)
            self.snapshot, self.candidate = snapshot, candidate
            self._composition = preview
            self.start = start
            self._startup = tuple(
                entry
                for entry in composition.entries
                if composition.roots is not None and entry.name in composition.roots
            )

    @staticmethod
    def check(
        composition: Candidate | ContribHub,
        *,
        force: bool = False,
        current: Candidate | None = None,
    ) -> None:
        missing = Counter(
            (record.name, record.state.missing)
            for record in (current.snapshots if current is not None else ())
            if isinstance(record.state, Blocked) and record.state.missing
        )
        errors = []
        dependencies = True
        for record in composition.snapshots:
            state = record.state
            if not isinstance(state, Stopping) or not state.error:
                continue
            if isinstance(state, Blocked) and state.missing:
                key = record.name, state.missing
                if force:
                    continue
                if missing[key]:
                    missing[key] -= 1
                    continue
            else:
                dependencies = False
            errors.append(f"{record.location}: {record.name}: {state.error}")
        if errors:
            error = ConfigDependencyError if dependencies else ConfigFormatError
            raise error("; ".join(errors))

    async def commit(
        self,
        payload: Mapping[str, object],
        *,
        expected_revision: str | None,
        force: bool = False,
    ) -> ConfigSnapshot:
        """Validate and save a document; apply() requests its runtime update."""
        candidate, composition = await self.prepare(payload)
        current = await self.store.read()
        baseline = (
            (await self.prepare(current.payload))[1]
            if current is not None and not force
            else None
        )
        self.check(composition, force=force, current=baseline)
        snapshot = await self._write(candidate, expected_revision)
        self.snapshot, self.candidate = snapshot, candidate
        return snapshot

    async def apply(self, snapshot: ConfigSnapshot) -> ConfigurationApplication:
        """Request the saved composition through this loader's public Context."""
        if self.start is not None:
            return ConfigurationApplication(pending_restart=True)
        async with self._lock:
            current = await self.store.read()
            if current is None or current.revision != snapshot.revision:
                return ConfigurationApplication(
                    pending_restart=True,
                    error="configuration changed after saving; read it before applying",
                )
            try:
                await asyncio.to_thread(importlib.invalidate_caches)
                document = ConfigDocument.model_validate(snapshot.payload)
                composition = await self._build(document)
                await self.ctx.update(composition)
            except Exception as error:  # noqa: BLE001 - Saving and lifecycle application have separate outcomes.
                return ConfigurationApplication(pending_restart=True, error=str(error))
            return ConfigurationApplication(applied=True)

    async def activate(self) -> None:
        """Start the saved full composition within this loader's existing Hub."""
        async with self._lock:
            assert self.candidate is not None
            document = ConfigDocument.model_validate(self.candidate.payload)
            composition = await self._build(document)
            preview = self.ctx.validate(composition, replacing=self._composition)
            self.check(preview, force=True)
            await self.ctx.update(composition)
            self._composition = preview
            self.start = None

    async def preview(
        self, python: Path, *, payload: Mapping[str, object] | None = None
    ) -> ConfigurationPreview:
        """Convert and validate with installed target code and the original carrier."""
        if self.environment is None:
            raise ConfigFormatError("target-environment bootstrap is not configured")
        snapshot = await self.store.read()
        request: dict[str, Any] = {
            "environment": self.environment.model_dump(mode="json"),
            "build": f"{self.build.__module__}:{self.build.__qualname__}",
            "defaults": self.defaults,
            "payload": dict(payload)
            if payload is not None
            else dict(snapshot.payload)
            if snapshot is not None
            else self.defaults,
            "revision": snapshot.revision if snapshot is not None else None,
        }
        temporary = await asyncio.to_thread(TemporaryDirectory)
        try:
            path = Path(temporary.name) / "preview.json"
            await asyncio.to_thread(path.write_text, json.dumps(request), "utf-8")
            process = await asyncio.create_subprocess_exec(
                str(python),
                "-m",
                "bazaar_contrib_config.preview",
                str(path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                output, errors = await process.communicate()
            except BaseException:
                if process.returncode is None:
                    process.terminate()
                await process.wait()
                raise
            if process.returncode:
                raise ConfigFormatError(
                    errors.decode("utf-8", errors="replace").strip()
                )
            return ConfigurationPreview.model_validate_json(output)
        finally:
            await asyncio.to_thread(temporary.cleanup)

    async def commit_preview(self, preview: ConfigurationPreview) -> ConfigSnapshot:
        """Save the target-validated candidate under the source's original revision."""
        if preview.error:
            raise ConfigFormatError(preview.error)
        snapshot = await self._write(preview.candidate, preview.revision)
        self.snapshot, self.candidate = snapshot, preview.candidate
        return snapshot

    async def import_defaults(self, payload: Mapping[str, object]) -> ConfigSnapshot:
        """Import a chosen package's combination, keeping saved parameters and choices."""
        document = ConfigDocument.model_validate(payload)
        snapshot = await self.store.read()
        if snapshot is None:
            return await self.commit(payload, expected_revision=None)
        current = ConfigDocument.model_validate(snapshot.payload)
        packages = list(current.contribs)
        selected = {entry.entry for package in packages for entry in package.enabled}
        defaults = ConfigDocument.model_validate(self.defaults)
        shared = {
            entry.entry for package in defaults.contribs for entry in package.enabled
        }
        for package in document.contribs:
            entries = tuple(
                entry
                for entry in package.enabled
                if entry.entry not in selected and entry.entry not in shared
            )
            if package.enabled and not entries:
                continue
            index = next(
                (
                    index
                    for index, item in enumerate(packages)
                    if package.package and item.distribution == package.distribution
                ),
                None,
            )
            if index is None:
                packages.append(package.model_copy(update={"enabled": entries}))
            else:
                packages[index] = packages[index].model_copy(
                    update={"enabled": (*packages[index].enabled, *entries)}
                )
            selected.update(entry.entry for entry in entries)
        candidate = current.model_copy(update={"contribs": tuple(packages)})
        return await self.commit(
            candidate.model_dump(mode="json", exclude_none=True),
            expected_revision=snapshot.revision,
        )


CONFIGURATION = ServiceKey[Configuration]("config.configuration")


def configuration(
    defaults: Mapping[str, object],
    build: Callable[[ConfigDocument], Composition],
    *,
    environment: ConfigurationEnvironment | None = None,
    start: Callable[[ConfigDocument], Composition] | None = None,
) -> Contrib:
    """Create a loader offering CONFIGURATION and its validated COMPOSITION."""

    @asynccontextmanager
    async def lifespan(ctx: Context):
        service = Configuration(
            ctx,
            ctx.accept(CONFIG_STORE),
            ctx.accept(CONFIG_CURRENT),
            defaults,
            build,
            environment=environment,
            start=start,
        )
        candidate = await service.load()
        ctx.offer(CONFIGURATION, service)
        ctx.offer(COMPOSITION, candidate)
        yield

    return Contrib(
        accepts=[CONFIG_STORE, CONFIG_CURRENT],
        offers=[CONFIGURATION, COMPOSITION],
        lifespan=lifespan,
    )
