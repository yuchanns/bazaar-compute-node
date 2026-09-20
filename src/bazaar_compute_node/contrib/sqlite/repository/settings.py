from __future__ import annotations

from typing import cast

from .base import RepositoryBase


class SettingOperations(RepositoryBase):
    """What an agent is set to do, by key: text an operator can change while
    the agent runs."""

    async def get_setting(self, key: str) -> str | None:
        row = await self.fetchone(
            "SELECT value FROM settings WHERE agent_id = /*agent_id*/? AND key = ?",
            (key,),
        )
        return None if row is None else cast(str, row["value"])

    async def set_setting(self, key: str, value: str, *, now_ms: int) -> None:
        await self.execute(
            "INSERT INTO settings (agent_id, key, value, updated_at_ms)"
            " VALUES (/*agent_id*/?, ?, ?, ?)"
            " ON CONFLICT (agent_id, key) DO UPDATE SET"
            " value = excluded.value, updated_at_ms = excluded.updated_at_ms",
            (key, value, now_ms),
        )


__all__ = ["SettingOperations"]
