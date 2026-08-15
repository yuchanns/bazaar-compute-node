from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import cast

from .database import SqliteDatabase as _BaseSqliteDatabase
from .reminder_migration import install_reminder_migration
from .reminder_repository import ReminderSqliteTransaction

install_reminder_migration()


class SqliteDatabase(_BaseSqliteDatabase):
    def transaction(self) -> AbstractAsyncContextManager[ReminderSqliteTransaction]:
        return cast(
            AbstractAsyncContextManager[ReminderSqliteTransaction],
            ReminderSqliteTransaction(self),
        )


__all__ = ["SqliteDatabase"]
