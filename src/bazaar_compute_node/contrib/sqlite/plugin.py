from __future__ import annotations

from ...core.storage import IStorage
from .database import SqliteDatabase


def create_storage() -> IStorage:
    return SqliteDatabase()


__all__ = ["create_storage"]
