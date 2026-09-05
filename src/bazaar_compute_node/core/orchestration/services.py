from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from typing import cast

from ..actor import Actor, Agent, Thread
from ..audit import AuditEvent, ErrorKind
from ..command import UnreadSummary
from ..correlation import CorrelationContext
from ..lifecycle import TimeoutBudget
from ..models import (
    RuntimeEventState,
)
from ..observability import IAudit, LogLevel
from ..storage import IStorage
from ..utils.sanitization import omit_sensitive_fields

_REACH_PAGE = 1_000


async def threads_in_reach(storage: IStorage, actor: Actor) -> tuple[str, ...]:
    """Return the conversations one actor answers for, most recent first."""

    match actor:
        case Thread(thread_id):
            return (thread_id,)
        case Agent():
            threads: list[str] = []
            offset = 0
            while True:
                page = await storage.list_inbox_targets(
                    limit=_REACH_PAGE, offset=offset
                )
                threads.extend(summary.thread_id for summary in page.targets)
                if not page.targets or not page.has_more:
                    return tuple(threads)
                offset += len(page.targets)


async def unread_in_reach(
    storage: IStorage,
    actor: Actor,
    *,
    limit: int,
) -> UnreadSummary:
    """Say what one actor has unread, counted and carried from the same read."""

    match actor:
        case Thread(thread_id):
            return await storage.read_unread_summary(thread_id, limit=limit)
        case Agent():
            return await storage.read_unread_summary(None, limit=limit)


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
