"""Shared loading, validation, conditional saving, and composition imports."""

from __future__ import annotations

lazy import asyncio
lazy from collections.abc import Callable, Mapping
lazy from contextlib import asynccontextmanager
lazy from copy import deepcopy

from bazaar_core import ServiceKey
lazy from bazaar_core import (
    COMPOSITION,
    Candidate,
    Composition,
    Context,
    Contrib,
    Stopping,
)

lazy from .contracts import (
    CONFIG_CURRENT,
    CONFIG_STORE,
    ConfigCandidate,
    ConfigCommitUnknown,
    ConfigConflict,
    ConfigConverter,
    ConfigDocument,
    ConfigFormatError,
    ConfigSnapshot,
    ConfigStore,
)


class Configuration:
    """Read, convert, validate, save, and apply configuration with the selected store."""

    def __init__(
        self,
        ctx: Context,
        store: ConfigStore,
        converter: ConfigConverter,
        defaults: Mapping[str, object],
        build: Callable[[ConfigDocument], Composition],
    ) -> None:
        self.ctx = ctx
        self.store = store
        self.converter = converter
        self.defaults = deepcopy(dict(defaults))
        self.build = build
        self.snapshot: ConfigSnapshot | None = None
        self.candidate: ConfigCandidate | None = None
        self._composition: Candidate | None = None

    async def prepare(
        self, payload: Mapping[str, object]
    ) -> tuple[ConfigCandidate, Candidate]:
        """Convert the document and validate its composition without starting it."""
        candidate = await asyncio.to_thread(self.converter.convert, payload)
        document = ConfigDocument.model_validate(candidate.payload)
        composition = await asyncio.to_thread(self.build, document)
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
        """Read configuration, save defaults or a version migration, and select entries."""
        snapshot = await self.store.read()
        payload = snapshot.payload if snapshot is not None else self.defaults
        candidate, composition = await self.prepare(payload)
        if (
            snapshot is not None
            and payload.get("version") == candidate.payload["version"]
        ):
            self.snapshot, self.candidate = snapshot, candidate
            self._composition = composition
            return composition
        self.check(composition)
        snapshot = await self._write(candidate, snapshot.revision if snapshot else None)
        self.snapshot, self.candidate = snapshot, candidate
        self._composition = composition
        return composition

    @staticmethod
    def check(composition: Candidate) -> None:
        errors = [
            f"{record.location}: {record.name}: {record.state.error}"
            for record in composition.snapshots
            if isinstance(record.state, Stopping) and record.state.error
        ]
        if errors:
            raise ConfigFormatError("; ".join(errors))

    async def commit(
        self, payload: Mapping[str, object], *, expected_revision: str | None
    ) -> ConfigSnapshot:
        """Validate and save a document; apply() requests its runtime update."""
        candidate, composition = await self.prepare(payload)
        self.check(composition)
        snapshot = await self._write(candidate, expected_revision)
        self.snapshot, self.candidate = snapshot, candidate
        return snapshot

    async def import_defaults(self, payload: Mapping[str, object]) -> ConfigSnapshot:
        """Import a chosen package's combination, keeping saved parameters and choices."""
        document = ConfigDocument.model_validate(payload)
        snapshot = await self.store.read()
        if snapshot is None:
            return await self.commit(payload, expected_revision=None)
        current = ConfigDocument.model_validate(snapshot.payload)
        entries = list(current.contribs)
        selected = {entry.contrib for entry in entries}
        defaults = ConfigDocument.model_validate(self.defaults)
        shared = {entry.contrib for entry in defaults.contribs}
        for entry in document.contribs:
            if entry.contrib not in selected and entry.contrib not in shared:
                entries.append(entry)
                selected.add(entry.contrib)
        candidate = current.model_copy(update={"contribs": tuple(entries)})
        return await self.commit(
            candidate.model_dump(mode="json", exclude_none=True),
            expected_revision=snapshot.revision,
        )


CONFIGURATION = ServiceKey[Configuration]("config.configuration")


def configuration(
    defaults: Mapping[str, object], build: Callable[[ConfigDocument], Composition]
) -> Contrib:
    """Create an ordinary composition loader for the application's document builder."""

    @asynccontextmanager
    async def lifespan(ctx: Context):
        service = Configuration(
            ctx, ctx.accept(CONFIG_STORE), ctx.accept(CONFIG_CURRENT), defaults, build
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
