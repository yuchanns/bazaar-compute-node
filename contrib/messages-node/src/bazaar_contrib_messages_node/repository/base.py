from __future__ import annotations

from typing import cast
from uuid import NAMESPACE_URL, uuid5

from bazaar_contrib_messages.actor import Agent
from bazaar_contrib_storage import (
    All,
    Column,
    Compare,
    IsNull,
    Predicate,
    Record,
    Scalar,
    Select,
    Session,
    Value,
)


def equal(name: str, value: Scalar, table: str | None = None) -> Predicate:
    column = Column(name=name, table=table)
    return (
        IsNull(expression=column)
        if value is None
        else Compare(left=column, operator="=", right=Value(value=value))
    )


class RepositoryBase:
    def __init__(self, session: Session, owner: Agent, name: str):
        self._session = session
        self.owner = owner
        self.name = name

    def scope(self, *conditions: Predicate, table: str | None = None) -> All:
        return All(conditions=(equal("agent_id", self.owner.id, table), *conditions))

    async def one(
        self, request: Select, *, conflict: str | None = None
    ) -> Record | None:
        rows = await self._session.select(request)
        if len(rows) > 1 and conflict is not None:
            raise ValueError(f"multiple rows violate {conflict}")
        return rows[0] if rows else None

    async def count(self, request: Select) -> int:
        rows = await self._session.select(request)
        return cast(int, rows[0]["total"])

    def _require_workspace(self, workspace_id: str) -> None:
        if workspace_id != self.owner.id:
            raise ValueError("session workspace does not match the Agent workspace")

    def _agent_local_id(self, kind: str, local_id: str) -> str:
        if not isinstance(local_id, str) or not local_id:
            raise ValueError(f"{kind} id must be non-empty")
        return str(uuid5(NAMESPACE_URL, f"bcn:{self.owner.id}:{kind}:{local_id}"))
