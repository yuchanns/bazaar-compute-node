from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Self


@dataclass(frozen=True, slots=True)
class Handoff:
    handoff_id: str
    command_id: str
    source_session_id: str
    target_session_id: str
    source_message_id: str | None
    body: str
    created_at_ms: int
    read_at_ms: int | None = None

    @property
    def pending(self) -> bool:
        return self.read_at_ms is None

    def mark_read(self, *, at_ms: int) -> Self:
        return replace(self, read_at_ms=at_ms)


__all__ = ["Handoff"]
