from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from typing import cast

from ..audit import AuditEvent, ErrorKind
from ..command import SessionNotFoundError
from ..concurrency import ISessionConcurrency
from ..correlation import CorrelationContext
from ..lifecycle import TimeoutBudget
from ..models import (
    RuntimeEventState,
    SessionRuntimeObservation,
    SessionRuntimeState,
    reduce_session_runtime_state,
)
from ..observability import IAudit, LogLevel
from ..sanitization import omit_sensitive_fields
from ..storage import IStorageScope


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


class SessionRuntimeStateMachine:
    """Serialize observations against process-local session runtime state."""

    def __init__(
        self,
        *,
        storage: IStorageScope,
        concurrency: ISessionConcurrency,
        states: dict[str, SessionRuntimeState],
    ) -> None:
        self._storage = storage
        self._concurrency = concurrency
        self._states = states

    def get(self, session_id: str) -> SessionRuntimeState:
        return self._states.get(session_id, SessionRuntimeState.CREATED)

    async def apply(
        self,
        session_id: str,
        observation: SessionRuntimeObservation,
    ) -> SessionRuntimeState:
        async with self._concurrency.for_session(session_id):
            return await self.apply_locked(session_id, observation)

    async def apply_locked(
        self,
        session_id: str,
        observation: SessionRuntimeObservation,
    ) -> SessionRuntimeState:
        bcn_session = await self._storage.get_bcn_session(session_id)
        if bcn_session is None:
            raise SessionNotFoundError(f"unknown bcn session: {session_id}")
        return self.apply_observation(bcn_session.id, observation)

    def apply_observation(
        self,
        session_id: str,
        observation: SessionRuntimeObservation,
    ) -> SessionRuntimeState:
        updated = reduce_session_runtime_state(self.get(session_id), observation)
        self._states[session_id] = updated
        return updated

    def apply_reconciliation(
        self,
        session_id: str,
        state: SessionRuntimeState,
    ) -> SessionRuntimeState:
        self._states[session_id] = state
        return state
