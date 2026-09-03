from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from typing import cast

from ..audit import AuditEvent, ErrorKind
from ..correlation import CorrelationContext
from ..lifecycle import TimeoutBudget
from ..models import (
    RuntimeEventState,
)
from ..observability import IAudit, LogLevel
from ..utils.sanitization import omit_sensitive_fields


class SessionAuditRecorder:
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
        error_kind: ErrorKind | None = None,
        error_message: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> None:
        event = AuditEvent(
            event_name=event_name,
            state=state,
            created_at_ms=self._clock(),
            correlation=correlation,
            level=LogLevel.ERROR if error_kind else LogLevel.INFO,
            error_kind=error_kind,
            error_message=error_message,
            metadata=cast(Mapping[str, object], omit_sensitive_fields(metadata or {})),
        )
        try:
            await self._sink.append(
                event,
                timeout=self._timeout_budget.command_seconds,
            )
        except asyncio.CancelledError:
            raise
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
        safe_arguments = cast(Mapping[str, object], omit_sensitive_fields(arguments))
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
                "arguments": safe_arguments,
            },
        )
