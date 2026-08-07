from __future__ import annotations

from pathlib import Path

from ...core.storage import IStorage
from .database import SqliteDatabase


def create_storage(data_dir: Path) -> IStorage:
    return SqliteDatabase(data_dir)


__all__ = ["create_storage"]
