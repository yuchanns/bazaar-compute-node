from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from ...core.storage import IHandoffStorageScope
from .database import SqliteDatabase as _BaseSqliteDatabase
from .executor import SqliteSession
from .repository import SqliteRepository

_READ_OPERATIONS = frozenset(
    {
        "count_messages",
        "count_pending_handoffs",
        "find_bcn_session",
        "find_channel_session",
        "find_message",
        "get_bcn_session",
        "get_channel_session",
        "get_consumer_cursor",
        "get_latest_message",
        "get_latest_message_seq",
        "get_next_scheduled_owned_reminder",
        "get_next_scheduled_reminder",
        "get_message",
        "get_owned_message",
        "get_owned_reminder",
        "get_reminder",
        "get_runtime_attempt",
        "list_due_owned_reminders",
        "list_due_reminders",
        "list_messages",
        "list_inbox_targets",
        "list_pending_handoffs",
        "list_unread_message_owners",
        "list_ready_attachment_paths",
        "list_reminders",
        "load_handoff_wake",
        "read_inbox_catalog",
        "read_message_history",
        "resolve_message",
        "resolve_inbox_target",
    }
)

_SNAPSHOT_READ_OPERATIONS = frozenset(
    {
        "load_handoff_wake",
        "read_message_history",
    }
)

_TRANSACTIONAL_WRITE_OPERATIONS = frozenset(
    {
        "check_outbound_freshness",
        "finalize_outbound_delivery",
        "record_inbound",
        "materialize_outbound_if_fresh",
        "materialize_owned_reminder_message",
    }
)


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

    def scope(self, agent_id: str, agent_name: str) -> IHandoffStorageScope:
        if agent_id != self.agent_id or agent_name != self.agent_name:
            raise ValueError("an Agent storage scope cannot be rebound")
        return cast(IHandoffStorageScope, self)

    def __getattr__(self, method_name: str):
        if method_name.startswith("_") or not hasattr(SqliteRepository, method_name):
            raise AttributeError(method_name)

        async def invoke(*args: object, **kwargs: object) -> object:
            async def run(session: SqliteSession) -> object:
                repository = SqliteRepository(
                    session,
                    agent_id=self.agent_id,
                    agent_name=self.agent_name,
                )
                method = getattr(repository, method_name)
                return await method(*args, **kwargs)

            if method_name in _READ_OPERATIONS:
                async with self.database.reader() as session:
                    if method_name in _SNAPSHOT_READ_OPERATIONS:
                        async with session.transaction():
                            return await run(session)
                    return await run(session)
            if method_name in _TRANSACTIONAL_WRITE_OPERATIONS:
                return await self.database.transaction_write(run)
            return await self.database._write(run)

        return invoke


class SqliteDatabase(_BaseSqliteDatabase):
    def scope(self, agent_id: str, agent_name: str) -> IHandoffStorageScope:
        return cast(
            IHandoffStorageScope,
            SqliteStorageScope(self, agent_id, agent_name),
        )

    def __getattr__(self, method_name: str):
        if method_name.startswith("_") or not hasattr(SqliteRepository, method_name):
            raise AttributeError(method_name)

        async def invoke(*args: object, **kwargs: object) -> object:
            async def run(session: SqliteSession) -> object:
                repository = SqliteRepository(session)
                method = getattr(repository, method_name)
                return await method(*args, **kwargs)

            if method_name in _READ_OPERATIONS:
                async with self.reader() as session:
                    if method_name in _SNAPSHOT_READ_OPERATIONS:
                        async with session.transaction():
                            return await run(session)
                    return await run(session)
            if method_name in _TRANSACTIONAL_WRITE_OPERATIONS:
                return await self.transaction_write(run)
            return await self._write(run)

        return invoke


__all__ = ["SqliteDatabase", "SqliteStorageScope"]
