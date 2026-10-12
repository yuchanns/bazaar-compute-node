"""Public audit values and the event shared by publishers and consumers."""

from enum import StrEnum
lazy import sys
lazy from time import time_ns
lazy from typing import Self
lazy from uuid import UUID

from bazaar_core import EventKey
from pydantic import BaseModel, ConfigDict, Field, JsonValue
lazy from bazaar_core import CURRENT_CONTEXT

__all__ = ["AUDIT_EVENT", "AuditEvent", "AuditSource", "LogLevel"]


class LogLevel(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class AuditSource(BaseModel):
    """The publishing contrib and Python call site, captured before delivery."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    contrib: str | None = Field(default=None, min_length=1)
    id: UUID | None = None
    module: str = Field(min_length=1)
    pathname: str = Field(min_length=1)
    lineno: int = Field(ge=1)
    function: str = Field(min_length=1)


class AuditEvent(BaseModel):
    """One event whose metadata has been sanitized by its publishing module."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_name: str = Field(min_length=1)
    source: AuditSource
    created_at_ms: int = Field(default_factory=lambda: time_ns() // 1_000_000)
    level: LogLevel = LogLevel.INFO
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @classmethod
    def capture(
        cls,
        *,
        event_name: str,
        level: LogLevel = LogLevel.INFO,
        metadata: dict[str, JsonValue] | None = None,
        stacklevel: int = 1,
    ) -> Self:
        """Capture the current instance and caller's location."""
        ctx = CURRENT_CONTEXT.get()
        frame = sys._getframe(stacklevel)
        try:
            source = AuditSource(
                contrib=ctx.name if ctx is not None else None,
                id=ctx.id if ctx is not None else None,
                module=frame.f_globals["__name__"],
                pathname=frame.f_code.co_filename,
                lineno=frame.f_lineno,
                function=frame.f_code.co_name,
            )
        finally:
            del frame
        return cls.model_validate(
            {
                "event_name": event_name,
                "source": source,
                "level": level,
                "metadata": metadata if metadata is not None else {},
            }
        )


AUDIT_EVENT = EventKey[AuditEvent]("audit.event")
