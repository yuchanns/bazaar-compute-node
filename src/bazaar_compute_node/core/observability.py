from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from .audit import AuditEvent


class LogLevel(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class LogRecord:
    """Structured runtime log record without raw provider payloads."""

    level: LogLevel
    event_name: str
    message: str
    fields: Mapping[str, object] = field(default_factory=dict)


class ILogger(Protocol):
    """Synchronous, non-blocking stderr logging boundary."""

    def emit(self, record: LogRecord, *, error: BaseException | None = None) -> None:
        """Emit a redacted record without blocking the asyncio event loop."""
        ...


class IAudit(Protocol):
    """Durable append-only operational event boundary."""

    async def append(self, event: AuditEvent, *, timeout: float) -> None:
        """Persist one event; cancellation must propagate to the caller."""
        ...
