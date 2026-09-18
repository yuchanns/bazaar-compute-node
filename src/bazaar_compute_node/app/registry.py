from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import partial
from importlib.metadata import EntryPoint, entry_points
from typing import Any, cast

from ..core.channel import IChannelBuilder
from ..core.control import ControlContext, IControl
from ..core.observability import AuditContext, IAudit
from ..core.runtime import IRuntime, RuntimeCommandContext
from ..core.storage import IStorage

CHANNEL_ENTRY_POINT_GROUP = "bazaar_compute_node.channels"
RUNTIME_ENTRY_POINT_GROUP = "bazaar_compute_node.runtimes"
STORAGE_ENTRY_POINT_GROUP = "bazaar_compute_node.storages"
AUDIT_ENTRY_POINT_GROUP = "bazaar_compute_node.audits"
CONTROL_ENTRY_POINT_GROUP = "bazaar_compute_node.controls"

RuntimeFactory = Callable[[RuntimeCommandContext], IRuntime]
StorageFactory = Callable[[], IStorage]
AuditFactory = Callable[[AuditContext], IAudit]
ControlFactory = Callable[[ControlContext], IControl]
# what a node without a management plane says for its control
NO_CONTROL = "none"


@dataclass(frozen=True, slots=True)
class SharedAdapterFactories:
    storage: StorageFactory
    audit: AuditFactory
    control: ControlFactory | None = None


@dataclass(frozen=True, slots=True)
class AgentAdapterFactories:
    channels: Mapping[str, IChannelBuilder]
    runtimes: Mapping[str, RuntimeFactory]


class ProviderLoadError(RuntimeError):
    """A selected provider is missing or has an invalid entry point."""


class AdapterRegistry:
    """Discover shared and Agent-scoped provider factories."""

    def load_shared(
        self,
        *,
        storage: str = "sqlite",
        audit: str = "logging",
        control: str = "none",
        storage_options: Mapping[str, object] | None = None,
    ) -> SharedAdapterFactories:
        storage_factory = cast(
            Callable[[Mapping[str, object]], IStorage] | StorageFactory,
            self._load(STORAGE_ENTRY_POINT_GROUP, storage),
        )
        if storage_options:
            storage_factory = partial(storage_factory, dict(storage_options))
        return SharedAdapterFactories(
            storage=cast(StorageFactory, storage_factory),
            audit=cast(AuditFactory, self._load(AUDIT_ENTRY_POINT_GROUP, audit)),
            control=(
                None
                if control == NO_CONTROL
                else cast(
                    ControlFactory, self._load(CONTROL_ENTRY_POINT_GROUP, control)
                )
            ),
        )

    def load_agent(
        self,
        *,
        channels: Sequence[str],
        runtimes: Sequence[str],
    ) -> AgentAdapterFactories:
        return AgentAdapterFactories(
            channels={
                kind: self._load_channel_builder(kind)
                for kind in dict.fromkeys(channels)
            },
            runtimes={
                kind: cast(
                    RuntimeFactory,
                    self._load(RUNTIME_ENTRY_POINT_GROUP, kind),
                )
                for kind in dict.fromkeys(runtimes)
            },
        )

    def _load_channel_builder(self, name: str) -> IChannelBuilder:
        entry_point = self._find(CHANNEL_ENTRY_POINT_GROUP, name)
        if entry_point is None:
            raise ProviderLoadError(
                f"provider '{name}' is not installed for entry point group "
                f"'{CHANNEL_ENTRY_POINT_GROUP}'"
            )
        try:
            builder = entry_point.load()
        except Exception as error:
            raise ProviderLoadError(
                f"failed to load provider '{name}' from "
                f"'{CHANNEL_ENTRY_POINT_GROUP}': {error}"
            ) from error
        if not callable(getattr(builder, "build", None)):
            raise ProviderLoadError(
                f"provider '{name}' from '{CHANNEL_ENTRY_POINT_GROUP}' "
                "does not provide a callable build method"
            )
        return cast(IChannelBuilder, builder)

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
