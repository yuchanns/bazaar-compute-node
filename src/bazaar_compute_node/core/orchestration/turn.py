from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace

from ..approval import ApprovalBinding
from ..audit import ErrorKind
from ..channel import IChannel
from ..command import SessionNotFoundError
from ..concurrency import ISessionConcurrency
from ..correlation import CorrelationContext
from ..lifecycle import TimeoutBudget
from ..models import (
    AgentSignal,
    AgentTick,
    AgentTickSource,
    ApprovalRequest,
    ApprovalResult,
    BcnSession,
    ChannelSession,
    InboundMessage,
    RuntimeEvent,
    RuntimeEventState,
    RuntimeSession,
    RuntimeTurn,
    RuntimeTurnState,
)
from ..runtime import IRuntime, IRuntimeTurnStream
from ..storage import IStorage
from .services import SessionAuditRecorder, SessionStateWriter


def _is_compaction_event(event_name: str) -> bool:
    return "compaction" in event_name.casefold()


def _is_turn_event(event_name: str) -> bool:
    return "turn" in event_name.casefold()


def _runtime_event_agent_signal(event: RuntimeEvent) -> AgentSignal:
    if _is_compaction_event(event.event_name):
        normalized = event.event_name.casefold()
        if any(token in normalized for token in ("start", "begin")):
            return AgentSignal.COMPACTION_STARTED
        if any(token in normalized for token in ("complete", "finish", "end")):
            return AgentSignal.COMPACTION_COMPLETED
        if event.state is RuntimeEventState.COMPLETED:
            return AgentSignal.COMPACTION_COMPLETED
        return AgentSignal.COMPACTION_IN_PROGRESS

    if not _is_turn_event(event.event_name):
        if event.state is RuntimeEventState.UNKNOWN:
            return AgentSignal.UNKNOWN
        if (
            event.state is RuntimeEventState.FAILED
            or "error" in event.event_name.casefold()
        ):
            return AgentSignal.FAILED
        return AgentSignal.WORKING_OBSERVED

    if event.state is RuntimeEventState.STARTED:
        return AgentSignal.TURN_STARTED
    if event.state is RuntimeEventState.COMPLETED:
        return AgentSignal.TURN_COMPLETED
    if event.state is RuntimeEventState.FAILED:
        return AgentSignal.TURN_FAILED
    if event.state is RuntimeEventState.CANCELLED:
        return AgentSignal.TURN_CANCELLED
    return AgentSignal.UNKNOWN


def _is_terminal_turn_event(event: RuntimeEvent) -> bool:
    return _is_turn_event(event.event_name) and event.state in {
        RuntimeEventState.COMPLETED,
        RuntimeEventState.FAILED,
        RuntimeEventState.CANCELLED,
        RuntimeEventState.UNKNOWN,
    }


@dataclass(frozen=True, slots=True)
class SessionContext:
    channel_session: ChannelSession
    bcn_session: BcnSession
    runtime_session: RuntimeSession


class _ApprovalHandler:
    def __init__(
        self,
        callback: Callable[[ApprovalRequest, float], Awaitable[ApprovalResult]],
    ) -> None:
        self._callback = callback

    async def request_approval(
        self, request: ApprovalRequest, *, timeout: float
    ) -> ApprovalResult:
        return await self._callback(request, timeout)


class SessionTurnCoordinator:
    """Drive runtime turns and persist their event/state transitions."""

    def __init__(
        self,
        *,
        channel: IChannel,
        runtime: IRuntime,
        storage: IStorage,
        audit: SessionAuditRecorder,
        state_writer: SessionStateWriter,
        timeout_budget: TimeoutBudget,
        concurrency: ISessionConcurrency,
        node_id: Callable[[], str],
        clock: Callable[[], int],
    ) -> None:
        self._channel = channel
        self._runtime = runtime
        self._storage = storage
        self._audit = audit
        self._state_writer = state_writer
        self._timeout_budget = timeout_budget
        self._concurrency = concurrency
        self._node_id = node_id
        self._clock = clock

    async def run_turn(
        self,
        message: InboundMessage,
        context: SessionContext,
        turn: RuntimeTurn,
    ) -> RuntimeTurn:
        binding = ApprovalBinding(
            request_id="pending",
            bcn_session_id=context.bcn_session.bcn_session_id,
            channel_session_id=context.channel_session.channel_session_id,
            agent_runtime_session_id=context.runtime_session.agent_runtime_session_id,
            turn_id=turn.turn_id,
        )
        turn_correlation = self.turn_correlation(message, context, turn)

        async def request_approval(
            request: ApprovalRequest, *, timeout: float
        ) -> ApprovalResult:
            request_id = request.request_id
            current_binding = replace(binding, request_id=request_id)
            if not current_binding.matches(request):
                raise ValueError("runtime approval request correlation mismatch")
            approval_correlation = CorrelationContext(
                node_id=self._node_id(),
                channel_slug=context.channel_session.channel_slug,
                channel_session_id=context.channel_session.channel_session_id,
                bcn_session_id=context.bcn_session.bcn_session_id,
                agent_runtime_session_id=context.runtime_session.agent_runtime_session_id,
                turn_id=turn.turn_id,
                request_id=request_id,
                inbound_seq=message.seq,
            )
            await self._audit.append(
                event_name="approval.requested",
                state=RuntimeEventState.STARTED,
                correlation=approval_correlation,
                metadata={"action": request.action},
            )
            try:
                result = await self._channel.request_approval(request, timeout=timeout)
                if result.request_id != request_id:
                    raise ValueError("channel approval result correlation mismatch")
            except Exception as error:
                await self._audit.append(
                    event_name="approval.failed",
                    state=RuntimeEventState.FAILED,
                    correlation=approval_correlation,
                    error_kind=ErrorKind.PROVIDER_FAILED,
                    error_message=f"approval failed: {type(error).__name__}",
                    metadata={"action": request.action},
                )
                raise
            await self._audit.append(
                event_name="approval.decided",
                state=RuntimeEventState.COMPLETED,
                correlation=approval_correlation,
                metadata={
                    "action": request.action,
                    "decision": result.decision.value,
                },
            )
            return result

        stream: IRuntimeTurnStream | None = None
        observed_terminal = False
        try:
            approval_handler = _ApprovalHandler(
                lambda request, timeout: request_approval(request, timeout=timeout)
            )
            await self._audit.append(
                event_name="runtime.request.turn.started",
                state=RuntimeEventState.STARTED,
                correlation=turn_correlation,
                metadata={"provider_method": "turn/start"},
            )
            try:
                stream = await self._runtime.start_turn(
                    context.runtime_session,
                    turn,
                    f"[inbox notice session={context.bcn_session.bcn_session_id}]\n"
                    "Inbox update: 1 unread message. Use the message command to read it.",
                    approval_handler,
                    timeout=self._timeout_budget.provider_call_seconds,
                )
            except Exception as error:
                await self._audit.append(
                    event_name="runtime.request.turn.failed",
                    state=RuntimeEventState.FAILED,
                    correlation=turn_correlation,
                    error_kind=ErrorKind.PROVIDER_FAILED,
                    error_message=f"turn request failed: {type(error).__name__}",
                    metadata={"provider_method": "turn/start"},
                )
                raise
            async for event in stream:
                turn = await self._apply_runtime_event(message, context, turn, event)
                if _is_terminal_turn_event(event):
                    observed_terminal = True
                    break
            if not observed_terminal:
                return await self.finish_turn(
                    turn,
                    RuntimeTurnState.UNKNOWN,
                    error_kind=ErrorKind.PROVIDER_UNKNOWN,
                    error_message="runtime stream ended without a terminal event",
                    correlation=turn_correlation,
                    bcn_session_id=context.bcn_session.bcn_session_id,
                )
            return turn
        except asyncio.CancelledError:
            await self._close_stream(stream)
            await self.finish_turn(
                turn,
                RuntimeTurnState.CANCELLED,
                error_kind=ErrorKind.CANCELLED,
                error_message="runtime turn cancelled",
                correlation=turn_correlation,
                bcn_session_id=context.bcn_session.bcn_session_id,
            )
            raise
        except Exception as error:  # noqa: BLE001
            await self._close_stream(stream)
            return await self.finish_turn(
                turn,
                RuntimeTurnState.FAILED,
                error_kind=ErrorKind.PROVIDER_FAILED,
                error_message=f"runtime turn failed: {type(error).__name__}",
                correlation=turn_correlation,
                bcn_session_id=context.bcn_session.bcn_session_id,
            )
        finally:
            await self._close_stream(stream)

    async def finish_turn(
        self,
        turn: RuntimeTurn,
        state: RuntimeTurnState,
        *,
        error_kind: ErrorKind | None,
        error_message: str | None,
        correlation: CorrelationContext | None,
        bcn_session_id: str,
    ) -> RuntimeTurn:
        async with self._concurrency.for_session(bcn_session_id):
            if turn.state in {
                RuntimeTurnState.COMPLETED,
                RuntimeTurnState.FAILED,
                RuntimeTurnState.CANCELLED,
            }:
                return turn
            async with self._storage.transaction() as transaction:
                current_turn = await transaction.get_runtime_turn(turn.turn_id)
                if current_turn is None:
                    raise ValueError(f"unknown runtime turn: {turn.turn_id}")
                if current_turn.state in {
                    RuntimeTurnState.COMPLETED,
                    RuntimeTurnState.FAILED,
                    RuntimeTurnState.CANCELLED,
                }:
                    return current_turn
                current_turn = current_turn.transition_to(
                    state,
                    at_ms=self._clock(),
                    error_kind=error_kind.value if error_kind else None,
                    error_message=error_message,
                )
                await transaction.save_runtime_turn(current_turn)
                bcn_session = await transaction.get_bcn_session(bcn_session_id)
                if bcn_session is None:
                    raise SessionNotFoundError(f"unknown bcn session: {bcn_session_id}")
                if state is RuntimeTurnState.COMPLETED:
                    agent_signal = AgentSignal.TURN_COMPLETED
                elif state is RuntimeTurnState.FAILED:
                    agent_signal = AgentSignal.TURN_FAILED
                elif state is RuntimeTurnState.CANCELLED:
                    agent_signal = AgentSignal.TURN_CANCELLED
                else:
                    agent_signal = AgentSignal.UNKNOWN
                await self._state_writer.apply_in_transaction(
                    transaction,
                    bcn_session,
                    AgentTick(
                        source=AgentTickSource.RUNTIME,
                        signal=agent_signal,
                        observed_at_ms=self._clock(),
                        error_kind=error_kind.value if error_kind else None,
                        error_message=error_message,
                    ),
                )
        await self._audit.append(
            event_name=f"runtime.turn.{state.value}",
            state=(
                RuntimeEventState.COMPLETED
                if state is RuntimeTurnState.COMPLETED
                else RuntimeEventState.CANCELLED
                if state is RuntimeTurnState.CANCELLED
                else RuntimeEventState.FAILED
                if state is not RuntimeTurnState.UNKNOWN
                else RuntimeEventState.UNKNOWN
            ),
            correlation=correlation or CorrelationContext(turn_id=turn.turn_id),
            error_kind=error_kind,
            error_message=error_message,
        )
        return current_turn

    async def _apply_runtime_event(
        self,
        message,
        context: SessionContext,
        turn: RuntimeTurn,
        event: RuntimeEvent,
    ) -> RuntimeTurn:
        if event.turn_id is not None and event.turn_id != turn.turn_id:
            raise ValueError("runtime event turn correlation mismatch")
        async with (
            self._concurrency.for_session(message.bcn_session_id),
            self._storage.transaction() as transaction,
        ):
            event = await transaction.append_runtime_event(event)
            current_turn = await transaction.get_runtime_turn(turn.turn_id)
            if current_turn is None:
                raise ValueError(f"unknown runtime turn: {turn.turn_id}")
            agent_signal = _runtime_event_agent_signal(event)
            if not _is_turn_event(event.event_name):
                target_state = current_turn.state
            elif event.state is RuntimeEventState.STARTED:
                target_state = RuntimeTurnState.RUNNING
            elif event.state is RuntimeEventState.COMPLETED:
                target_state = RuntimeTurnState.COMPLETED
            elif event.state is RuntimeEventState.FAILED:
                target_state = RuntimeTurnState.FAILED
            elif event.state is RuntimeEventState.CANCELLED:
                target_state = RuntimeTurnState.CANCELLED
            else:
                target_state = RuntimeTurnState.UNKNOWN
            provider_turn_id = event.metadata.get("provider_turn_id")
            if provider_turn_id is not None and (
                not isinstance(provider_turn_id, str) or not provider_turn_id
            ):
                raise ValueError("runtime event provider_turn_id is invalid")
            if (
                provider_turn_id is not None
                and current_turn.provider_turn_id is not None
                and current_turn.provider_turn_id != provider_turn_id
            ):
                raise ValueError("runtime event provider turn correlation mismatch")
            error_kind = event.error_kind
            if event.state is RuntimeEventState.FAILED and error_kind is None:
                error_kind = ErrorKind.PROVIDER_FAILED.value
            if event.state is RuntimeEventState.UNKNOWN and error_kind is None:
                error_kind = ErrorKind.PROVIDER_UNKNOWN.value
            if event.state is RuntimeEventState.CANCELLED and error_kind is None:
                error_kind = ErrorKind.CANCELLED.value
            updated_turn = current_turn.transition_to(
                target_state,
                at_ms=event.created_at_ms,
                error_kind=error_kind,
                error_message=event.error_message,
                latest_event_name=event.event_name,
            )
            if provider_turn_id is not None:
                updated_turn = replace(
                    updated_turn,
                    provider_turn_id=provider_turn_id,
                )
            await transaction.save_runtime_turn(updated_turn)
            bcn_session = await transaction.get_bcn_session(
                context.bcn_session.bcn_session_id
            )
            if bcn_session is None:
                raise SessionNotFoundError(
                    f"unknown bcn session: {context.bcn_session.bcn_session_id}"
                )
            await self._state_writer.apply_in_transaction(
                transaction,
                bcn_session,
                AgentTick(
                    source=AgentTickSource.RUNTIME,
                    signal=agent_signal,
                    observed_at_ms=self._clock(),
                    error_kind=error_kind,
                    error_message=event.error_message,
                ),
            )
        try:
            audit_kind = ErrorKind(event.error_kind) if event.error_kind else None
        except ValueError:
            audit_kind = ErrorKind.INTERNAL
        audit_error_message = event.error_message if audit_kind else None
        await self._audit.append(
            event_name=event.event_name,
            state=event.state,
            correlation=self.turn_correlation(message, context, updated_turn),
            error_kind=audit_kind,
            error_message=audit_error_message,
        )
        return updated_turn

    async def _close_stream(self, stream: IRuntimeTurnStream | None) -> None:
        if stream is not None:
            await stream.aclose()

    def turn_correlation(
        self,
        message: InboundMessage,
        context: SessionContext,
        turn: RuntimeTurn,
    ) -> CorrelationContext:
        return CorrelationContext(
            node_id=self._node_id(),
            channel_slug=context.channel_session.channel_slug,
            channel_session_id=context.channel_session.channel_session_id,
            bcn_session_id=context.bcn_session.bcn_session_id,
            agent_runtime_session_id=context.runtime_session.agent_runtime_session_id,
            turn_id=turn.turn_id,
            inbound_seq=message.seq,
            provider_thread_id=context.runtime_session.provider_thread_id,
            provider_turn_id=turn.provider_turn_id,
        )
