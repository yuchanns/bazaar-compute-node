from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import time_ns

from ...core.approval import IApprovalHandler
from ...core.command import ICommandService
from ...core.models import (
    ApprovalRequest,
    RuntimeEvent,
    RuntimeEventState,
    RuntimeProcessState,
    RuntimeSession,
    RuntimeTurn,
    RuntimeTurnState,
)
from ...core.outcomes import ProviderCallResult, ProviderCallStatus
from ...core.runtime import IRuntime, IRuntimeTurnStream

CommandScript = Callable[[ICommandService, str], Awaitable[None]]
CommandRunner = Callable[[str], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class DummyTurnPlan:
    """A controlled runtime behavior used by one integration test turn."""

    states: tuple[RuntimeEventState, ...] = (
        RuntimeEventState.STARTED,
        RuntimeEventState.COMPLETED,
    )
    approval_request: ApprovalRequest | None = None
    command_script: CommandScript | None = None
    block_until_release: bool = False
    raise_error: str | None = None


class DummyRuntime(IRuntime):
    """Deterministic runtime that can execute a command script inside a turn."""

    def __init__(
        self,
        command_service: ICommandService | None = None,
        default_command_runner: CommandRunner | None = None,
    ) -> None:
        self.command_service = command_service
        self.default_command_runner = default_command_runner
        self.started = False
        self.stopped = False
        self.started_sessions: list[RuntimeSession] = []
        self.resumed_sessions: list[RuntimeSession] = []
        self.stopped_sessions: list[RuntimeSession] = []
        self.started_turns: list[tuple[RuntimeSession, RuntimeTurn, str]] = []
        self.approval_results = []
        self.active_streams: set[_DummyTurnStream] = set()
        self.closed_streams: list[_DummyTurnStream] = []
        self.turn_started = asyncio.Event()
        self._turn_plans: deque[DummyTurnPlan] = deque()
        self._start_results: deque[ProviderCallResult[RuntimeSession]] = deque()
        self._resume_results: deque[ProviderCallResult[RuntimeSession]] = deque()
        self._stop_results: deque[ProviderCallResult[RuntimeSession]] = deque()
        self._event_seq = 0

    async def start(self, *, timeout: float) -> None:
        self.started = True
        self.stopped = False

    async def stop(self, *, timeout: float) -> None:
        self.stopped = True
        streams = tuple(self.active_streams)
        for stream in streams:
            await stream.aclose()

    def queue_turn_plan(self, plan: DummyTurnPlan) -> None:
        self._turn_plans.append(plan)

    def queue_start_result(self, result: ProviderCallResult[RuntimeSession]) -> None:
        self._start_results.append(result)

    def queue_resume_result(self, result: ProviderCallResult[RuntimeSession]) -> None:
        self._resume_results.append(result)

    def queue_stop_result(self, result: ProviderCallResult[RuntimeSession]) -> None:
        self._stop_results.append(result)

    async def start_session(
        self, session: RuntimeSession, *, timeout: float
    ) -> ProviderCallResult[RuntimeSession]:
        self.started_sessions.append(session)
        if self._start_results:
            return self._start_results.popleft()
        running = session.transition_process_to(
            RuntimeProcessState.RUNNING,
            updated_at_ms=time_ns() // 1_000_000,
        )
        return ProviderCallResult(
            status=ProviderCallStatus.CONFIRMED,
            value=running,
        )

    async def resume_session(
        self, session: RuntimeSession, *, timeout: float
    ) -> ProviderCallResult[RuntimeSession]:
        self.resumed_sessions.append(session)
        if self._resume_results:
            return self._resume_results.popleft()
        if session.process_state is RuntimeProcessState.UNKNOWN:
            session = session.transition_process_to(
                RuntimeProcessState.RECONCILING,
                updated_at_ms=time_ns() // 1_000_000,
            )
        running = session.transition_process_to(
            RuntimeProcessState.RUNNING,
            updated_at_ms=time_ns() // 1_000_000,
        )
        return ProviderCallResult(
            status=ProviderCallStatus.CONFIRMED,
            value=running,
        )

    async def start_turn(
        self,
        session: RuntimeSession,
        turn: RuntimeTurn,
        input_text: str,
        approval_handler: IApprovalHandler,
        *,
        timeout: float,
    ) -> IRuntimeTurnStream:
        if not self.started or self.stopped:
            raise RuntimeError("dummy runtime is not started")
        plan = self._turn_plans.popleft() if self._turn_plans else DummyTurnPlan()
        stream = _DummyTurnStream(
            runtime=self,
            session=session,
            turn=turn,
            input_text=input_text,
            approval_handler=approval_handler,
            timeout=timeout,
            plan=plan,
        )
        self.started_turns.append((session, turn, input_text))
        self.active_streams.add(stream)
        self.turn_started.set()
        return stream

    async def interrupt_turn(
        self,
        session: RuntimeSession,
        turn: RuntimeTurn,
        *,
        timeout: float,
    ) -> ProviderCallResult[RuntimeTurn]:
        if turn.state in {RuntimeTurnState.STARTING, RuntimeTurnState.RUNNING}:
            turn = turn.transition_to(
                RuntimeTurnState.CANCELLED,
                at_ms=time_ns() // 1_000_000,
            )
        return ProviderCallResult(
            status=ProviderCallStatus.CONFIRMED,
            value=turn,
        )

    async def stop_session(
        self, session: RuntimeSession, *, timeout: float
    ) -> ProviderCallResult[RuntimeSession]:
        self.stopped_sessions.append(session)
        if self._stop_results:
            return self._stop_results.popleft()
        if session.process_state is not RuntimeProcessState.STOPPING:
            session = session.transition_process_to(
                RuntimeProcessState.STOPPING,
                updated_at_ms=time_ns() // 1_000_000,
            )
        stopped = session.transition_process_to(
            RuntimeProcessState.STOPPED,
            updated_at_ms=time_ns() // 1_000_000,
        )
        return ProviderCallResult(
            status=ProviderCallStatus.CONFIRMED,
            value=stopped,
        )

    def _next_event(
        self,
        session: RuntimeSession,
        turn: RuntimeTurn,
        state: RuntimeEventState,
    ) -> RuntimeEvent:
        self._event_seq += 1
        error_kind = None
        if state is RuntimeEventState.FAILED:
            error_kind = "provider_failed"
        elif state is RuntimeEventState.UNKNOWN:
            error_kind = "provider_unknown"
        return RuntimeEvent(
            event_seq=self._event_seq,
            event_id=f"dummy-event-{self._event_seq}",
            created_at_ms=time_ns() // 1_000_000,
            level="error" if error_kind else "info",
            event_name=f"runtime.turn.{state.value}",
            state=state,
            node_id="dummy-node",
            runtime_slug=session.runtime_slug,
            bcn_session_id=session.bcn_session_id,
            agent_runtime_session_id=session.agent_runtime_session_id,
            turn_id=turn.turn_id,
            error_kind=error_kind,
            error_message="dummy provider failure" if error_kind else None,
        )


class _DummyTurnStream(IRuntimeTurnStream):
    def __init__(
        self,
        *,
        runtime: DummyRuntime,
        session: RuntimeSession,
        turn: RuntimeTurn,
        input_text: str,
        approval_handler: IApprovalHandler,
        timeout: float,
        plan: DummyTurnPlan,
    ) -> None:
        self.runtime = runtime
        self.session = session
        self.turn = turn
        self.input_text = input_text
        self.approval_handler = approval_handler
        self.timeout = timeout
        self.plan = plan
        self.index = 0
        self.approval_done = False
        self.command_done = False
        self.error_raised = False
        self.closed = False
        self.released = asyncio.Event()

    def __aiter__(self) -> _DummyTurnStream:
        return self

    async def __anext__(self) -> RuntimeEvent:
        if self.closed:
            raise StopAsyncIteration
        if not self.approval_done and self.plan.approval_request is not None:
            self.approval_done = True
            result = await self.approval_handler.request_approval(
                self.plan.approval_request,
                timeout=self.timeout,
            )
            self.runtime.approval_results.append(result)
        if not self.command_done and self.plan.command_script is not None:
            self.command_done = True
            if self.runtime.command_service is None:
                raise RuntimeError("dummy runtime command service is not configured")
            await self.plan.command_script(
                self.runtime.command_service,
                self.session.bcn_session_id,
            )
        elif (
            not self.command_done
            and self.plan.command_script is None
            and self.runtime.default_command_runner is not None
        ):
            self.command_done = True
            await self.runtime.default_command_runner(self.session.bcn_session_id)
        if self.plan.raise_error is not None and not self.error_raised:
            self.error_raised = True
            raise RuntimeError(self.plan.raise_error)
        if self.index >= len(self.plan.states):
            if self.plan.block_until_release and not self.released.is_set():
                await self.released.wait()
                if self.closed:
                    raise StopAsyncIteration
            raise StopAsyncIteration
        state = self.plan.states[self.index]
        if (
            self.plan.block_until_release
            and state
            in {
                RuntimeEventState.COMPLETED,
                RuntimeEventState.FAILED,
                RuntimeEventState.UNKNOWN,
            }
            and not self.released.is_set()
        ):
            await self.released.wait()
            if self.closed:
                raise StopAsyncIteration
        self.index += 1
        return self.runtime._next_event(self.session, self.turn, state)

    async def aclose(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.released.set()
        self.runtime.active_streams.discard(self)
        self.runtime.closed_streams.append(self)

    def release(self) -> None:
        self.released.set()
