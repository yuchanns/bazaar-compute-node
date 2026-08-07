from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from importlib.metadata import EntryPoint, entry_points
from typing import Any, cast

from ..core.channel import IChannel
from ..core.observability import IAudit
from ..core.runtime import IRuntime, RuntimeCommandContext
from ..core.storage import IStorage
from .command import ControlHandler

CHANNEL_ENTRY_POINT_GROUP = "bazaar_compute_node.channels"
RUNTIME_ENTRY_POINT_GROUP = "bazaar_compute_node.runtimes"
STORAGE_ENTRY_POINT_GROUP = "bazaar_compute_node.storages"
AUDIT_ENTRY_POINT_GROUP = "bazaar_compute_node.audits"
CONTROL_ENTRY_POINT_GROUP = "bazaar_compute_node.controls"

ChannelFactory = Callable[[], IChannel]
RuntimeFactory = Callable[[RuntimeCommandContext], IRuntime]
StorageFactory = Callable[[], IStorage]
AuditFactory = Callable[[], IAudit]


@dataclass(frozen=True, slots=True)
class AdapterFactories:
    channel: ChannelFactory
    runtime: RuntimeFactory
    storage: StorageFactory
    audit: AuditFactory
    control: Callable[[Mapping[str, object]], ControlHandler] | None = None


class ProviderLoadError(RuntimeError):
    """A selected provider is missing or has an invalid entry point."""


class AdapterRegistry:
    """Discover provider factories through Python package entry points."""

    def load(
        self,
        *,
        channel_slug: str,
        runtime_slug: str,
        storage_slug: str = "dummy",
        audit_slug: str = "dummy",
    ) -> AdapterFactories:
        control = None
        if storage_slug == "dummy":
            control = self._load_optional(
                CONTROL_ENTRY_POINT_GROUP,
                f"{channel_slug}+{runtime_slug}",
            )
            if control is None and channel_slug == runtime_slug:
                control = self._load_optional(
                    CONTROL_ENTRY_POINT_GROUP,
                    channel_slug,
                )
        return AdapterFactories(
            channel=cast(
                ChannelFactory,
                self._load(CHANNEL_ENTRY_POINT_GROUP, channel_slug),
            ),
            runtime=cast(
                RuntimeFactory,
                self._load(RUNTIME_ENTRY_POINT_GROUP, runtime_slug),
            ),
            storage=cast(
                StorageFactory,
                self._load(STORAGE_ENTRY_POINT_GROUP, storage_slug),
            ),
            audit=cast(
                AuditFactory,
                self._load(AUDIT_ENTRY_POINT_GROUP, audit_slug),
            ),
            control=cast(
                Callable[[Mapping[str, object]], ControlHandler] | None,
                control,
            ),
        )

    def _load(self, group: str, slug: str) -> Any:
        entry_point = self._find(group, slug)
        if entry_point is None:
            raise ProviderLoadError(
                f"provider '{slug}' is not installed for entry point group '{group}'"
            )
        try:
            factory = entry_point.load()
        except Exception as error:
            raise ProviderLoadError(
                f"failed to load provider '{slug}' from '{group}': {error}"
            ) from error
        if not callable(factory):
            raise ProviderLoadError(f"provider '{slug}' from '{group}' is not callable")
        return factory

    def _load_optional(self, group: str, slug: str) -> Any | None:
        entry_point = self._find(group, slug)
        if entry_point is None:
            return None
        try:
            factory = entry_point.load()
        except Exception as error:
            raise ProviderLoadError(
                f"failed to load optional provider '{slug}' from '{group}': {error}"
            ) from error
        if not callable(factory):
            raise ProviderLoadError(
                f"optional provider '{slug}' from '{group}' is not callable"
            )
        return factory

    @staticmethod
    def _find(group: str, slug: str) -> EntryPoint | None:
        if not slug:
            raise ProviderLoadError(f"provider slug for '{group}' is empty")
        return next(
            (
                candidate
                for candidate in entry_points(group=group)
                if candidate.name == slug
            ),
            None,
        )
