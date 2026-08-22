from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import cast

from . import scoped_repository
from .agent_migration import install_agent_ownership_migration
from .database import SqliteDatabase as _BaseSqliteDatabase
from .inbox_migration import install_inbox_discovery_migration
from .reminder_migration import install_reminder_migration
from .reminder_repository import ReminderTransaction

install_reminder_migration()
install_agent_ownership_migration()
install_inbox_discovery_migration()


@dataclass(frozen=True, slots=True)
class SqliteStorageScope:
    database: SqliteDatabase
    agent_id: str
    agent_name: str

    def __post_init__(self) -> None:
        if not isinstance(self.agent_id, str) or not self.agent_id:
            raise ValueError("agent_id must be a non-empty string")
        if not isinstance(self.agent_name, str) or not self.agent_name:
            raise ValueError("agent_name must be a non-empty string")

    @property
    def name(self) -> str:
        return self.database.name

    async def start(self, *, timeout: float) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if not self.database.is_started:
            raise RuntimeError("shared SQLite storage is not started")

    async def stop(self, *, timeout: float) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")

    def scope(self, agent_id: str, agent_name: str) -> SqliteStorageScope:
        if agent_id != self.agent_id or agent_name != self.agent_name:
            raise ValueError("an Agent storage scope cannot be rebound")
        return self

    def transaction(
        self,
    ) -> AbstractAsyncContextManager[scoped_repository.ReminderTransaction]:
        return cast(
            AbstractAsyncContextManager[scoped_repository.ReminderTransaction],
            scoped_repository.ReminderTransaction(
                self.database,
                agent_id=self.agent_id,
                agent_name=self.agent_name,
            ),
        )


class SqliteDatabase(_BaseSqliteDatabase):
    def scope(self, agent_id: str, agent_name: str) -> SqliteStorageScope:
        return SqliteStorageScope(self, agent_id, agent_name)

    def transaction(
        self,
    ) -> AbstractAsyncContextManager[ReminderTransaction]:
        return cast(
            AbstractAsyncContextManager[ReminderTransaction],
            ReminderTransaction(self),
        )


__all__ = ["SqliteDatabase", "SqliteStorageScope"]
