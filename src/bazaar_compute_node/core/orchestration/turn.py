from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace

from ..approval import ApprovalBinding, IApprovalHandler
from ..audit import ErrorKind
from ..channel import ChannelApprovalRequest, IChannel
from ..concurrency import ISessionConcurrency
from ..correlation import CorrelationContext
from ..lifecycle import TimeoutBudget
from ..models import (
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
    SessionRuntimeObservation,
    SessionRuntimeObservationSource,
    SessionRuntimeSignal,
    StreamEvent,
)
from ..runtime import IRuntime, IRuntimeTurnStream, RuntimeSessionUnavailable
from ..storage import IStorageScope
from .services import SessionAuditRecorder, SessionRuntimeStateMachine


def _is_compaction_event(event_name: str) -> bool:
    return "compaction" in event_name.casefold()


def _is_turn_event(event_name: str) -> bool:
    return "turn" in event_name.casefold()


def _runtime_event_signal(event: RuntimeEvent) -> SessionRuntimeSignal:
    if _is_compaction_event(event.event_name):
        normalized = event.event_name.casefold()
        if any(token in normalized for token in ("start", "begin")):
            return SessionRuntimeSignal.COMPACTION_STARTED
        if any(token in normalized for token in ("complete", "finish", "end")):
            return SessionRuntimeSignal.COMPACTION_COMPLETED
        if event.state is RuntimeEventState.COMPLETED:
            return SessionRuntimeSignal.COMPACTION_COMPLETED
        return SessionRuntimeSignal.COMPACTION_IN_PROGRESS

    if not _is_turn_event(event.event_name):
        if event.state is RuntimeEventState.UNKNOWN:
            return SessionRuntimeSignal.UNKNOWN
        if (
            event.state is RuntimeEventState.FAILED
            or "error" in event.event_name.casefold()
        ):
            return SessionRuntimeSignal.FAILED
        return SessionRuntimeSignal.WORKING_OBSERVED

    if event.state is RuntimeEventState.STARTED:
        return SessionRuntimeSignal.TURN_STARTED
    if event.state is RuntimeEventState.COMPLETED:
        return SessionRuntimeSignal.TURN_COMPLETED
    if event.state is RuntimeEventState.FAILED:
        return SessionRuntimeSignal.TURN_FAILED
    if event.state is RuntimeEventState.CANCELLED:
        return SessionRuntimeSignal.TURN_CANCELLED
    return SessionRuntimeSignal.UNKNOWN


def _is_terminal_turn_event(event: RuntimeEvent) -> bool:
    return _is_turn_event(event.event_name) and event.state in {
        RuntimeEventState.COMPLETED,
        RuntimeEventState.FAILED,
        RuntimeEventState.CANCELLED,
        RuntimeEventState.UNKNOWN,
    }


def inbox_notice(session_id: str, unread_count: int) -> str:
    return (
        f"[inbox notice session={session_id}]\n"
        f"Inbox update: {unread_count} unread message(s). "
        "Use the message command to read them."
    )


def reminder_notice(session_id: str, pending_count: int) -> str:
    return (
        f"[reminder notice session={session_id}]\n"
        f"Reminders pending: {pending_count}. "
        "Use `bcc reminder check` to read them."
    )


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
        agent_id: str,
        channel: IChannel,
        runtime: IRuntime,
        storage: IStorageScope,
        audit: SessionAuditRecorder,
        state_machine: SessionRuntimeStateMachine,
        timeout_budget: TimeoutBudget,
        concurrency: ISessionConcurrency,
        turns: dict[str, RuntimeTurn],
        clock: Callable[[], int],
    ) -> None:
        if not isinstance(agent_id, str) or not agent_id:
            raise ValueError("agent_id must be a non-empty string")
        self._agent_id = agent_id
        self._channel = channel
        self._runtime = runtime
        self._storage = storage
        self._audit = audit
        self._state_machine = state_machine
        self._timeout_budget = timeout_budget
        self._concurrency = concurrency
        self._turns = turns
        self._clock = clock
        self._logger = logging.getLogger("bazaar_compute_node.orchestration.turn")

    def approval_handler(
        self,
        message: InboundMessage,
        context: SessionContext,
        turn: RuntimeTurn,
    ) -> IApprovalHandler:
        binding = ApprovalBinding(
            request_id="pending",
            bcn_session_id=context.bcn_session.id,
            channel_session_id=context.channel_session.id,
            runtime_session_id=context.runtime_session.id,
            turn_id=turn.turn_id,
        )

        async def request_approval(
            request: ApprovalRequest, *, timeout: float
        ) -> ApprovalResult:
            request_id = request.request_id
            current_binding = replace(binding, request_id=request_id)
            if not current_binding.matches(request):
                raise ValueError("runtime approval request correlation mismatch")
            approval_correlation = CorrelationContext(
                node_id=self._agent_id,
                channel=context.channel_session.channel,
                channel_session_id=context.channel_session.id,
                bcn_session_id=context.bcn_session.id,
                runtime_session_id=context.runtime_session.id,
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
                channel_request = ChannelApprovalRequest(
                    approval=request,
                    target_kind=context.channel_session.target_kind,
                    provider_thread_id=context.channel_session.provider_thread_id,
                    provider_reply_to_message_id=message.provider_message_id,
                    provider_sender_id=message.sender,
                )
                result = await self._channel.request_approval(
                    channel_request,
                    timeout=timeout,
                )
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

        return _ApprovalHandler(
            lambda request, timeout: request_approval(request, timeout=timeout)
        )

    async def run_turn(
        self,
        message: InboundMessage,
        context: SessionContext,
        turn: RuntimeTurn,
        *,
        input_text: str,
    ) -> RuntimeTurn:
        if not isinstance(input_text, str) or not input_text:
            raise ValueError("turn input_text must be a non-empty string")
        turn_correlation = self.turn_correlation(message, context, turn)
        stream: IRuntimeTurnStream | None = None
        try:
            approval_handler = self.approval_handler(message, context, turn)
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
                    input_text,
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
            return await self._consume_turn_stream(
                message,
                context,
                turn,
                stream,
                turn_correlation=turn_correlation,
            )
        except RuntimeSessionUnavailable:
            await self._close_stream(stream)
            raise
        except asyncio.CancelledError:
            await self._close_stream(stream)
            await self.finish_turn(
                turn,
                RuntimeTurnState.CANCELLED,
                error_kind=ErrorKind.CANCELLED,
                error_message="runtime turn cancelled",
                correlation=turn_correlation,
                session_id=context.bcn_session.id,
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
                session_id=context.bcn_session.id,
            )

    async def resume_turn(
        self,
        message: InboundMessage,
        context: SessionContext,
        turn: RuntimeTurn,
        stream: IRuntimeTurnStream,
    ) -> RuntimeTurn:
        return await self._consume_turn_stream(
            message,
            context,
            turn,
            stream,
            turn_correlation=self.turn_correlation(message, context, turn),
        )

    async def _consume_turn_stream(
        self,
        message: InboundMessage,
        context: SessionContext,
        turn: RuntimeTurn,
        stream: IRuntimeTurnStream,
        *,
        turn_correlation: CorrelationContext,
    ) -> RuntimeTurn:
        observed_terminal = False
        try:
            async for event in stream:
                if isinstance(event, StreamEvent):
                    if event.session_id != context.bcn_session.id:
                        self._logger.error(
                            "runtime emitted stream event for another session",
                            extra={
                                "expected_session_id": context.bcn_session.id,
                                "actual_session_id": event.session_id,
                            },
                        )
                        continue
                    try:
                        self._channel.accept_turn_event(
                            event,
                            session_id=context.bcn_session.id,
                        )
                    except Exception:
                        self._logger.exception("channel rejected turn event")
                    continue
                turn = await self._apply_runtime_event(message, context, turn, event)
                try:
                    self._channel.accept_turn_event(
                        event,
                        session_id=context.bcn_session.id,
                    )
                except Exception:
                    self._logger.exception("channel rejected runtime event")
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
                    session_id=context.bcn_session.id,
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
                session_id=context.bcn_session.id,
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
                session_id=context.bcn_session.id,
            )
        finally:
            await self._close_stream(stream)

    async def steer_turn(
        self,
        message: InboundMessage,
        context: SessionContext,
        turn: RuntimeTurn,
        *,
        input_text: str,
    ) -> None:
        if not isinstance(input_text, str) or not input_text:
            raise ValueError("turn input_text must be a non-empty string")
        try:
            accepted = await self._runtime.steer_turn(
                context.runtime_session,
                turn,
                input_text,
                timeout=self._timeout_budget.provider_call_seconds,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            self._logger.warning(
                "runtime turn steer failed",
                extra={
                    "error_type": type(error).__name__,
                    "session_id": context.bcn_session.id,
                    "turn_id": turn.turn_id,
                },
            )
            accepted = False
        try:
            await self._audit.append(
                event_name=(
                    "runtime.request.turn.steer.accepted"
                    if accepted
                    else "runtime.request.turn.steer.not_accepted"
                ),
                state=RuntimeEventState.COMPLETED,
                correlation=self.turn_correlation(message, context, turn),
                metadata={"provider_method": "turn/steer"},
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            self._logger.exception("runtime turn steer audit failed")

    async def finish_turn(
        self,
        turn: RuntimeTurn,
        state: RuntimeTurnState,
        *,
        error_kind: ErrorKind | None,
        error_message: str | None,
        correlation: CorrelationContext | None,
        session_id: str,
    ) -> RuntimeTurn:
        async with self._concurrency.for_session(session_id):
            if turn.state in {
                RuntimeTurnState.COMPLETED,
                RuntimeTurnState.FAILED,
                RuntimeTurnState.CANCELLED,
                RuntimeTurnState.UNKNOWN,
            }:
                return turn
            current_turn = turn.transition_to(
                state,
                at_ms=self._clock(),
                error_kind=error_kind.value if error_kind else None,
                error_message=error_message,
            )
            self._turns.pop(turn.turn_id, None)
            if state is RuntimeTurnState.COMPLETED:
                signal = SessionRuntimeSignal.TURN_COMPLETED
            elif state is RuntimeTurnState.FAILED:
                signal = SessionRuntimeSignal.TURN_FAILED
            elif state is RuntimeTurnState.CANCELLED:
                signal = SessionRuntimeSignal.TURN_CANCELLED
            else:
                signal = SessionRuntimeSignal.UNKNOWN
            self._state_machine.apply_observation(
                session_id,
                SessionRuntimeObservation(
                    source=SessionRuntimeObservationSource.RUNTIME,
                    signal=signal,
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
        message: InboundMessage,
        context: SessionContext,
        turn: RuntimeTurn,
        event: RuntimeEvent,
    ) -> RuntimeTurn:
        if event.turn_id is not None and event.turn_id != turn.turn_id:
            raise ValueError("runtime event turn correlation mismatch")
        async with self._concurrency.for_session(message.session_id):
            signal = _runtime_event_signal(event)
            if not _is_turn_event(event.event_name):
                target_state = turn.state
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
                and turn.provider_turn_id is not None
                and turn.provider_turn_id != provider_turn_id
            ):
                raise ValueError("runtime event provider turn correlation mismatch")
            error_kind = event.error_kind
            if event.state is RuntimeEventState.FAILED and error_kind is None:
                error_kind = ErrorKind.PROVIDER_FAILED.value
            if event.state is RuntimeEventState.UNKNOWN and error_kind is None:
                error_kind = ErrorKind.PROVIDER_UNKNOWN.value
            if event.state is RuntimeEventState.CANCELLED and error_kind is None:
                error_kind = ErrorKind.CANCELLED.value
            updated_turn = turn.transition_to(
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
            observation = SessionRuntimeObservation(
                source=SessionRuntimeObservationSource.RUNTIME,
                signal=signal,
                observed_at_ms=self._clock(),
                error_kind=error_kind,
                error_message=event.error_message,
            )
            if _is_terminal_turn_event(event):
                self._turns.pop(turn.turn_id, None)
            else:
                self._turns[turn.turn_id] = updated_turn
            self._state_machine.apply_observation(
                context.bcn_session.id,
                observation,
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
            node_id=self._agent_id,
            channel=context.channel_session.channel,
            channel_session_id=context.channel_session.id,
            bcn_session_id=context.bcn_session.id,
            runtime_session_id=context.runtime_session.id,
            turn_id=turn.turn_id,
            inbound_seq=message.seq,
            provider_thread_id=context.runtime_session.provider_thread_id,
            provider_turn_id=turn.provider_turn_id,
        )
