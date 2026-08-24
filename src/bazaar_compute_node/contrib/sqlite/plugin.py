from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from ...core.storage import IStorage
from .storage import SqliteDatabase


def create_storage(options: Mapping[str, object] | None = None) -> IStorage:
    database_name = options.get("database_name") if options is not None else None
    if database_name is not None and not isinstance(database_name, str):
        raise TypeError("database_name must be text")
    return cast(IStorage, SqliteDatabase(database_name=database_name))


__all__ = ["create_storage"]
