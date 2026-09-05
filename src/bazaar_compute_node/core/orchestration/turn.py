from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace

from ... import __distribution__
from ...rendering import TextTemplate
from ..actor import Actor
from ..actor import Agent as AgentActor
from ..actor import Thread as ThreadActor
from ..agent import Agent, State
from ..approval import ApprovalBinding, IApprovalHandler
from ..audit import ErrorKind
from ..channel import ChannelApprovalRequest, IChannel
from ..concurrency import ISessionConcurrency
from ..correlation import CorrelationContext
from ..lifecycle import TimeoutBudget
from ..models import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalResult,
    BcnSession,
    ChannelSession,
    ChannelTargetKind,
    ContentDelta,
    ContextCompactionCompleted,
    ContextCompactionStarted,
    Message,
    RuntimeEventEnvelope,
    RuntimeEventState,
    RuntimeOutputEvent,
    RuntimeSession,
    RuntimeTurn,
    RuntimeTurnState,
    SenderKind,
    ToolCallCompleted,
    ToolCallFailed,
    ToolCallInteraction,
    ToolCallPatchUpdated,
    ToolCallStarted,
    ToolCallTextDelta,
    TurnCancelled,
    TurnCompleted,
    TurnFailed,
    TurnStarted,
    TurnUnknown,
    UsageUpdated,
)
from ..runtime import IRuntimeTurnStream, Runtime, RuntimeSessionUnavailable
from ..storage import IStorageScope
from .reminder import resolve_reminder_anchor
from .services import SessionAuditRecorder


def _is_turn_event(event_name: str) -> bool:
    return "turn" in event_name.casefold()


type TurnPayload = (
    TurnStarted | TurnCompleted | TurnFailed | TurnCancelled | TurnUnknown
)


type _Advance = Callable[[Agent, Actor], State]


def _runtime_event_advance(payload: TurnPayload) -> _Advance:
    match payload:
        case TurnStarted(event_name=event_name):
            state = RuntimeEventState.STARTED
        case TurnCompleted(event_name=event_name):
            state = RuntimeEventState.COMPLETED
        case TurnFailed(event_name=event_name):
            state = RuntimeEventState.FAILED
        case TurnCancelled(event_name=event_name):
            state = RuntimeEventState.CANCELLED
        case TurnUnknown(event_name=event_name):
            state = RuntimeEventState.UNKNOWN

    if not _is_turn_event(event_name):
        # a provider event outside any turn only tells us whether the runtime
        # still looks healthy, and an unhealthy one has to be reconciled
        if state in {RuntimeEventState.UNKNOWN, RuntimeEventState.FAILED} or (
            "error" in event_name.casefold()
        ):
            return Agent.lost_runtime
        return Agent.started_turn

    return _EVENTS[state].advance


@dataclass(frozen=True, slots=True)
class _Event:
    turn_state: RuntimeTurnState
    advance: _Advance


_EVENTS: Mapping[RuntimeEventState, _Event] = {
    RuntimeEventState.STARTED: _Event(RuntimeTurnState.RUNNING, Agent.started_turn),
    RuntimeEventState.COMPLETED: _Event(
        RuntimeTurnState.COMPLETED, Agent.finished_turn
    ),
    RuntimeEventState.FAILED: _Event(RuntimeTurnState.FAILED, Agent.finished_turn),
    RuntimeEventState.CANCELLED: _Event(
        RuntimeTurnState.CANCELLED, Agent.finished_turn
    ),
    RuntimeEventState.UNKNOWN: _Event(RuntimeTurnState.UNKNOWN, Agent.lost_runtime),
}


@dataclass(frozen=True, slots=True)
class _Terminal:
    payload: Callable[[str | None, str | None], TurnPayload]
    advance: _Advance
    audit_state: RuntimeEventState
    retryable: bool = False


_TERMINALS: Mapping[RuntimeTurnState, _Terminal] = {
    RuntimeTurnState.COMPLETED: _Terminal(
        payload=lambda *_: TurnCompleted(event_name="bcn.turn.completed"),
        advance=Agent.finished_turn,
        audit_state=RuntimeEventState.COMPLETED,
    ),
    RuntimeTurnState.CANCELLED: _Terminal(
        payload=lambda *_: TurnCancelled(event_name="bcn.turn.cancelled"),
        advance=Agent.finished_turn,
        audit_state=RuntimeEventState.CANCELLED,
    ),
    RuntimeTurnState.FAILED: _Terminal(
        payload=lambda kind, message: TurnFailed(
            event_name="bcn.turn.failed",
            error_kind=kind or ErrorKind.PROVIDER_FAILED.value,
            error_message=message,
        ),
        advance=Agent.finished_turn,
        audit_state=RuntimeEventState.FAILED,
        retryable=True,
    ),
    RuntimeTurnState.UNKNOWN: _Terminal(
        payload=lambda kind, message: TurnUnknown(
            event_name="bcn.turn.unknown",
            error_kind=kind,
            error_message=message,
        ),
        advance=Agent.lost_runtime,
        audit_state=RuntimeEventState.UNKNOWN,
        retryable=True,
    ),
}
_TURN_FAILED = "Turn failed"
_TURN_UNKNOWN = "Turn outcome is unknown"
_INBOX_NOTICE = TextTemplate.from_resource("inbox_notice.tpl")
_NO_PERSON_TO_APPROVE = TextTemplate.from_resource("approval_no_person.tpl").render()
_APPROVED_WITHOUT_ASKING = (
    "This Agent answers for every conversation and allows tool use without asking."
)


def _with_a_reason(event: RuntimeOutputEvent) -> RuntimeOutputEvent:
    """Give an ending a reason of our own when the runtime supplied none.

    Which runtime it was is ours to know, not the reader's, so adapters do not
    put their own name in the text. An unknown ending keeps its own words: the
    provider may yet have finished the work, and calling that a failure invites
    a retry of something already done.
    """

    match event.payload:
        case TurnFailed(error_message=None):
            return replace(
                event, payload=replace(event.payload, error_message=_TURN_FAILED)
            )
        case TurnUnknown(error_message=None):
            return replace(
                event, payload=replace(event.payload, error_message=_TURN_UNKNOWN)
            )
        case _:
            return event


def _is_terminal_turn_event(payload: TurnPayload) -> bool:
    match payload:
        case TurnStarted():
            return False
        case (
            TurnCompleted(event_name=event_name)
            | TurnFailed(event_name=event_name)
            | TurnCancelled(event_name=event_name)
            | TurnUnknown(event_name=event_name)
        ):
            return _is_turn_event(event_name)


def inbox_notice(
    messages: Sequence[Message],
    *,
    total_unread_count: int,
    closing_bracket_on_own_line: bool,
    upgrade_version: str | None = None,
    installed_version: str | None = None,
) -> str:
    if not messages:
        raise ValueError("inbox notice requires at least one changed message")
    if total_unread_count < len(messages):
        raise ValueError("total unread count cannot be smaller than changed messages")

    buckets: dict[str, list[Message]] = {}
    for message in messages:
        buckets.setdefault(message.target, []).append(message)

    rows: list[tuple[int, str, dict[str, object]]] = []
    for target, changed in buckets.items():
        ordered = sorted(changed, key=lambda message: (message.seq, message.message_id))
        first = ordered[0]
        latest = ordered[-1]
        flags: set[str] = set()
        if any(message.target_kind is ChannelTargetKind.DM for message in ordered):
            flags.add("dm")
        if any(message.mentions_agent for message in ordered):
            flags.add("mention")
        if any(message.metadata.get("threaded") is True for message in ordered):
            flags.add("thread")
        rows.append(
            (
                latest.seq,
                target,
                {
                    "target": target,
                    "pending_count": len(ordered),
                    "first_id": first.message_id[:8],
                    "latest_sender": (
                        latest.sender.label if latest.sender is not None else None
                    ),
                    "latest_id": latest.message_id[:8],
                    "flags": sorted(flags),
                },
            )
        )

    rows.sort(key=lambda row: (-row[0], row[1]))
    return _INBOX_NOTICE.render(
        {
            "distribution": __distribution__,
            "total_unread_count": total_unread_count,
            "rows": [row[2] for row in rows],
            "upgrade_version": upgrade_version,
            "installed_version": installed_version,
            # Windows has nothing that would bring the node back after an
            # upgrade exits it, so there the user runs the commands themselves
            "manual_upgrade": os.name == "nt",
            "closing_bracket_on_own_line": closing_bracket_on_own_line,
        }
    )


@dataclass(frozen=True, slots=True)
class SessionContext:
    channel_session: ChannelSession
    bcn_session: BcnSession
    runtime_session: RuntimeSession

    @property
    def actor(self) -> Actor:
        return self.runtime_session.actor


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
        runtimes: Runtime,
        storage: IStorageScope,
        audit: SessionAuditRecorder,
        agent: Agent,
        timeout_budget: TimeoutBudget,
        concurrency: ISessionConcurrency,
        turns: dict[str, RuntimeTurn],
        clock: Callable[[], int],
    ) -> None:
        if not isinstance(agent_id, str) or not agent_id:
            raise ValueError("agent_id must be a non-empty string")
        self._agent_id = agent_id
        self._channel = channel
        self._runtimes = runtimes
        self._storage = storage
        self._audit = audit
        self._agent = agent
        self._timeout_budget = timeout_budget
        self._concurrency = concurrency
        self._turns = turns
        self._turn_threads: dict[str, dict[str, Message]] = {}
        self._clock = clock
        self._logger = logging.getLogger("bazaar_compute_node.orchestration.turn")

    async def join_turn(self, turn_id: str, thread_id: str, message: Message) -> None:
        """Take a conversation into a turn, and say where its output belongs."""

        threads = self._turn_threads.setdefault(turn_id, {})
        if thread_id in threads:
            return
        threads[thread_id] = message
        anchor = await resolve_reminder_anchor(self._storage, self._agent_id, message)
        if anchor is not None:
            self._channel.anchor_turn(thread_id, anchor)

    def threads_in_turn(self, turn_id: str) -> tuple[Message, ...]:
        """Return one message from each conversation a turn has taken one from."""

        return tuple(self._turn_threads.get(turn_id, {}).values())

    def release_turn(self, turn_id: str) -> None:
        """Forget the conversations a turn no longer answers for."""

        self._turn_threads.pop(turn_id, None)

    def _threads_of(self, turn_id: str, thread_id: str) -> tuple[str, ...]:
        return tuple(self._turn_threads.get(turn_id) or (thread_id,))

    def approval_handler(
        self,
        message: Message,
        context: SessionContext,
        turn: RuntimeTurn,
    ) -> IApprovalHandler:
        return _ApprovalHandler(
            lambda request, timeout: self._request_approval(
                request,
                message=message,
                context=context,
                turn=turn,
                timeout=timeout,
            )
        )

    async def _ask_channel(
        self,
        request: ApprovalRequest,
        context: SessionContext,
        approval_target: Message,
        *,
        correlation: CorrelationContext,
        metadata: dict[str, object],
        timeout: float,
    ) -> ApprovalResult:
        """Put the request in front of a person; note a channel that could not."""

        try:
            channel_request = ChannelApprovalRequest(
                approval=request,
                target_kind=context.channel_session.target_kind,
                provider_thread_id=context.channel_session.provider_thread_id,
                provider_reply_to_message_id=(approval_target.provider_message_id),
                provider_sender_id=(
                    approval_target.sender.id
                    if approval_target.sender is not None
                    else None
                ),
            )
            result = await self._channel.request_approval(
                channel_request,
                timeout=timeout,
            )
            if result.request_id != request.request_id:
                raise ValueError("channel approval result correlation mismatch")
        except Exception as error:
            await self._audit.append(
                event_name="approval.failed",
                state=RuntimeEventState.FAILED,
                correlation=correlation,
                error_kind=ErrorKind.PROVIDER_FAILED,
                error_message=f"approval failed: {type(error).__name__}",
                metadata=metadata,
            )
            raise
        return result

    async def _request_approval(
        self,
        request: ApprovalRequest,
        *,
        message: Message,
        context: SessionContext,
        turn: RuntimeTurn,
        timeout: float,
    ) -> ApprovalResult:
        """Settle what the runtime wants to do, by asking or by the mode itself."""

        request_id = request.request_id
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
        match context.actor:
            case AgentActor():
                # TODO: unconditional only until an individual mode keeps
                # approvals; then decide here from the sandbox policy
                return await self._approve_without_asking(
                    request,
                    message,
                    correlation=approval_correlation,
                )
            case ThreadActor():
                pass
        current_binding = ApprovalBinding(
            request_id=request_id,
            actor=context.actor,
            channel_session_id=context.channel_session.id,
            runtime_session_id=context.runtime_session.id,
            turn_id=turn.turn_id,
        )
        if not current_binding.matches(request):
            raise ValueError("runtime approval request correlation mismatch")
        sender_kind = message.sender_kind
        approval_target = await resolve_reminder_anchor(
            self._storage,
            self._agent_id,
            message,
        )
        approval_target_kind = (
            approval_target.sender_kind.value
            if approval_target is not None
            else "unavailable"
        )
        metadata: dict[str, object] = {
            "action": request.action,
            "sender_kind": sender_kind.value,
            "approval_target_kind": approval_target_kind,
        }
        await self._audit.append(
            event_name="approval.requested",
            state=RuntimeEventState.STARTED,
            correlation=approval_correlation,
            metadata=metadata,
        )
        if approval_target is None or (
            approval_target.sender_kind is not SenderKind.HUMAN
            or approval_target.sender is None
            or approval_target.sender.id is None
        ):
            result = ApprovalResult(
                request_id=request_id,
                decision=ApprovalDecision.REJECTED,
                decided_at_ms=self._clock(),
                reason=_NO_PERSON_TO_APPROVE,
            )
        else:
            result = await self._ask_channel(
                request,
                context,
                approval_target,
                correlation=approval_correlation,
                metadata=metadata,
                timeout=timeout,
            )
        await self._audit.append(
            event_name="approval.decided",
            state=RuntimeEventState.COMPLETED,
            correlation=approval_correlation,
            metadata=metadata
            | {
                "decision": result.decision.value,
                **({"reason": result.reason} if result.reason is not None else {}),
            },
        )
        return result

    async def _approve_without_asking(
        self,
        request: ApprovalRequest,
        message: Message,
        *,
        correlation: CorrelationContext,
    ) -> ApprovalResult:
        """Allow what the runtime asked for, with no one conversation to ask."""

        result = ApprovalResult(
            request_id=request.request_id,
            decision=ApprovalDecision.APPROVED,
            decided_at_ms=self._clock(),
            reason=_APPROVED_WITHOUT_ASKING,
        )
        await self._audit.append(
            event_name="approval.decided",
            state=RuntimeEventState.COMPLETED,
            correlation=correlation,
            metadata={
                "action": request.action,
                "sender_kind": message.sender_kind.value,
                "decision": result.decision.value,
                "reason": result.reason,
            },
        )
        return result

    async def run_turn(
        self,
        message: Message,
        context: SessionContext,
        turn: RuntimeTurn,
        *,
        input_text: str,
        retry_available: bool = False,
    ) -> RuntimeTurn:
        if not isinstance(input_text, str) or not input_text:
            raise ValueError("turn input_text must be a non-empty string")
        turn_correlation = self.turn_correlation(message, context, turn)
        stream: IRuntimeTurnStream | None = None
        try:
            await self.join_turn(turn.turn_id, context.bcn_session.id, message)
            approval_handler = self.approval_handler(message, context, turn)
            await self._audit.append(
                event_name="runtime.request.turn.started",
                state=RuntimeEventState.STARTED,
                correlation=turn_correlation,
                metadata={"provider_method": "turn/start"},
            )
            try:
                stream = await self._runtimes.get(
                    context.runtime_session.runtime_index
                ).start_turn(
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
                retry_available=retry_available,
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
                actor=context.actor,
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
                actor=context.actor,
                session_id=context.bcn_session.id,
                retry_available=retry_available,
            )

    async def resume_turn(
        self,
        message: Message,
        context: SessionContext,
        turn: RuntimeTurn,
        stream: IRuntimeTurnStream,
        *,
        retry_available: bool = False,
    ) -> RuntimeTurn:
        return await self._consume_turn_stream(
            message,
            context,
            turn,
            stream,
            turn_correlation=self.turn_correlation(message, context, turn),
            retry_available=retry_available,
        )

    async def _consume_events(
        self,
        message: Message,
        context: SessionContext,
        turn: RuntimeTurn,
        stream: IRuntimeTurnStream,
        *,
        retry_available: bool,
    ) -> tuple[RuntimeTurn, bool]:
        """Read the stream out, saying whether it reached a terminal event."""

        observed_terminal = False
        async for event in stream:
            if event.envelope.actor != context.actor:
                self._logger.error(
                    "runtime emitted event for another actor",
                    extra={
                        "expected_actor_id": context.actor.id,
                        "actual_actor_id": event.envelope.actor.id,
                    },
                )
                continue
            event = _with_a_reason(event)
            match event.payload:
                case (
                    TurnStarted()
                    | TurnCompleted()
                    | TurnFailed()
                    | TurnCancelled()
                    | TurnUnknown()
                ) as payload:
                    turn = await self._apply_runtime_event(
                        message,
                        context,
                        turn,
                        event.envelope,
                        payload,
                    )
                    if not (
                        isinstance(payload, TurnUnknown)
                        or (retry_available and isinstance(payload, TurnFailed))
                    ):
                        self._forward(event, turn.turn_id, context.bcn_session.id)
                    if _is_terminal_turn_event(payload):
                        observed_terminal = True
                        break
                case (
                    ContentDelta()
                    | ToolCallStarted()
                    | ToolCallCompleted()
                    | ToolCallFailed()
                    | ToolCallTextDelta()
                    | ToolCallPatchUpdated()
                    | ToolCallInteraction()
                    | UsageUpdated()
                    | ContextCompactionStarted()
                    | ContextCompactionCompleted()
                ):
                    self._forward(event, turn.turn_id, context.bcn_session.id)
        return turn, observed_terminal

    def _forward(
        self,
        event: RuntimeOutputEvent,
        turn_id: str,
        thread_id: str,
    ) -> None:
        """Show a runtime event on the channel; note a channel that refuses it."""

        for thread in self._threads_of(turn_id, thread_id):
            try:
                self._channel.accept_turn_event(event, session_id=thread)
            except Exception:
                self._logger.exception(
                    "channel rejected %s", type(event.payload).__name__
                )

    async def _consume_turn_stream(
        self,
        message: Message,
        context: SessionContext,
        turn: RuntimeTurn,
        stream: IRuntimeTurnStream,
        *,
        turn_correlation: CorrelationContext,
        retry_available: bool = False,
    ) -> RuntimeTurn:
        try:
            turn, observed_terminal = await self._consume_events(
                message, context, turn, stream, retry_available=retry_available
            )
            if not observed_terminal:
                return await self.finish_turn(
                    turn,
                    RuntimeTurnState.UNKNOWN,
                    error_kind=ErrorKind.PROVIDER_UNKNOWN,
                    error_message="runtime stream ended without a terminal event",
                    correlation=turn_correlation,
                    actor=context.actor,
                    session_id=context.bcn_session.id,
                    retry_available=retry_available,
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
                actor=context.actor,
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
                actor=context.actor,
                session_id=context.bcn_session.id,
                retry_available=retry_available,
            )
        finally:
            await self._close_stream(stream)

    async def steer_turn(
        self,
        message: Message,
        context: SessionContext,
        turn: RuntimeTurn,
        *,
        input_text: str,
    ) -> None:
        if not isinstance(input_text, str) or not input_text:
            raise ValueError("turn input_text must be a non-empty string")
        try:
            accepted = await self._runtimes.get(
                context.runtime_session.runtime_index
            ).steer_turn(
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
        if accepted:
            await self.join_turn(turn.turn_id, context.bcn_session.id, message)
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

    def notify_terminal(self, turn: RuntimeTurn, actor: Actor, session_id: str) -> None:
        """Announce a turn whose outcome only the orchestrator can settle."""

        self._notify_channel_terminal(
            turn,
            turn.state,
            ErrorKind(turn.error_kind) if turn.error_kind else None,
            turn.error_message,
            actor,
            session_id,
        )

    def _notify_channel_terminal(
        self,
        turn: RuntimeTurn,
        state: RuntimeTurnState,
        error_kind: ErrorKind | None,
        error_message: str | None,
        actor: Actor,
        session_id: str,
    ) -> None:
        payload = _TERMINALS[state].payload(
            error_kind.value if error_kind else None,
            error_message,
        )
        event = RuntimeOutputEvent(
            envelope=RuntimeEventEnvelope(
                actor=actor,
                runtime_session_id=turn.session_id,
                turn_id=turn.turn_id,
                provider_turn_id=turn.provider_turn_id,
                occurred_at_ms=self._clock(),
            ),
            payload=payload,
        )
        for thread in self._threads_of(turn.turn_id, session_id):
            try:
                self._channel.accept_turn_event(event, session_id=thread)
            except Exception:
                self._logger.exception("channel rejected synthesized terminal event")

    async def finish_turn(
        self,
        turn: RuntimeTurn,
        state: RuntimeTurnState,
        *,
        error_kind: ErrorKind | None,
        error_message: str | None,
        correlation: CorrelationContext | None,
        actor: Actor,
        session_id: str,
        retry_available: bool = False,
    ) -> RuntimeTurn:
        async with self._concurrency.for_session(actor.id):
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
            _TERMINALS[state].advance(self._agent, actor)
        if not (retry_available and _TERMINALS[state].retryable):
            self._notify_channel_terminal(
                turn, state, error_kind, error_message, actor, session_id
            )
        await self._audit.append(
            event_name=f"runtime.turn.{state.value}",
            state=_TERMINALS[state].audit_state,
            correlation=correlation or CorrelationContext(turn_id=turn.turn_id),
            error_kind=error_kind,
            error_message=error_message,
        )
        return current_turn

    async def _apply_runtime_event(
        self,
        message: Message,
        context: SessionContext,
        turn: RuntimeTurn,
        envelope: RuntimeEventEnvelope,
        payload: TurnPayload,
    ) -> RuntimeTurn:
        if envelope.turn_id != turn.turn_id:
            raise ValueError("runtime event turn correlation mismatch")
        match payload:
            case TurnStarted(event_name=event_name, metadata=metadata):
                state = RuntimeEventState.STARTED
                error_kind = None
                error_message = None
            case TurnCompleted(event_name=event_name, metadata=metadata):
                state = RuntimeEventState.COMPLETED
                error_kind = None
                error_message = None
            case TurnFailed(
                event_name=event_name,
                error_kind=error_kind,
                error_message=error_message,
                metadata=metadata,
            ):
                state = RuntimeEventState.FAILED
            case TurnCancelled(event_name=event_name, metadata=metadata):
                state = RuntimeEventState.CANCELLED
                error_kind = None
                error_message = None
            case TurnUnknown(
                event_name=event_name,
                error_kind=error_kind,
                error_message=error_message,
                metadata=metadata,
            ):
                state = RuntimeEventState.UNKNOWN
        async with self._concurrency.for_session(message.session_id):
            advance = _runtime_event_advance(payload)
            target_state = (
                _EVENTS[state].turn_state if _is_turn_event(event_name) else turn.state
            )
            provider_turn_id = envelope.provider_turn_id
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
            if state is RuntimeEventState.UNKNOWN and error_kind is None:
                error_kind = ErrorKind.PROVIDER_UNKNOWN.value
            if state is RuntimeEventState.CANCELLED and error_kind is None:
                error_kind = ErrorKind.CANCELLED.value
            updated_turn = turn.transition_to(
                target_state,
                at_ms=envelope.occurred_at_ms,
                error_kind=error_kind,
                error_message=error_message,
                latest_event_name=event_name,
            )
            if provider_turn_id is not None:
                updated_turn = replace(
                    updated_turn,
                    provider_turn_id=provider_turn_id,
                )
            if _is_terminal_turn_event(payload):
                self._turns.pop(turn.turn_id, None)
            else:
                self._turns[turn.turn_id] = updated_turn
            advance(self._agent, context.actor)
        try:
            audit_kind = ErrorKind(error_kind) if error_kind else None
        except ValueError:
            audit_kind = ErrorKind.INTERNAL
        audit_error_message = error_message if audit_kind else None
        await self._audit.append(
            event_name=event_name,
            state=state,
            correlation=self.turn_correlation(message, context, updated_turn),
            error_kind=audit_kind,
            error_message=audit_error_message,
            metadata=metadata,
        )
        return updated_turn

    async def _close_stream(self, stream: IRuntimeTurnStream | None) -> None:
        if stream is not None:
            await stream.aclose()

    def turn_correlation(
        self,
        message: Message,
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
