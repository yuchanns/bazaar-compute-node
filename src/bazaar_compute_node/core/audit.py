from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import cast

from .correlation import CorrelationContext
from .lifecycle import TimeoutBudget
from .models import RuntimeEventState
from .observability import IAudit, LogLevel
from .utils.sanitization import is_sensitive_field, omit_sensitive_fields


class ErrorKind(StrEnum):
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    VALIDATION = "validation"
    SESSION_NOT_FOUND = "session_not_found"
    EMPTY_BODY = "empty_body"
    FRESH_CHECK_REQUIRED = "fresh_check_required"
    FRESH_CHECK_FAILED = "fresh_check_failed"
    PROVIDER_FAILED = "provider_failed"
    PROVIDER_PARTIAL = "provider_partial"
    PROVIDER_UNKNOWN = "provider_unknown"
    PROTOCOL = "protocol"
    STORAGE = "storage"
    SHUTDOWN_TIMEOUT = "shutdown_timeout"
    INTERNAL = "internal"


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """Sanitized append-only event contract shared by all adapters."""

    event_name: str
    state: RuntimeEventState
    created_at_ms: int
    correlation: CorrelationContext
    level: LogLevel = LogLevel.INFO
    duration_ms: int | None = None
    error_kind: ErrorKind | None = None
    error_type: str | None = None
    error_message: str | None = None
    traceback_ref: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.error_kind is None and any(
            value is not None
            for value in (
                self.error_type,
                self.error_message,
                self.traceback_ref,
            )
        ):
            raise ValueError("error details require an error_kind")
        pending: list[object] = [self.metadata]
        while pending:
            value = pending.pop()
            if isinstance(value, Mapping):
                for key, item in value.items():
                    if not isinstance(key, str):
                        raise TypeError("audit metadata keys must be strings")
                    if is_sensitive_field(key):
                        raise ValueError(
                            f"audit metadata cannot contain sensitive field: {key}"
                        )
                    pending.append(item)
            elif isinstance(value, list | tuple):
                pending.extend(value)


class AuditRecorder:
    """Write sanitized session audit events with one shared policy."""

    def __init__(
        self,
        *,
        sink: IAudit,
        timeout_budget: TimeoutBudget,
        clock: Callable[[], int],
    ) -> None:
        self._sink = sink
        self._timeout_budget = timeout_budget
        self._clock = clock
        self._logger = logging.getLogger("bazaar_compute_node.audit.fallback")

    async def append(
        self,
        *,
        event_name: str,
        state: RuntimeEventState,
        correlation: CorrelationContext,
        level: LogLevel | None = None,
        error_kind: ErrorKind | None = None,
        error_message: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> None:
        # how severe an event is belongs to whoever raises it; the fallback is
        # for the callers that have no opinion
        if level is None:
            level = LogLevel.ERROR if error_kind else LogLevel.INFO
        event = AuditEvent(
            event_name=event_name,
            state=state,
            created_at_ms=self._clock(),
            correlation=correlation,
            level=level,
            error_kind=error_kind,
            error_message=error_message,
            metadata=cast(Mapping[str, object], omit_sensitive_fields(metadata or {})),
        )
        try:
            await self._sink.append(
                event,
                timeout=self._timeout_budget.command_seconds,
            )
        except Exception:
            self._logger.exception(
                "audit append failed",
                extra={"event_name": event_name},
            )

    async def append_tool(
        self,
        *,
        operation: str,
        status: str,
        state: RuntimeEventState,
        correlation: CorrelationContext,
        arguments: Mapping[str, object],
        error_kind: ErrorKind | None = None,
        error_message: str | None = None,
    ) -> None:
        await self.append(
            event_name=f"tool.{operation}.{status}",
            state=state,
            correlation=correlation,
            error_kind=error_kind,
            error_message=error_message,
            metadata={
                "kind": "tool_call",
                "operation": operation,
                "status": status,
                "arguments": arguments,
            },
        )
