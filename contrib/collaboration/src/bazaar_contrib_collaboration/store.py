from __future__ import annotations

from typing import cast

from bazaar_contrib_messages import Agent
from bazaar_contrib_storage import (
    All,
    Column,
    Compare,
    Conflict,
    Database,
    Insert,
    Select,
    Value,
)


class CollaborationStore:
    """What an agent is set to do, by key: text an operator can change while
    the agent runs."""

    def __init__(self, database: Database, owner: Agent):
        self.database = database
        self.owner = owner

    async def get_setting(self, key: str) -> str | None:
        rows = await self.database.read(
            lambda session: session.select(
                Select(
                    table="settings",
                    columns={"value": Column(name="value")},
                    where=All(
                        conditions=(
                            Compare(
                                left=Column(name="agent_id"),
                                operator="=",
                                right=Value(value=self.owner.id),
                            ),
                            Compare(
                                left=Column(name="key"),
                                operator="=",
                                right=Value(value=key),
                            ),
                        )
                    ),
                )
            )
        )
        return None if not rows else cast(str, rows[0]["value"])

    async def set_setting(self, key: str, value: str, *, now_ms: int) -> None:
        await self.database.write(
            lambda session: session.insert(
                Insert(
                    table="settings",
                    rows=(
                        {
                            "agent_id": self.owner.id,
                            "key": key,
                            "value": value,
                            "updated_at_ms": now_ms,
                        },
                    ),
                    conflict=Conflict(
                        columns=("agent_id", "key"), update=("value", "updated_at_ms")
                    ),
                )
            )
        )
