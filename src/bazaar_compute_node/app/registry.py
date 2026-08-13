from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import partial
from importlib.metadata import EntryPoint, entry_points
from typing import Any, cast

from ..core.channel import ChannelContext, IChannel
from ..core.observability import IAudit
from ..core.runtime import IRuntime, RuntimeCommandContext
from ..core.storage import IStorage
from .command import ControlHandler

CHANNEL_ENTRY_POINT_GROUP = "bazaar_compute_node.channels"
RUNTIME_ENTRY_POINT_GROUP = "bazaar_compute_node.runtimes"
STORAGE_ENTRY_POINT_GROUP = "bazaar_compute_node.storages"
AUDIT_ENTRY_POINT_GROUP = "bazaar_compute_node.audits"
CONTROL_ENTRY_POINT_GROUP = "bazaar_compute_node.controls"

ChannelFactory = Callable[[ChannelContext], IChannel]
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
        channel: str,
        runtime: str,
        storage: str = "sqlite",
        audit: str = "logging",
        storage_options: Mapping[str, object] | None = None,
    ) -> AdapterFactories:
        control = self._load_optional(
            CONTROL_ENTRY_POINT_GROUP,
            f"{channel}+{runtime}+{storage}",
        )
        storage_factory = cast(
            Callable[[Mapping[str, object]], IStorage] | StorageFactory,
            self._load(STORAGE_ENTRY_POINT_GROUP, storage),
        )
        if storage_options:
            storage_factory = partial(storage_factory, dict(storage_options))
        return AdapterFactories(
            channel=cast(
                ChannelFactory,
                self._load(CHANNEL_ENTRY_POINT_GROUP, channel),
            ),
            runtime=cast(
                RuntimeFactory,
                self._load(RUNTIME_ENTRY_POINT_GROUP, runtime),
            ),
            storage=cast(StorageFactory, storage_factory),
            audit=cast(
                AuditFactory,
                self._load(AUDIT_ENTRY_POINT_GROUP, audit),
            ),
            control=cast(
                Callable[[Mapping[str, object]], ControlHandler] | None,
                control,
            ),
        )

    def _load(self, group: str, name: str) -> Any:
        entry_point = self._find(group, name)
        if entry_point is None:
            raise ProviderLoadError(
                f"provider '{name}' is not installed for entry point group '{group}'"
            )
        try:
            factory = entry_point.load()
        except Exception as error:
            raise ProviderLoadError(
                f"failed to load provider '{name}' from '{group}': {error}"
            ) from error
        if not callable(factory):
            raise ProviderLoadError(f"provider '{name}' from '{group}' is not callable")
        return factory

    def _load_optional(self, group: str, name: str) -> Any | None:
        entry_point = self._find(group, name)
        if entry_point is None:
            return None
        try:
            factory = entry_point.load()
        except Exception as error:
            raise ProviderLoadError(
                f"failed to load optional provider '{name}' from '{group}': {error}"
            ) from error
        if not callable(factory):
            raise ProviderLoadError(
                f"optional provider '{name}' from '{group}' is not callable"
            )
        return factory

    @staticmethod
    def _find(group: str, name: str) -> EntryPoint | None:
        if not name:
            raise ProviderLoadError(f"provider name for '{group}' is empty")
        return next(
            (
                candidate
                for candidate in entry_points(group=group)
                if candidate.name == name
            ),
            None,
        )
