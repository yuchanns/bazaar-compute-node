from __future__ import annotations

from collections.abc import Callable, Mapping

from ..audit import AuditEvent, ErrorKind
from ..command import SessionNotFoundError
from ..concurrency import ISessionConcurrency
from ..correlation import CorrelationContext
from ..lifecycle import TimeoutBudget
from ..models import (
    AgentState,
    AgentTick,
    RuntimeEventState,
    reduce_agent_tick,
)
from ..observability import IAudit, LogLevel
from ..storage import IStorage


class SessionAuditRecorder:
    """Write sanitized session audit events with one shared policy."""

    _FORBIDDEN_TOOL_ARGUMENTS = frozenset(
        {
            "access_token",
            "api_key",
            "authorization",
            "body",
            "cookie",
            "credential",
            "payload",
            "raw_payload",
            "secret",
            "token",
        }
    )

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
        await self._sink.append(
            AuditEvent(
                event_name=event_name,
                state=state,
                created_at_ms=self._clock(),
                correlation=correlation,
                level=LogLevel.ERROR if error_kind else LogLevel.INFO,
                error_kind=error_kind,
                error_message=error_message,
                metadata=metadata or {},
            ),
            timeout=self._timeout_budget.command_seconds,
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
        safe_arguments = {
            key: value
            for key, value in arguments.items()
            if key.casefold() not in self._FORBIDDEN_TOOL_ARGUMENTS
        }
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


class SessionStateWriter:
    """Serialize ticks against the process-local AgentState source of truth."""

    def __init__(
        self,
        *,
        storage: IStorage,
        concurrency: ISessionConcurrency,
        states: dict[str, AgentState],
    ) -> None:
        self._storage = storage
        self._concurrency = concurrency
        self._states = states

    def get(self, session_id: str) -> AgentState:
        return self._states.get(session_id, AgentState.CREATED)

    async def apply(self, session_id: str, tick: AgentTick) -> AgentState:
        async with self._concurrency.for_session(session_id):
            return await self.apply_locked(session_id, tick)

    async def apply_locked(self, session_id: str, tick: AgentTick) -> AgentState:
        async with self._storage.transaction() as transaction:
            bcn_session = await transaction.get_bcn_session(session_id)
            if bcn_session is None:
                raise SessionNotFoundError(f"unknown bcn session: {session_id}")
        return self.apply_observation(bcn_session.id, tick)

    def apply_observation(self, session_id: str, tick: AgentTick) -> AgentState:
        updated = reduce_agent_tick(self.get(session_id), tick)
        self._states[session_id] = updated
        return updated
