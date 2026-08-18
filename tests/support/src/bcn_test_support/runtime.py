from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from time import time_ns
from typing import ClassVar

from bazaar_compute_node.core.approval import IApprovalHandler
from bazaar_compute_node.core.command import ICommandService
from bazaar_compute_node.core.models import (
    ApprovalRequest,
    RuntimeEvent,
    RuntimeEventState,
    RuntimeSession,
    RuntimeTurn,
    RuntimeTurnState,
    SessionRuntimeState,
    StreamEvent,
    StreamEventKind,
)
from bazaar_compute_node.core.outcomes import ProviderCallResult, ProviderCallStatus
from bazaar_compute_node.core.runtime import (
    IRuntime,
    IRuntimeTurnStream,
    RuntimeExpire,
    RuntimeSessionReconciliation,
    RuntimeSessionUnavailable,
)

CommandScript = Callable[[ICommandService, str], Awaitable[None]]
CommandRunner = Callable[[str], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class TestTurnPlan:
    """A controlled runtime behavior used by one integration test turn."""

    __test__: ClassVar[bool] = False

    states: tuple[RuntimeEventState, ...] = (
        RuntimeEventState.STARTED,
        RuntimeEventState.COMPLETED,
    )
    approval_request: ApprovalRequest | None = None
    command_script: CommandScript | None = None
    block_until_release: bool = False
    raise_error: str | None = None
    pre_start_unavailable: bool = False
    update_count: int = 0
    stream_session_id: str | None = None


class TestRuntime(IRuntime):
    """Deterministic runtime that can execute a command script inside a turn."""

    __test__ = False

    @property
    def name(self) -> str:
        return "test"

    def environment_variable_names(self) -> tuple[str, ...]:
        return ("TEST_RUNTIME_HOME",)

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
        self.reconciled_sessions: list[RuntimeSession] = []
        self.stopped_sessions: list[RuntimeSession] = []
        self.started_turns: list[tuple[RuntimeSession, RuntimeTurn, str]] = []
        self.steered_turns: list[tuple[RuntimeSession, RuntimeTurn, str]] = []
        self.approval_results = []
        self.active_streams: set[_TestTurnStream] = set()
        self.closed_streams: list[_TestTurnStream] = []
        self.turn_started = asyncio.Event()
        self._turn_plans: deque[TestTurnPlan] = deque()
        self._start_results: deque[ProviderCallResult[RuntimeSession]] = deque()
        self._reconcile_results: deque[
            ProviderCallResult[RuntimeSessionReconciliation]
        ] = deque()
        self._reconcile_turn_plans: deque[TestTurnPlan] = deque()
        self._stop_results: deque[ProviderCallResult[RuntimeSession]] = deque()
        self._expire_events: asyncio.Queue[RuntimeExpire] = asyncio.Queue()
        self._update_seq = 0

    async def start(self, *, timeout: float) -> None:
        del timeout
        self.started = True
        self.stopped = False

    async def stop(self, *, timeout: float) -> None:
        del timeout
        self.stopped = True
        streams = tuple(self.active_streams)
        for stream in streams:
            await stream.aclose()

    def queue_turn_plan(self, plan: TestTurnPlan) -> None:
        self._turn_plans.append(plan)

    def queue_start_result(self, result: ProviderCallResult[RuntimeSession]) -> None:
        self._start_results.append(result)

    def queue_reconcile_result(
        self, result: ProviderCallResult[RuntimeSessionReconciliation]
    ) -> None:
        self._reconcile_results.append(result)

    def queue_reconcile_turn_plan(self, plan: TestTurnPlan) -> None:
        self._reconcile_turn_plans.append(plan)

    def queue_stop_result(self, result: ProviderCallResult[RuntimeSession]) -> None:
        self._stop_results.append(result)

    def emit_expire(self, runtime_session_id: str) -> None:
        self._expire_events.put_nowait(RuntimeExpire(runtime_session_id))

    async def receive_expire(self) -> RuntimeExpire:
        return await self._expire_events.get()

    async def start_session(
        self, session: RuntimeSession, *, timeout: float
    ) -> ProviderCallResult[RuntimeSession]:
        del timeout
        self.started_sessions.append(session)
        if self._start_results:
            return self._start_results.popleft()
        if session.provider_thread_id is None:
            session = replace(
                session,
                provider_thread_id=f"test-thread-{session.id}",
                updated_at_ms=time_ns() // 1_000_000,
            )
        return ProviderCallResult(
            status=ProviderCallStatus.CONFIRMED,
            value=session,
        )

    async def reconcile_session(
        self,
        session: RuntimeSession,
        turn: RuntimeTurn | None,
        approval_handler: IApprovalHandler | None,
        *,
        timeout: float,
    ) -> ProviderCallResult[RuntimeSessionReconciliation]:
        self.reconciled_sessions.append(session)
        if self._reconcile_results:
            return self._reconcile_results.popleft()
        if self._reconcile_turn_plans:
            if turn is None or approval_handler is None:
                raise ValueError("working reconciliation requires an active turn")
            stream = _TestTurnStream(
                runtime=self,
                session=session,
                turn=turn,
                input_text="",
                approval_handler=approval_handler,
                timeout=timeout,
                plan=self._reconcile_turn_plans.popleft(),
            )
            self.active_streams.add(stream)
            return ProviderCallResult(
                status=ProviderCallStatus.CONFIRMED,
                value=RuntimeSessionReconciliation(
                    session=session,
                    state=SessionRuntimeState.WORKING,
                    stream=stream,
                ),
            )
        return ProviderCallResult(
            status=ProviderCallStatus.CONFIRMED,
            value=RuntimeSessionReconciliation(
                session=session,
                state=SessionRuntimeState.IDLE,
            ),
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
            raise RuntimeError("test runtime is not started")
        plan = self._turn_plans.popleft() if self._turn_plans else TestTurnPlan()
        if plan.pre_start_unavailable:
            raise RuntimeSessionUnavailable("test runtime session is unavailable")
        stream = _TestTurnStream(
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
        del session, timeout
        if turn.state in {RuntimeTurnState.STARTING, RuntimeTurnState.RUNNING}:
            turn = turn.transition_to(
                RuntimeTurnState.CANCELLED,
                at_ms=time_ns() // 1_000_000,
            )
        return ProviderCallResult(
            status=ProviderCallStatus.CONFIRMED,
            value=turn,
        )

    async def steer_turn(
        self,
        session: RuntimeSession,
        turn: RuntimeTurn,
        input_text: str,
        *,
        timeout: float,
    ) -> bool:
        del timeout
        self.steered_turns.append((session, turn, input_text))
        return False

    async def stop_session(
        self, session: RuntimeSession, *, timeout: float
    ) -> ProviderCallResult[RuntimeSession]:
        del timeout
        self.stopped_sessions.append(session)
        if self._stop_results:
            return self._stop_results.popleft()
        return ProviderCallResult(
            status=ProviderCallStatus.CONFIRMED,
            value=session,
        )

    def _next_event(
        self,
        turn: RuntimeTurn,
        state: RuntimeEventState,
    ) -> RuntimeEvent:
        error_kind = None
        if state is RuntimeEventState.FAILED:
            error_kind = "provider_failed"
        elif state is RuntimeEventState.UNKNOWN:
            error_kind = "provider_unknown"
        return RuntimeEvent(
            created_at_ms=time_ns() // 1_000_000,
            event_name=f"runtime.turn.{state.value}",
            state=state,
            turn_id=turn.turn_id,
            error_kind=error_kind,
            error_message="test provider failure" if error_kind else None,
            metadata=(
                {"provider_turn_id": f"test-provider-{turn.turn_id}"}
                if state is RuntimeEventState.STARTED
                else {}
            ),
        )

    def _next_update(
        self,
        session: RuntimeSession,
        turn: RuntimeTurn,
        session_id: str | None,
    ) -> StreamEvent:
        self._update_seq += 1
        return StreamEvent(
            kind=StreamEventKind.REASONING_SUMMARY_DELTA,
            created_at_ms=time_ns() // 1_000_000,
            session_id=session_id or session.bcn_session_id,
            stream_id=f"reasoning-{turn.turn_id}",
            content=f"delta-{self._update_seq}",
        )


class _TestTurnStream(IRuntimeTurnStream):
    def __init__(
        self,
        *,
        runtime: TestRuntime,
        session: RuntimeSession,
        turn: RuntimeTurn,
        input_text: str,
        approval_handler: IApprovalHandler,
        timeout: float,
        plan: TestTurnPlan,
    ) -> None:
        self.runtime = runtime
        self.session = session
        self.turn = turn
        self.input_text = input_text
        self.approval_handler = approval_handler
        self.timeout = timeout
        self.plan = plan
        self.index = 0
        self.update_index = 0
        self.approval_done = False
        self.command_done = False
        self.error_raised = False
        self.closed = False
        self.released = asyncio.Event()

    def __aiter__(self) -> _TestTurnStream:
        return self

    async def __anext__(self) -> RuntimeEvent | StreamEvent:
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
                raise RuntimeError("test runtime command service is not configured")
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
        if self.index == 1 and self.update_index < self.plan.update_count:
            self.update_index += 1
            return self.runtime._next_update(
                self.session,
                self.turn,
                self.plan.stream_session_id,
            )
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
        return self.runtime._next_event(self.turn, state)

    async def aclose(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.released.set()
        self.runtime.active_streams.discard(self)
        self.runtime.closed_streams.append(self)

    def release(self) -> None:
        self.released.set()
