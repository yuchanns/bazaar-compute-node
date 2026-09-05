from __future__ import annotations

import re
from collections.abc import Sequence

import aiosqlite

from ....core.models import (
    ChannelSession,
    InboundAttachment,
    Message,
    MessageDirection,
    OutboundAttachment,
    OutboundDeliveryState,
    Thread,
)
from ..executor import SqliteExecuteResult, SqliteSession


class RepositoryBase:
    def __init__(
        self,
        session: SqliteSession,
        *,
        agent_id: str | None = None,
        agent_name: str | None = None,
    ) -> None:
        if (agent_id is None) != (agent_name is None):
            raise ValueError("agent_id and agent_name must be provided together")
        if agent_id is not None and not agent_id:
            raise ValueError("agent_id must be a non-empty string")
        if agent_name is not None and not agent_name:
            raise ValueError("agent_name must be a non-empty string")
        self._session = session
        self.agent_id = agent_id
        self.agent_name = agent_name

    async def execute(
        self,
        statement: str,
        parameters: Sequence[object] = (),
    ) -> SqliteExecuteResult:
        statement, parameters = self._expand_parameters(statement, parameters)
        return await self._require_session().execute(statement, parameters)

    async def fetchone(
        self,
        statement: str,
        parameters: Sequence[object] = (),
    ) -> aiosqlite.Row | None:
        statement, parameters = self._expand_parameters(statement, parameters)
        return await self._require_session().fetchone(statement, parameters)

    async def fetchall(
        self,
        statement: str,
        parameters: Sequence[object] = (),
    ) -> list[aiosqlite.Row]:
        statement, parameters = self._expand_parameters(statement, parameters)
        return await self._require_session().fetchall(statement, parameters)

    async def _fetch_one_or_conflict(
        self,
        statement: str,
        parameters: Sequence[object],
        binding_name: str,
    ) -> aiosqlite.Row | None:
        rows = await self.fetchall(statement, parameters)
        if len(rows) > 1:
            raise ValueError(f"multiple rows violate {binding_name}")
        return rows[0] if rows else None

    def _require_session(self) -> SqliteSession:
        session = self._session
        if session is None:
            raise RuntimeError("SQLite repository operation has no active session")
        return session

    def _require_agent_id(self) -> str:
        agent_id = getattr(self, "agent_id", None)
        if not isinstance(agent_id, str) or not agent_id:
            raise RuntimeError("Agent-owned write requires an Agent scope")
        return agent_id

    def _require_agent_name(self) -> str:
        agent_name = getattr(self, "agent_name", None)
        if not isinstance(agent_name, str) or not agent_name:
            raise RuntimeError("Agent-owned write requires an Agent scope")
        return agent_name

    def _expand_parameters(
        self,
        statement: str,
        parameters: Sequence[object],
    ) -> tuple[str, tuple[object, ...]]:
        markers = re.split(r"(/\*agent_id\*/\?|\?)", statement)
        if len(markers) == 1:
            return statement, tuple(parameters)
        agent_id = getattr(self, "agent_id", None)
        bound: list[object] = []
        source = iter(parameters)
        rewritten: list[str] = []
        for marker in markers:
            if marker == "?":
                rewritten.append(marker)
                try:
                    bound.append(next(source))
                except StopIteration as error:
                    raise ValueError(
                        "SQL parameter count does not match placeholders"
                    ) from error
            elif marker == "/*agent_id*/?":
                if agent_id is None:
                    raise RuntimeError("Agent-owned query requires an Agent scope")
                rewritten.append("?")
                bound.append(agent_id)
            else:
                rewritten.append(marker)
        try:
            next(source)
        except StopIteration:
            pass
        else:
            raise ValueError("SQL parameter count does not match placeholders")
        return "".join(rewritten), tuple(bound)

    async def get_thread(self, thread_id: str) -> Thread | None:
        del thread_id
        raise NotImplementedError

    async def get_channel_session(
        self, channel_session_id: str
    ) -> ChannelSession | None:
        del channel_session_id
        raise NotImplementedError

    async def get_latest_message_seq(
        self,
        thread_id: str,
        *,
        direction: MessageDirection | None = None,
        delivery_states: frozenset[OutboundDeliveryState] | None = None,
    ) -> int:
        del thread_id, direction, delivery_states
        raise NotImplementedError

    async def resolve_message(
        self,
        thread_id: str,
        message_id: str,
        *,
        direction: MessageDirection | None = None,
        delivery_states: frozenset[OutboundDeliveryState] | None = None,
    ) -> Message[InboundAttachment | OutboundAttachment] | None:
        del thread_id, message_id, direction, delivery_states
        raise NotImplementedError

    async def get_owned_message(
        self,
        agent_id: str,
        thread_id: str,
        message_id: str,
        *,
        direction: MessageDirection | None = None,
    ) -> Message[InboundAttachment | OutboundAttachment] | None:
        del agent_id, thread_id, message_id, direction
        raise NotImplementedError

    async def _attachments(self, message_id: str) -> tuple[InboundAttachment, ...]:
        del message_id
        raise NotImplementedError

    async def _save_system_message_for_agent(
        self,
        message: Message[InboundAttachment],
        agent_id: str,
    ) -> Message[InboundAttachment]:
        del message, agent_id
        raise NotImplementedError

    def _agent_local_id(self, kind: str, local_id: str) -> str:
        del kind, local_id
        raise NotImplementedError

    def _bound_agent_id(self) -> str | None:
        return self.agent_id

    def _agent_predicate(self) -> str:
        return "agent_id = /*agent_id*/? AND " if self._bound_agent_id() else ""
