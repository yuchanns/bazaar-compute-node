from __future__ import annotations

from dataclasses import dataclass, field

from .models import Handoff

HANDOFF_CHECK_LIMIT = 100


@dataclass(frozen=True, slots=True)
class HandoffSendRequest:
    target: str
    body: str
    command_id: str
    created_at_ms: int
    source_message_id: str | None = None


@dataclass(frozen=True, slots=True)
class HandoffCheckRequest:
    limit: int = field(init=False, default=HANDOFF_CHECK_LIMIT)


@dataclass(frozen=True, slots=True)
class HandoffSendResult:
    handoff: Handoff
    target: str


@dataclass(frozen=True, slots=True)
class HandoffCheckItem:
    handoff: Handoff
    source_target: str


@dataclass(frozen=True, slots=True)
class HandoffCheckResult:
    items: tuple[HandoffCheckItem, ...]
    has_more: bool


__all__ = [
    "HANDOFF_CHECK_LIMIT",
    "HandoffCheckItem",
    "HandoffCheckRequest",
    "HandoffCheckResult",
    "HandoffSendRequest",
    "HandoffSendResult",
]
