"""Find the storage the configuration names."""

from __future__ import annotations

from collections.abc import Callable
from importlib.metadata import entry_points
from typing import cast

from .storage import IStorage, StorageContext

STORAGE_ENTRY_POINT_GROUP = "bazaar_compute_server.storages"

StorageFactory = Callable[[StorageContext], IStorage]


class ProviderLoadError(RuntimeError):
    """A selected provider is missing or has an invalid entry point."""


def load_storage_factory(name: str) -> StorageFactory:
    entry_point = next(
        (
            candidate
            for candidate in entry_points(group=STORAGE_ENTRY_POINT_GROUP)
            if candidate.name == name
        ),
        None,
    )
    if entry_point is None:
        raise ProviderLoadError(
            f"storage '{name}' is not installed for entry point group "
            f"'{STORAGE_ENTRY_POINT_GROUP}'"
        )
    try:
        factory = entry_point.load()
    except Exception as error:
        raise ProviderLoadError(
            f"failed to load storage '{name}' from '{STORAGE_ENTRY_POINT_GROUP}': {error}"
        ) from error
    if not callable(factory):
        raise ProviderLoadError(f"storage '{name}' entry point is not callable")
    return cast(StorageFactory, factory)


__all__ = ["STORAGE_ENTRY_POINT_GROUP", "ProviderLoadError", "load_storage_factory"]
