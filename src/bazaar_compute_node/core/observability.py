from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from .audit import AuditEvent


class LogLevel(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class IAudit(Protocol):
    """Durable append-only operational event boundary."""

    @property
    def name(self) -> str:
        """Return the stable entry-point identity of this adapter."""
        ...

    async def append(self, event: AuditEvent, *, timeout: float) -> None:
        """Persist one event; cancellation must propagate to the caller."""
        ...
