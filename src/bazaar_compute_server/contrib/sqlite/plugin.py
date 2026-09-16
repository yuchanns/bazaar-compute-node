from __future__ import annotations

from ...storage import IStorage, StorageContext
from .storage import SqliteStorage


def create_storage(context: StorageContext) -> IStorage:
    return SqliteStorage(
        context.data_dir / "bcs.sqlite3", retention_days=context.retention_days
    )


__all__ = ["create_storage"]
