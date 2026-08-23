from __future__ import annotations

from typing import cast

from ...core.models import Handoff, InboundMessage
from ...core.storage import HandoffConflictError
from .codec import inbound_message_from_row
from .handoff_codec import handoff_from_row
from .reminder_repository import _INBOUND_COLUMNS
from .repository import SqliteTransaction

_HANDOFF_COLUMNS = (
    "handoff_id, command_id, source_session_id, target_session_id, "
    "source_message_id, body, created_at_ms, read_at_ms"
)


class HandoffRepository(SqliteTransaction):
    async def get_latest_inbound_message(
        self,
        session_id: str,
    ) -> InboundMessage | None:
        agent_predicate = self._agent_predicate()
        row = await self.fetchone(
            f"SELECT {_INBOUND_COLUMNS} FROM inbound_messages "
            f"WHERE {agent_predicate}session_id = ? ORDER BY seq DESC LIMIT 1",
            (session_id,),
        )
        if row is None:
            return None
        return inbound_message_from_row(
            row,
            await self._attachments(row["message_id"]),
        )

    async def save_handoff(self, handoff: Handoff) -> Handoff:
        agent_id = await self._handoff_agent_id(handoff)
        existing = await self._get_handoff_by_command_id(handoff.command_id)
        if existing is not None:
            if self._same_handoff_payload(existing, handoff):
                return existing
            raise HandoffConflictError(
                "handoff command id is already bound to a different payload"
            )

        await self.execute(
            "INSERT INTO handoffs ("
            "handoff_id, command_id, agent_id, source_session_id, target_session_id, "
            "source_message_id, body, created_at_ms, read_at_ms"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                handoff.handoff_id,
                handoff.command_id,
                agent_id,
                handoff.source_session_id,
                handoff.target_session_id,
                handoff.source_message_id,
                handoff.body,
                handoff.created_at_ms,
                handoff.read_at_ms,
            ),
        )
        return handoff

    async def list_pending_handoffs(
        self,
        target_session_id: str,
        *,
        limit: int,
    ) -> tuple[Handoff, ...]:
        rows = await self.fetchall(
            f"SELECT {_HANDOFF_COLUMNS} FROM handoffs "
            f"WHERE {self._agent_predicate()}target_session_id = ? "
            "AND read_at_ms IS NULL ORDER BY seq LIMIT ?",
            (target_session_id, limit),
        )
        return tuple(handoff_from_row(row) for row in rows)

    async def count_pending_handoffs(self, target_session_id: str) -> int:
        row = await self.fetchone(
            "SELECT COUNT(*) AS pending_count FROM handoffs "
            f"WHERE {self._agent_predicate()}target_session_id = ? "
            "AND read_at_ms IS NULL",
            (target_session_id,),
        )
        if row is None:
            raise RuntimeError("SQLite handoff pending count returned no row")
        return cast(int, row["pending_count"])

    async def mark_handoffs_read(
        self,
        target_session_id: str,
        handoff_ids: tuple[str, ...],
        *,
        read_at_ms: int,
    ) -> tuple[Handoff, ...]:
        if not handoff_ids:
            return ()
        placeholders = ", ".join("?" for _ in handoff_ids)
        rows = await self.fetchall(
            "UPDATE handoffs SET read_at_ms = ? "
            f"WHERE {self._agent_predicate()}target_session_id = ? "
            f"AND handoff_id IN ({placeholders}) AND read_at_ms IS NULL "
            f"RETURNING {_HANDOFF_COLUMNS}",
            (read_at_ms, target_session_id, *handoff_ids),
        )
        marked_by_id = {
            handoff.handoff_id: handoff for handoff in map(handoff_from_row, rows)
        }
        return tuple(
            marked_by_id[handoff_id]
            for handoff_id in handoff_ids
            if handoff_id in marked_by_id
        )

    async def _get_handoff_by_command_id(self, command_id: str) -> Handoff | None:
        row = await self.fetchone(
            f"SELECT {_HANDOFF_COLUMNS} FROM handoffs "
            f"WHERE {self._agent_predicate()}command_id = ?",
            (command_id,),
        )
        return handoff_from_row(row) if row is not None else None

    async def _handoff_agent_id(self, handoff: Handoff) -> str:
        session_ids = {handoff.source_session_id, handoff.target_session_id}
        placeholders = ", ".join("?" for _ in session_ids)
        bound_agent_id = self._bound_agent_id()
        if bound_agent_id is None:
            rows = await self.fetchall(
                f"SELECT id, agent_id FROM bcn_sessions WHERE id IN ({placeholders})",
                tuple(session_ids),
            )
            agents = {cast(str, row["agent_id"]) for row in rows}
            if {cast(str, row["id"]) for row in rows} != session_ids or len(
                agents
            ) != 1:
                raise ValueError("handoff sessions must belong to one Agent")
            return agents.pop()

        rows = await self.fetchall(
            "SELECT id FROM bcn_sessions WHERE agent_id = bcn_agent_id() "
            f"AND id IN ({placeholders})",
            tuple(session_ids),
        )
        if {cast(str, row["id"]) for row in rows} != session_ids:
            raise ValueError("handoff sessions must belong to the current Agent")
        return bound_agent_id

    def _bound_agent_id(self) -> str | None:
        return getattr(self, "agent_id", None)

    def _agent_predicate(self) -> str:
        return "agent_id = bcn_agent_id() AND " if self._bound_agent_id() else ""

    @staticmethod
    def _same_handoff_payload(existing: Handoff, incoming: Handoff) -> bool:
        return (
            existing.source_session_id == incoming.source_session_id
            and existing.target_session_id == incoming.target_session_id
            and existing.source_message_id == incoming.source_message_id
            and existing.body == incoming.body
            and existing.created_at_ms == incoming.created_at_ms
        )


__all__ = ["_HANDOFF_COLUMNS", "HandoffRepository"]
