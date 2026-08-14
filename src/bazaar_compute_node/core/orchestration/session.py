from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from pathlib import Path
from time import time_ns
from uuid import uuid7

from ..approval import IApprovalHandler
from ..audit import ErrorKind
from ..channel import IChannel
from ..concurrency import ISessionConcurrency, SessionLockRegistry
from ..correlation import CorrelationContext
from ..lifecycle import IAsyncLifecycle, TimeoutBudget
from ..models import (
    AgentSignal,
    AgentState,
    AgentTick,
    AgentTickSource,
    BcnSession,
    ChannelSession,
    ChannelTargetKind,
    ConsumerCursor,
    InboundMessage,
    RuntimeAttempt,
    RuntimeEventState,
    RuntimeSession,
    RuntimeTurn,
    RuntimeTurnState,
)
from ..observability import IAudit
from ..outcomes import ProviderCallStatus
from ..runtime import (
    IRuntime,
    IRuntimeTurnStream,
    RuntimeExpire,
    RuntimeSessionReconciliation,
    RuntimeSessionUnavailable,
)
from ..storage import IStorage, NodeIdentity
from ..timerwheel import (
    Timer,
    TimerCancelledError,
    TimerWheel,
    TimerWheelClosedError,
)
from .command import SessionCommandService
from .services import SessionAuditRecorder, SessionStateWriter
from .turn import SessionContext, SessionTurnCoordinator


def _current_time_ms() -> int:
    return time_ns() // 1_000_000


@dataclass(slots=True)
class _IngressItem:
    message: InboundMessage
    completion: asyncio.Future[RuntimeTurn | None]
    activity_at_ms: int


@dataclass(slots=True)
class _DurableSessionContext:
    channel_session: ChannelSession
    bcn_session: BcnSession


@dataclass(slots=True)
class _RuntimeNotification:
    message: InboundMessage
    context: _DurableSessionContext
    completion: asyncio.Future[RuntimeTurn | None]
    activity_at_ms: int


@dataclass(slots=True)
class _RuntimeActivity:
    context: _DurableSessionContext
    completion: asyncio.Future[RuntimeTurn | None]
    activity_at_ms: int


@dataclass(frozen=True, slots=True)
class _RuntimeExpiry:
    bcn_session_id: str
    runtime_session_id: str
    timer_id: int
    generation: int


type _RuntimeQueueItem = (
    _RuntimeNotification | _RuntimeActivity | _RuntimeExpiry | RuntimeExpire
)


@dataclass(slots=True)
class _RuntimeTimerBinding:
    runtime_session_id: str
    timer: Timer
    watcher: asyncio.Task[None]
    expired: bool = False


class SessionOrchestrator(IAsyncLifecycle):
    """Route one Channel composition through provider-neutral core contracts."""

    def __init__(
        self,
        *,
        node_id: str | None = None,
        workspace_id: str | None = None,
        channel: IChannel,
        runtime: IRuntime,
        storage: IStorage,
        audit: IAudit,
        timeout_budget: TimeoutBudget,
        timer_wheel: TimerWheel,
        runtime_idle_timeout_ms: int = 0,
        workspace: Callable[[], Path],
        concurrency: ISessionConcurrency | None = None,
        clock: Callable[[], int] | None = None,
        on_node_initialized: Callable[[NodeIdentity], Awaitable[None]] | None = None,
    ) -> None:
        for value, field_name in (
            (node_id, "node_id"),
            (runtime.name, "runtime.name"),
        ):
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError(f"{field_name} must be a non-empty string")
        if workspace_id is not None and (
            not isinstance(workspace_id, str) or not workspace_id
        ):
            raise ValueError("workspace_id must be a non-empty string")
        if (
            isinstance(runtime_idle_timeout_ms, bool)
            or not isinstance(runtime_idle_timeout_ms, int)
            or runtime_idle_timeout_ms < 0
        ):
            raise ValueError("runtime_idle_timeout_ms must be a non-negative integer")
        self._node_id = node_id
        self._workspace_id = workspace_id
        self._on_node_initialized = on_node_initialized
        self._channel = channel
        self._runtime = runtime
        self._storage = storage
        self._timeout_budget = timeout_budget
        self._timer_wheel = timer_wheel
        self._runtime_idle_timeout_ms = runtime_idle_timeout_ms
        self._concurrency = concurrency or SessionLockRegistry()
        self._clock = clock or _current_time_ms
        self._runtime_sessions: dict[str, RuntimeSession] = {}
        self._runtime_turns: dict[str, RuntimeTurn] = {}
        self._agent_states: dict[str, AgentState] = {}
        self._logger = logging.getLogger("bazaar_compute_node.orchestration.session")
        if not self._logger.handlers:
            self._logger.addHandler(logging.StreamHandler())
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False
        self._audit = SessionAuditRecorder(
            sink=audit,
            timeout_budget=timeout_budget,
            clock=self._clock,
        )
        self._state_writer = SessionStateWriter(
            storage=storage,
            concurrency=self._concurrency,
            states=self._agent_states,
        )
        self._command_service = SessionCommandService(
            channel=channel,
            storage=storage,
            audit=self._audit,
            provider_call_timeout=timeout_budget.provider_call_seconds,
            concurrency=self._concurrency,
            node_id=lambda: self.node_id,
            workspace=workspace,
            clock=self._clock,
        )
        self._turns = SessionTurnCoordinator(
            channel=channel,
            runtime=runtime,
            storage=storage,
            audit=self._audit,
            state_writer=self._state_writer,
            timeout_budget=timeout_budget,
            concurrency=self._concurrency,
            turns=self._runtime_turns,
            node_id=lambda: self.node_id,
            clock=self._clock,
        )
        self._active_tasks: set[asyncio.Task[RuntimeTurn | None]] = set()
        self._ingress_queues: dict[tuple[str, str], asyncio.Queue[_IngressItem]] = {}
        self._ingress_workers: dict[tuple[str, str], asyncio.Task[None]] = {}
        self._runtime_queues: dict[str, asyncio.Queue[_RuntimeQueueItem]] = {}
        self._runtime_workers: dict[str, asyncio.Task[None]] = {}
        self._runtime_timers: dict[str, _RuntimeTimerBinding] = {}
        self._expired_runtime_ids: set[str] = set()
        self._receive_task: asyncio.Task[None] | None = None
        self._runtime_expire_task: asyncio.Task[None] | None = None
        self._started = False
        self._stopping = False
        self._shutdown_errors: list[str] = []

    @property
    def node_id(self) -> str:
        if self._node_id is None:
            raise RuntimeError("node identity has not been initialized")
        return self._node_id

    @property
    def workspace_id(self) -> str:
        if self._workspace_id is None:
            raise RuntimeError("node identity has not been initialized")
        return self._workspace_id

    @property
    def command_service(self) -> SessionCommandService:
        return self._command_service

    def agent_state(self, session_id: str) -> AgentState | None:
        """Return the process-local lifecycle state for one active Agent."""

        return self._agent_states.get(session_id)

    def runtime_session(self, session_id: str) -> RuntimeSession | None:
        """Return the process-local runtime session bound to one bcn session."""

        return self._runtime_sessions.get(session_id)

    def _create_runtime_session(
        self, context: _DurableSessionContext
    ) -> SessionContext:
        now_ms = self._clock()
        runtime_session = RuntimeSession(
            id=str(uuid7()),
            bcn_session_id=context.bcn_session.id,
            channel_session_id=context.channel_session.id,
            runtime=self._runtime.name,
            workspace_id=self.workspace_id,
            created_at_ms=now_ms,
            updated_at_ms=now_ms,
        )
        self._runtime_sessions[context.bcn_session.id] = runtime_session
        return SessionContext(
            context.channel_session,
            context.bcn_session,
            runtime_session,
        )

    async def _discard_runtime_session(self, runtime_session: RuntimeSession) -> None:
        self._expired_runtime_ids.discard(runtime_session.id)
        if self.runtime_session(runtime_session.bcn_session_id) is runtime_session:
            await self._cancel_runtime_timer(runtime_session)
            self._runtime_sessions.pop(runtime_session.bcn_session_id, None)
            self._agent_states.pop(runtime_session.bcn_session_id, None)

    async def _cancel_runtime_timer(self, runtime_session: RuntimeSession) -> None:
        binding = self._runtime_timers.get(runtime_session.bcn_session_id)
        if binding is None or binding.runtime_session_id != runtime_session.id:
            return
        self._runtime_timers.pop(runtime_session.bcn_session_id, None)
        binding.timer.cancel()
        if not binding.watcher.done():
            binding.watcher.cancel()
        await asyncio.gather(binding.watcher, return_exceptions=True)

    async def _refresh_runtime_timer(
        self,
        runtime_session: RuntimeSession,
        activity_at_ms: int,
        *,
        full_timeout: bool = False,
    ) -> None:
        if self._runtime_idle_timeout_ms <= 0:
            return
        binding = self._runtime_timers.get(runtime_session.bcn_session_id)
        delay_ms = self._runtime_idle_timeout_ms
        if not full_timeout:
            delay_ms = max(
                0,
                delay_ms - (self._timer_wheel.now_ms - activity_at_ms),
            )
        if (
            binding is not None
            and binding.runtime_session_id == runtime_session.id
            and binding.timer.active
        ):
            try:
                binding.timer.reset(delay_ms)
            except RuntimeError:
                if binding.timer.active:
                    raise
            else:
                binding.expired = False
                return
        if binding is not None:
            binding.timer.cancel()
            if not binding.watcher.done():
                binding.watcher.cancel()
            await asyncio.gather(binding.watcher, return_exceptions=True)
        timer = self._timer_wheel.create(delay_ms)
        queue = self._runtime_queues[runtime_session.bcn_session_id]
        watcher = asyncio.create_task(
            self._forward_runtime_session_expiry(
                runtime_session,
                timer,
                queue,
            ),
            name=f"bcn-runtime-expiry-{runtime_session.bcn_session_id}",
        )
        self._runtime_timers[runtime_session.bcn_session_id] = _RuntimeTimerBinding(
            runtime_session.id, timer, watcher
        )

    async def _forward_runtime_session_expiry(
        self,
        runtime_session: RuntimeSession,
        timer: Timer,
        queue: asyncio.Queue[_RuntimeQueueItem],
    ) -> None:
        try:
            await timer.wait()
        except TimerCancelledError, TimerWheelClosedError, asyncio.CancelledError:
            return
        generation = timer.expired_generation
        if generation is None:
            return
        queue.put_nowait(
            _RuntimeExpiry(
                bcn_session_id=runtime_session.bcn_session_id,
                runtime_session_id=runtime_session.id,
                timer_id=timer.id,
                generation=generation,
            )
        )

    async def _ensure_runtime_session_or_discard(
        self,
        context: SessionContext,
        *,
        turn: RuntimeTurn | None = None,
        approval_handler: IApprovalHandler | None = None,
    ) -> tuple[SessionContext, IRuntimeTurnStream | None]:
        try:
            return await self._ensure_runtime_session(
                context,
                turn=turn,
                approval_handler=approval_handler,
            )
        except BaseException:
            try:
                await self._runtime.stop_session(
                    context.runtime_session,
                    timeout=self._timeout_budget.provider_call_seconds,
                )
            finally:
                await self._discard_runtime_session(context.runtime_session)
            raise

    async def start(self, *, timeout: float) -> None:
        if self._started:
            return
        if self._stopping:
            raise RuntimeError("session orchestrator is stopping")
        await self._storage.start(timeout=timeout)
        try:
            identity = await self._storage.initialize(
                node_id=self._node_id,
                workspace_id=self._workspace_id,
            )
            self._node_id = identity.node_id
            self._workspace_id = identity.workspace_id
            if self._on_node_initialized is not None:
                await self._on_node_initialized(identity)
            await self._runtime.start(timeout=timeout)
            await self._channel.start(timeout=timeout)
        except BaseException:
            await self._runtime.stop(timeout=timeout)
            await self._storage.stop(timeout=timeout)
            raise
        self._started = True
        self._receive_task = asyncio.create_task(
            self._receive_loop(), name="bcn-channel-receive"
        )
        self._runtime_expire_task = asyncio.create_task(
            self._receive_runtime_expire_loop(),
            name="bcn-runtime-expire-events",
        )

    async def stop(self, *, timeout: float) -> None:
        if self._stopping:
            return
        self._stopping = True

        try:
            await self._channel.stop(timeout=timeout)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            self._shutdown_errors.append(f"channel.stop: {type(error).__name__}")

        receive_task = self._receive_task
        if receive_task is not None and not receive_task.done():
            receive_task.cancel()
            try:
                await asyncio.wait_for(receive_task, timeout=timeout)
            except TimeoutError, asyncio.CancelledError:
                self._shutdown_errors.append("channel.receive: shutdown timeout")
        self._receive_task = None

        runtime_expire_task = self._runtime_expire_task
        if runtime_expire_task is not None and not runtime_expire_task.done():
            runtime_expire_task.cancel()
            try:
                await asyncio.wait_for(runtime_expire_task, timeout=timeout)
            except TimeoutError, asyncio.CancelledError:
                self._shutdown_errors.append("runtime.expire: shutdown timeout")
        self._runtime_expire_task = None

        active_tasks = tuple(self._active_tasks)
        for task in active_tasks:
            task.cancel()
        if active_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*active_tasks, return_exceptions=True),
                    timeout=timeout,
                )
            except TimeoutError:
                self._shutdown_errors.append("inbound tasks: shutdown timeout")

        workers = (*self._ingress_workers.values(), *self._runtime_workers.values())
        for worker in workers:
            worker.cancel()
        if workers:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*workers, return_exceptions=True),
                    timeout=timeout,
                )
            except TimeoutError:
                self._shutdown_errors.append("session workers: shutdown timeout")
        self._ingress_queues.clear()
        self._ingress_workers.clear()
        self._runtime_queues.clear()
        self._runtime_workers.clear()

        for runtime_session in tuple(self._runtime_sessions.values()):
            await self._stop_runtime_session(runtime_session, timeout=timeout)

        try:
            await self._runtime.stop(timeout=timeout)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            self._shutdown_errors.append(f"runtime.stop: {type(error).__name__}")
        try:
            await self._storage.stop(timeout=timeout)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            self._shutdown_errors.append(f"storage.stop: {type(error).__name__}")
        self._started = False
        self._agent_states.clear()
        self._runtime_sessions.clear()
        self._runtime_turns.clear()
        self._expired_runtime_ids.clear()

    def dispatch_inbound(
        self, message: InboundMessage
    ) -> asyncio.Task[RuntimeTurn | None]:
        """Schedule one inbound message while retaining its task for shutdown."""

        if self._stopping:
            raise RuntimeError("session orchestrator is stopping")
        task = asyncio.create_task(
            self.handle_inbound(message),
            name=f"bcn-inbound-{message.message_id}",
        )
        self._active_tasks.add(task)
        task.add_done_callback(self._forget_task)
        return task

    async def tick(self, session_id: str, tick: AgentTick) -> AgentState:
        """Apply one serialized lifecycle observation to a bcn session."""

        if not session_id:
            raise ValueError("session_id must be a non-empty string")
        return await self._state_writer.apply(session_id, tick)

    async def handle_inbound(self, message: InboundMessage) -> RuntimeTurn | None:
        """Queue one inbound message without waiting for Runtime I/O at ingress."""

        loop = asyncio.get_running_loop()
        completion: asyncio.Future[RuntimeTurn | None] = loop.create_future()
        conversation_key = (message.channel, message.provider_thread_id)
        ingress_queue = self._ingress_queues.get(conversation_key)
        if ingress_queue is None:
            ingress_queue = asyncio.Queue()
            self._ingress_queues[conversation_key] = ingress_queue
            self._ingress_workers[conversation_key] = asyncio.create_task(
                self._ingress_loop(ingress_queue),
                name=f"bcn-ingress-{message.channel_session_id}",
            )
        ingress_queue.put_nowait(
            _IngressItem(message, completion, self._timer_wheel.now_ms)
        )
        return await completion

    async def _ingress_loop(
        self,
        queue: asyncio.Queue[_IngressItem],
    ) -> None:
        while True:
            item = await queue.get()
            try:
                context, message, created = await self._record_inbound(item.message)
                if not created:
                    if not item.completion.done():
                        item.completion.set_result(None)
                    continue
                if context is None:
                    raise RuntimeError("new inbound has no durable session context")
                session_id = context.bcn_session.id
                runtime_queue = self._runtime_queues.get(session_id)
                if runtime_queue is None and (
                    message.notifies_runtime
                    or self.runtime_session(session_id) is not None
                ):
                    runtime_queue = asyncio.Queue()
                    self._runtime_queues[session_id] = runtime_queue
                    self._runtime_workers[session_id] = asyncio.create_task(
                        self._runtime_loop(session_id, runtime_queue),
                        name=f"bcn-runtime-{session_id}",
                    )
                if runtime_queue is None:
                    if not item.completion.done():
                        item.completion.set_result(None)
                elif message.notifies_runtime:
                    runtime_queue.put_nowait(
                        _RuntimeNotification(
                            message,
                            context,
                            item.completion,
                            item.activity_at_ms,
                        )
                    )
                else:
                    runtime_queue.put_nowait(
                        _RuntimeActivity(
                            context,
                            item.completion,
                            item.activity_at_ms,
                        )
                    )
            except asyncio.CancelledError:
                if not item.completion.done():
                    item.completion.cancel()
                raise
            except Exception as error:  # noqa: BLE001
                if not item.completion.done():
                    item.completion.set_exception(error)
            finally:
                queue.task_done()

    async def _runtime_loop(
        self,
        session_id: str,
        queue: asyncio.Queue[_RuntimeQueueItem],
    ) -> None:
        pending: list[_RuntimeQueueItem] = []
        while True:
            if pending:
                item = pending.pop(0)
            else:
                item = await queue.get()
            if isinstance(item, _RuntimeActivity):
                try:
                    runtime_session = self.runtime_session(item.context.bcn_session.id)
                    if runtime_session is not None:
                        await self._refresh_runtime_timer(
                            runtime_session,
                            item.activity_at_ms,
                        )
                    if not item.completion.done():
                        item.completion.set_result(None)
                except Exception as error:  # noqa: BLE001
                    if not item.completion.done():
                        item.completion.set_exception(error)
                finally:
                    queue.task_done()
                continue
            if isinstance(item, _RuntimeExpiry):
                try:
                    await self._handle_runtime_expiry(
                        item,
                        queue,
                        queue_quiescent=not pending and queue.empty(),
                    )
                finally:
                    queue.task_done()
                continue
            if isinstance(item, RuntimeExpire):
                try:
                    await self._handle_runtime_context_expire(
                        session_id,
                        item,
                        queue,
                    )
                finally:
                    queue.task_done()
                continue

            batch = [item]
            while True:
                if pending:
                    candidate = pending.pop(0)
                else:
                    try:
                        candidate = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                if not isinstance(candidate, _RuntimeNotification):
                    pending.insert(0, candidate)
                    break
                batch.append(candidate)
            for notification in batch:
                runtime_session = self.runtime_session(
                    notification.context.bcn_session.id
                )
                if runtime_session is not None:
                    await self._refresh_runtime_timer(
                        runtime_session,
                        notification.activity_at_ms,
                    )
            turn_task = asyncio.create_task(
                self._run_notification(batch[0]),
                name=f"bcn-turn-{batch[0].context.bcn_session.id}",
            )
            queue_task = asyncio.create_task(queue.get())
            queue_item_consumed = False
            try:
                while True:
                    done, _ = await asyncio.wait(
                        (turn_task, queue_task),
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if queue_task in done:
                        queued_item = queue_task.result()
                        queue_item_consumed = True
                        if isinstance(queued_item, _RuntimeNotification):
                            runtime_session = self.runtime_session(
                                queued_item.context.bcn_session.id
                            )
                            if runtime_session is not None:
                                await self._refresh_runtime_timer(
                                    runtime_session,
                                    queued_item.activity_at_ms,
                                )
                            pending.append(queued_item)
                            await self._steer_active_turn(queued_item)
                        elif isinstance(queued_item, _RuntimeActivity):
                            runtime_session = self.runtime_session(
                                queued_item.context.bcn_session.id
                            )
                            if runtime_session is not None:
                                await self._refresh_runtime_timer(
                                    runtime_session,
                                    queued_item.activity_at_ms,
                                )
                            if not queued_item.completion.done():
                                queued_item.completion.set_result(None)
                            queue.task_done()
                        elif isinstance(queued_item, _RuntimeExpiry):
                            await self._handle_runtime_expiry(
                                queued_item,
                                queue,
                                queue_quiescent=False,
                            )
                            queue.task_done()
                        else:
                            await self._handle_runtime_context_expire(
                                session_id,
                                queued_item,
                                queue,
                            )
                            queue.task_done()
                    if turn_task in done:
                        break
                    queue_task = asyncio.create_task(queue.get())
                    queue_item_consumed = False

                result = turn_task.result()
                for notification in batch:
                    if not notification.completion.done():
                        notification.completion.set_result(result)
                await self._stop_expired_runtime_if_idle(
                    batch[0].context.bcn_session.id,
                    queue,
                    queue_quiescent=not pending and queue.empty(),
                )
            except asyncio.CancelledError:
                turn_task.cancel()
                queue_task.cancel()
                await asyncio.gather(
                    turn_task,
                    queue_task,
                    return_exceptions=True,
                )
                if (
                    not queue_item_consumed
                    and not queue_task.cancelled()
                    and queue_task.exception() is None
                ):
                    pending.append(queue_task.result())
                    queue_item_consumed = True
                for queued_item in (*batch, *pending):
                    if (
                        isinstance(queued_item, _RuntimeNotification | _RuntimeActivity)
                        and not queued_item.completion.done()
                    ):
                        queued_item.completion.cancel()
                for _ in range(len(pending)):
                    queue.task_done()
                pending.clear()
                raise
            except Exception as error:  # noqa: BLE001
                turn_task.cancel()
                await asyncio.gather(turn_task, return_exceptions=True)
                for notification in batch:
                    if not notification.completion.done():
                        notification.completion.set_exception(error)
            finally:
                if not queue_task.done():
                    queue_task.cancel()
                try:
                    queued_item = await queue_task
                except asyncio.CancelledError:
                    pass
                else:
                    if not queue_item_consumed:
                        pending.append(queued_item)
                for _ in range(len(batch)):
                    queue.task_done()

    async def _steer_active_turn(
        self,
        notification: _RuntimeNotification,
    ) -> None:
        session_id = notification.context.bcn_session.id
        runtime_session = self.runtime_session(session_id)
        if runtime_session is None:
            return
        async with self._storage.transaction() as transaction:
            cursor = await transaction.get_consumer_cursor(session_id)
            delivered_through_seq = (
                cursor.delivered_through_seq if cursor is not None else 0
            )
            unread = await transaction.list_inbound_messages(
                session_id,
                after_seq=delivered_through_seq,
                notifying_only=True,
            )
        active_turn = next(
            (
                turn
                for turn in self._runtime_turns.values()
                if turn.session_id == runtime_session.id
                and turn.state is RuntimeTurnState.RUNNING
                and turn.provider_turn_id is not None
            ),
            None,
        )
        if not unread or active_turn is None:
            return
        await self._turns.steer_turn(
            notification.message,
            SessionContext(
                notification.context.channel_session,
                notification.context.bcn_session,
                runtime_session,
            ),
            active_turn,
            unread_count=len(unread),
        )

    async def _handle_runtime_expiry(
        self,
        expiry: _RuntimeExpiry,
        queue: asyncio.Queue[_RuntimeQueueItem],
        *,
        queue_quiescent: bool,
    ) -> None:
        binding = self._runtime_timers.get(expiry.bcn_session_id)
        if (
            binding is None
            or binding.runtime_session_id != expiry.runtime_session_id
            or binding.timer.id != expiry.timer_id
            or binding.timer.expired_generation != expiry.generation
        ):
            return
        binding.expired = True
        await self._stop_expired_runtime_if_idle(
            expiry.bcn_session_id,
            queue,
            queue_quiescent=queue_quiescent,
        )

    async def _handle_runtime_context_expire(
        self,
        session_id: str,
        expire: RuntimeExpire,
        queue: asyncio.Queue[_RuntimeQueueItem],
    ) -> None:
        runtime_session = self.runtime_session(session_id)
        if runtime_session is None or runtime_session.id != expire.runtime_session_id:
            return
        await self._stop_expired_runtime_if_idle(
            session_id,
            queue,
            queue_quiescent=True,
        )

    async def _stop_expired_runtime_if_idle(
        self,
        session_id: str,
        queue: asyncio.Queue[_RuntimeQueueItem],
        *,
        queue_quiescent: bool,
    ) -> None:
        binding = self._runtime_timers.get(session_id)
        runtime_session = self.runtime_session(session_id)
        context_expired = (
            runtime_session is not None
            and runtime_session.id in self._expired_runtime_ids
        )
        timer_expired = (
            binding is not None
            and binding.expired
            and runtime_session is not None
            and runtime_session.id == binding.runtime_session_id
        )
        if (
            runtime_session is None
            or not (context_expired or timer_expired)
            or (not context_expired and not queue_quiescent)
            or self._state_writer.get(session_id) is not AgentState.IDLE
        ):
            return
        async with self._concurrency.for_session(session_id):
            binding = self._runtime_timers.get(session_id)
            runtime_session = self.runtime_session(session_id)
            context_expired = (
                runtime_session is not None
                and runtime_session.id in self._expired_runtime_ids
            )
            timer_expired = (
                binding is not None
                and binding.expired
                and runtime_session is not None
                and runtime_session.id == binding.runtime_session_id
            )
            if (
                runtime_session is None
                or not (context_expired or timer_expired)
                or (not context_expired and not queue.empty())
                or self._state_writer.get(session_id) is not AgentState.IDLE
            ):
                return
            await self._stop_runtime_session_locked(
                runtime_session,
                timeout=self._timeout_budget.provider_call_seconds,
            )

    async def _receive_loop(self) -> None:
        async for message in self._channel.receive():
            if self._stopping:
                break
            self.dispatch_inbound(message)

    async def _receive_runtime_expire_loop(self) -> None:
        while True:
            expire = await self._runtime.receive_expire()
            if self._stopping:
                return
            source = next(
                (
                    runtime_session
                    for runtime_session in self._runtime_sessions.values()
                    if runtime_session.id == expire.runtime_session_id
                ),
                None,
            )
            if source is None or source.id in self._expired_runtime_ids:
                continue
            targets = tuple(self._runtime_sessions.values())
            self._expired_runtime_ids.update(
                runtime_session.id for runtime_session in targets
            )
            for runtime_session in targets:
                self._runtime_queues[runtime_session.bcn_session_id].put_nowait(
                    RuntimeExpire(runtime_session.id)
                )

    def _forget_task(self, task: asyncio.Task[RuntimeTurn | None]) -> None:
        self._active_tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is None:
            return
        self._logger.error(
            "%s",
            json.dumps(
                {
                    "event_name": "channel.inbound.failed",
                    "created_at_ms": self._clock(),
                    "metadata": {
                        "task_name": task.get_name(),
                        "error_type": type(error).__name__,
                        "error_message": str(error),
                    },
                },
                separators=(",", ":"),
                sort_keys=True,
            ),
            exc_info=(type(error), error, error.__traceback__),
        )

    async def _record_inbound(
        self, message: InboundMessage
    ) -> tuple[_DurableSessionContext | None, InboundMessage, bool]:
        context: _DurableSessionContext | None = None
        channel_session_created = False
        bcn_session_created = False
        async with self._storage.transaction() as transaction:
            existing_message = await transaction.find_inbound_message(
                message.channel,
                message.provider_thread_id,
                message.provider_message_id,
            )
            if existing_message is not None:
                message = existing_message
            channel_session = await transaction.find_channel_session(
                channel=message.channel,
                provider_thread_id=message.provider_thread_id,
            )
            now_ms = self._clock()
            if channel_session is None:
                channel_session_created = True
                channel_session = ChannelSession(
                    id=message.channel_session_id,
                    channel=message.channel,
                    provider_thread_id=message.provider_thread_id,
                    created_at_ms=now_ms,
                    updated_at_ms=now_ms,
                    target_kind=message.target_kind,
                    following=message.target_kind is ChannelTargetKind.DM
                    or message.mentions_agent,
                )
                await transaction.save_channel_session(channel_session)
            elif (
                existing_message is None
                and message.mentions_agent
                and not channel_session.following
            ):
                channel_session = replace(
                    channel_session,
                    following=True,
                    updated_at_ms=now_ms,
                )
                await transaction.save_channel_session(channel_session)

            bcn_session = await transaction.find_bcn_session(channel_session.id)
            if bcn_session is None:
                bcn_session_created = True
                bcn_session = BcnSession(
                    id=message.session_id,
                    channel_session_id=channel_session.id,
                    workspace_id=self.workspace_id,
                    created_at_ms=now_ms,
                    updated_at_ms=now_ms,
                )
                await transaction.save_bcn_session(bcn_session)

            if existing_message is None:
                notifies_runtime = message.notifies_runtime and (
                    message.target_kind is ChannelTargetKind.DM
                    or channel_session.following
                    or message.mentions_agent
                )
                canonical_target = message.canonical_target
                if channel_session.id != message.channel_session_id:
                    canonical_target = (
                        f"{channel_session.target_kind.value}:{channel_session.id}"
                    )
                message = replace(
                    message,
                    session_id=bcn_session.id,
                    channel_session_id=channel_session.id,
                    canonical_target=canonical_target,
                    notifies_runtime=notifies_runtime,
                )

            if message.notifies_runtime:
                cursor = await transaction.get_consumer_cursor(bcn_session.id)
                if cursor is None:
                    await transaction.save_consumer_cursor(
                        ConsumerCursor(session_id=bcn_session.id)
                    )

            if existing_message is None:
                message = await transaction.append_inbound_message(message)
                channel_session = replace(
                    channel_session,
                    last_inbound_at_ms=message.received_at_ms,
                    updated_at_ms=now_ms,
                )
                bcn_session = replace(
                    bcn_session,
                    last_activity_at_ms=message.received_at_ms,
                    updated_at_ms=now_ms,
                )
                await transaction.save_channel_session(channel_session)
                await transaction.save_bcn_session(bcn_session)

            context = _DurableSessionContext(channel_session, bcn_session)

        if existing_message is None:
            await self._audit.append(
                event_name="channel.inbound.persisted",
                state=RuntimeEventState.COMPLETED,
                correlation=CorrelationContext(
                    node_id=self.node_id,
                    channel=message.channel,
                    channel_session_id=message.channel_session_id,
                    bcn_session_id=message.session_id,
                    provider_thread_id=message.provider_thread_id,
                    inbound_seq=message.seq,
                ),
                metadata={
                    "notifies_runtime": message.notifies_runtime,
                    "channel_session_mapping": (
                        "created" if channel_session_created else "reused"
                    ),
                    "bcn_session_mapping": (
                        "created" if bcn_session_created else "reused"
                    ),
                },
            )
        return context, message, existing_message is None

    async def _run_notification(
        self, notification: _RuntimeNotification
    ) -> RuntimeTurn | None:
        message = notification.message
        durable_context = notification.context
        async with self._storage.transaction() as transaction:
            cursor = await transaction.get_consumer_cursor(
                durable_context.bcn_session.id
            )
            delivered_through_seq = (
                cursor.delivered_through_seq if cursor is not None else 0
            )
            unread = await transaction.list_inbound_messages(
                durable_context.bcn_session.id,
                after_seq=delivered_through_seq,
                notifying_only=True,
            )
            if not unread:
                return None
            turn_id = f"turn-{message.message_id}"
            if await transaction.get_runtime_attempt(turn_id) is not None:
                return self._runtime_turns.get(turn_id)

        runtime_session = self.runtime_session(durable_context.bcn_session.id)
        context = (
            SessionContext(
                durable_context.channel_session,
                durable_context.bcn_session,
                runtime_session,
            )
            if runtime_session is not None
            else self._create_runtime_session(durable_context)
        )
        for establishment_attempt in range(2):
            context, recovered_stream = await self._ensure_runtime_session_or_discard(
                context
            )
            if recovered_stream is not None:
                await recovered_stream.aclose()
                raise RuntimeError(
                    "runtime establishment unexpectedly recovered an active turn"
                )
            if self._state_writer.get(context.bcn_session.id) is AgentState.IDLE:
                binding = self._runtime_timers.get(context.bcn_session.id)
                if (
                    binding is None
                    or binding.runtime_session_id != context.runtime_session.id
                ):
                    await self._refresh_runtime_timer(
                        context.runtime_session,
                        notification.activity_at_ms,
                        full_timeout=establishment_attempt > 0,
                    )
                break
            if establishment_attempt == 0:
                await self._discard_runtime_session(context.runtime_session)
                context = self._create_runtime_session(durable_context)
        turn = RuntimeTurn(
            turn_id=turn_id,
            session_id=context.runtime_session.id,
            state=RuntimeTurnState.STARTING,
            started_at_ms=self._clock(),
            client_user_message_id=message.message_id,
        )
        try:
            async with self._storage.transaction() as transaction:
                await transaction.save_runtime_attempt(
                    RuntimeAttempt(
                        turn_id=turn.turn_id,
                        session_id=turn.session_id,
                        client_user_message_id=message.message_id,
                        started_at_ms=turn.started_at_ms,
                    )
                )
        except BaseException:
            await self._stop_runtime_session(
                context.runtime_session,
                timeout=self._timeout_budget.provider_call_seconds,
            )
            raise
        self._runtime_turns[turn.turn_id] = turn

        for attempt in range(2):
            agent_state = self._state_writer.get(context.bcn_session.id)
            if agent_state is not AgentState.IDLE:
                finish_state = (
                    RuntimeTurnState.UNKNOWN
                    if agent_state is AgentState.UNKNOWN
                    else RuntimeTurnState.FAILED
                )
                finished = await self._turns.finish_turn(
                    turn,
                    finish_state,
                    error_kind=(
                        ErrorKind.PROVIDER_UNKNOWN
                        if finish_state is RuntimeTurnState.UNKNOWN
                        else ErrorKind.PROVIDER_FAILED
                    ),
                    error_message=(
                        "runtime session start outcome is unknown"
                        if finish_state is RuntimeTurnState.UNKNOWN
                        else "runtime session failed to start"
                    ),
                    correlation=self._turns.turn_correlation(message, context, turn),
                    session_id=context.bcn_session.id,
                )
                await self._discard_runtime_session(context.runtime_session)
                return finished
            self._state_writer.apply_observation(
                context.bcn_session.id,
                AgentTick(
                    source=AgentTickSource.CHANNEL,
                    signal=AgentSignal.TURN_STARTED,
                    observed_at_ms=self._clock(),
                ),
            )
            try:
                result = await self._turns.run_turn(
                    message,
                    context,
                    turn,
                    unread_count=len(unread),
                )
                agent_state = self._state_writer.get(context.bcn_session.id)
                while agent_state is AgentState.UNKNOWN:
                    (
                        context,
                        recovered_stream,
                    ) = await self._ensure_runtime_session_or_discard(
                        context,
                        turn=result,
                        approval_handler=self._turns.approval_handler(
                            message,
                            context,
                            result,
                        ),
                    )
                    agent_state = self._state_writer.get(context.bcn_session.id)
                    recoverable_active_states = {
                        AgentState.WORKING,
                        AgentState.COMPACTION_STARTING,
                        AgentState.COMPACTING,
                        AgentState.COMPACTION_COMPLETED,
                    }
                    if recovered_stream is None or agent_state not in (
                        recoverable_active_states
                    ):
                        if recovered_stream is not None:
                            await recovered_stream.aclose()
                        if agent_state is not AgentState.IDLE:
                            await self._stop_runtime_session(
                                context.runtime_session,
                                timeout=self._timeout_budget.provider_call_seconds,
                            )
                            agent_state = self._state_writer.get(context.bcn_session.id)
                        break
                    recovered_turn = replace(
                        result,
                        state=RuntimeTurnState.RUNNING,
                        completed_at_ms=None,
                        error_kind=None,
                        error_message=None,
                    )
                    self._runtime_turns[recovered_turn.turn_id] = recovered_turn
                    result = await self._turns.resume_turn(
                        message,
                        context,
                        recovered_turn,
                        recovered_stream,
                    )
                    agent_state = self._state_writer.get(context.bcn_session.id)
                if (
                    agent_state is AgentState.FAILED
                    and self.runtime_session(context.bcn_session.id)
                    is context.runtime_session
                ):
                    await self._stop_runtime_session(
                        context.runtime_session,
                        timeout=self._timeout_budget.provider_call_seconds,
                    )
                elif (
                    agent_state not in {AgentState.IDLE, AgentState.WORKING}
                    and self.runtime_session(context.bcn_session.id) is not None
                ):
                    await self._discard_runtime_session(context.runtime_session)
                return result
            except RuntimeSessionUnavailable as error:
                self._state_writer.apply_observation(
                    context.bcn_session.id,
                    AgentTick(
                        source=AgentTickSource.RUNTIME,
                        signal=(
                            AgentSignal.FAILED if attempt == 1 else AgentSignal.UNKNOWN
                        ),
                        observed_at_ms=self._clock(),
                        error_kind=ErrorKind.PROVIDER_FAILED.value,
                        error_message=str(error),
                    ),
                )
                if attempt == 1:
                    finished = await self._turns.finish_turn(
                        turn,
                        RuntimeTurnState.FAILED,
                        error_kind=ErrorKind.PROVIDER_FAILED,
                        error_message=str(error),
                        correlation=self._turns.turn_correlation(
                            message, context, turn
                        ),
                        session_id=context.bcn_session.id,
                    )
                    await self._discard_runtime_session(context.runtime_session)
                    return finished
                (
                    context,
                    recovered_stream,
                ) = await self._ensure_runtime_session_or_discard(context)
                if recovered_stream is not None:
                    await recovered_stream.aclose()
                    raise RuntimeError(
                        "runtime retry unexpectedly recovered an active turn"
                    )
        raise AssertionError("runtime pre-start retry loop did not return")

    async def _ensure_runtime_session(
        self,
        context: SessionContext,
        *,
        turn: RuntimeTurn | None,
        approval_handler: IApprovalHandler | None,
    ) -> tuple[SessionContext, IRuntimeTurnStream | None]:
        runtime_session = context.runtime_session
        recovered_stream: IRuntimeTurnStream | None = None
        agent_state = self._state_writer.get(context.bcn_session.id)
        if agent_state in {
            AgentState.IDLE,
            AgentState.WORKING,
            AgentState.COMPACTION_STARTING,
            AgentState.COMPACTING,
            AgentState.COMPACTION_COMPLETED,
            AgentState.STOPPING,
        }:
            return context, None

        if agent_state is AgentState.CREATED:
            agent_state = self._state_writer.apply_observation(
                context.bcn_session.id,
                AgentTick(
                    source=AgentTickSource.SESSION,
                    signal=AgentSignal.START_REQUESTED,
                    observed_at_ms=self._clock(),
                ),
            )
        elif agent_state is AgentState.UNKNOWN:
            agent_state = self._state_writer.apply_observation(
                context.bcn_session.id,
                AgentTick(
                    source=AgentTickSource.RECOVERY,
                    signal=AgentSignal.RECONCILE_REQUESTED,
                    observed_at_ms=self._clock(),
                ),
            )
        if agent_state not in {AgentState.STARTING, AgentState.RECONCILING}:
            return context, None

        process_operation = (
            "start" if agent_state is AgentState.STARTING else "reconcile"
        )
        process_correlation = CorrelationContext(
            node_id=self.node_id,
            channel=context.channel_session.channel,
            channel_session_id=context.channel_session.id,
            bcn_session_id=context.bcn_session.id,
            runtime_session_id=runtime_session.id,
            provider_thread_id=runtime_session.provider_thread_id,
        )
        await self._audit.append(
            event_name=f"runtime.process.{process_operation}.requested",
            state=RuntimeEventState.STARTED,
            correlation=process_correlation,
            metadata={
                "runtime": runtime_session.runtime,
                "workspace_id": runtime_session.workspace_id,
            },
        )

        if process_operation == "start":
            provider_result = await self._runtime.start_session(
                runtime_session,
                timeout=self._timeout_budget.provider_call_seconds,
            )
        else:
            provider_result = await self._runtime.reconcile_session(
                runtime_session,
                turn,
                approval_handler,
                timeout=self._timeout_budget.startup_seconds,
            )

        now_ms = self._clock()
        reconciliation_state: AgentState | None = None
        if provider_result.status is ProviderCallStatus.CONFIRMED:
            confirmed_value = provider_result.value
            if confirmed_value is None:
                raise ValueError("confirmed runtime operation has no result")
            if process_operation == "start":
                if not isinstance(confirmed_value, RuntimeSession):
                    raise TypeError("runtime start returned an invalid result")
                updated_runtime = confirmed_value
            else:
                if not isinstance(confirmed_value, RuntimeSessionReconciliation):
                    raise TypeError("runtime reconcile returned an invalid result")
                reconciliation_state = confirmed_value.state
                recovered_stream = confirmed_value.stream
                updated_runtime = confirmed_value.session
            if (
                updated_runtime.id != runtime_session.id
                or updated_runtime.bcn_session_id != context.bcn_session.id
                or updated_runtime.channel_session_id != context.channel_session.id
                or updated_runtime.runtime != runtime_session.runtime
                or updated_runtime.workspace_id != self.workspace_id
                or updated_runtime.created_at_ms != runtime_session.created_at_ms
            ):
                raise ValueError("runtime provider returned a mismatched session")
            runtime_session = updated_runtime
            current_state = self._state_writer.get(context.bcn_session.id)
            if current_state is AgentState.STARTING:
                self._state_writer.apply_observation(
                    context.bcn_session.id,
                    AgentTick(
                        source=AgentTickSource.RUNTIME,
                        signal=AgentSignal.START_CONFIRMED,
                        observed_at_ms=now_ms,
                    ),
                )
            elif current_state is AgentState.RECONCILING:
                if reconciliation_state is None:
                    raise ValueError("confirmed reconcile has no agent state")
                self._state_writer.apply_reconciliation(
                    context.bcn_session.id,
                    reconciliation_state,
                )
            await self._audit.append(
                event_name=(
                    "runtime.process.started"
                    if process_operation == "start"
                    else "runtime.process.reconciled"
                ),
                state=RuntimeEventState.COMPLETED,
                correlation=replace(
                    process_correlation,
                    provider_thread_id=runtime_session.provider_thread_id,
                ),
                metadata={
                    "runtime": runtime_session.runtime,
                    "workspace_id": runtime_session.workspace_id,
                    **(
                        {"reconciled_state": reconciliation_state.value}
                        if reconciliation_state is not None
                        else {}
                    ),
                },
            )
        else:
            signal = (
                AgentSignal.FAILED
                if provider_result.status is ProviderCallStatus.FAILED
                else AgentSignal.UNKNOWN
            )
            self._state_writer.apply_observation(
                context.bcn_session.id,
                AgentTick(
                    source=AgentTickSource.RUNTIME,
                    signal=signal,
                    observed_at_ms=now_ms,
                    error_kind=provider_result.error_kind,
                    error_message=provider_result.error_message,
                ),
            )
            await self._audit.append(
                event_name=f"runtime.process.{signal.value}",
                state=(
                    RuntimeEventState.FAILED
                    if signal is AgentSignal.FAILED
                    else RuntimeEventState.UNKNOWN
                ),
                correlation=process_correlation,
                error_kind=(
                    ErrorKind(provider_result.error_kind)
                    if provider_result.error_kind in ErrorKind._value2member_map_
                    else ErrorKind.INTERNAL
                ),
                error_message=provider_result.error_message,
                metadata={
                    "operation": process_operation,
                    "runtime": runtime_session.runtime,
                    "workspace_id": runtime_session.workspace_id,
                },
            )

        self._runtime_sessions[context.bcn_session.id] = runtime_session
        return (
            SessionContext(
                context.channel_session,
                context.bcn_session,
                runtime_session,
            ),
            recovered_stream,
        )

    async def _stop_runtime_session(
        self, runtime_session: RuntimeSession, *, timeout: float
    ) -> None:
        async with self._concurrency.for_session(runtime_session.bcn_session_id):
            await self._stop_runtime_session_locked(runtime_session, timeout=timeout)

    async def _stop_runtime_session_locked(
        self, runtime_session: RuntimeSession, *, timeout: float
    ) -> None:
        process_correlation = CorrelationContext(
            node_id=self.node_id,
            channel_session_id=runtime_session.channel_session_id,
            bcn_session_id=runtime_session.bcn_session_id,
            runtime_session_id=runtime_session.id,
            provider_thread_id=runtime_session.provider_thread_id,
        )
        await self._audit.append(
            event_name="runtime.process.stop.requested",
            state=RuntimeEventState.STARTED,
            correlation=process_correlation,
            metadata={
                "runtime": runtime_session.runtime,
                "workspace_id": runtime_session.workspace_id,
            },
        )
        await self._state_writer.apply_locked(
            runtime_session.bcn_session_id,
            AgentTick(
                source=AgentTickSource.SESSION,
                signal=AgentSignal.STOP_REQUESTED,
                observed_at_ms=self._clock(),
            ),
        )
        try:
            result = await self._runtime.stop_session(runtime_session, timeout=timeout)
        except Exception as error:  # noqa: BLE001
            result = None
            stop_error = error
        else:
            stop_error = None
        confirmed = result is not None and result.status is ProviderCallStatus.CONFIRMED
        unknown = result is None or result.status in {
            ProviderCallStatus.UNKNOWN,
            ProviderCallStatus.QUEUED,
        }
        error_kind = (
            result.error_kind
            if result is not None
            else ErrorKind.PROVIDER_UNKNOWN.value
        )
        error_message = result.error_message if result is not None else str(stop_error)
        await self._discard_runtime_session(runtime_session)
        await self._audit.append(
            event_name=(
                "runtime.process.stop.completed"
                if confirmed
                else "runtime.process.stop.unknown"
                if unknown
                else "runtime.process.stop.failed"
            ),
            state=(
                RuntimeEventState.COMPLETED
                if confirmed
                else RuntimeEventState.UNKNOWN
                if unknown
                else RuntimeEventState.FAILED
            ),
            correlation=process_correlation,
            error_kind=(
                ErrorKind(error_kind)
                if error_kind in ErrorKind._value2member_map_
                else ErrorKind.INTERNAL
                if error_message
                else None
            )
            if not confirmed
            else None,
            error_message=error_message if not confirmed else None,
            metadata={
                "runtime": runtime_session.runtime,
                "workspace_id": runtime_session.workspace_id,
            },
        )
