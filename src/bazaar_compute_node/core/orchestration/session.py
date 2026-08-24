from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from time import time_ns
from uuid import uuid7

from ...i18n import Translator
from ..approval import IApprovalHandler
from ..audit import ErrorKind
from ..channel import IChannel
from ..concurrency import ISessionConcurrency, SessionLockRegistry
from ..correlation import CorrelationContext
from ..lifecycle import IAsyncLifecycle, TimeoutBudget
from ..models import (
    BcnSession,
    ChannelSession,
    Message,
    MessageDirection,
    RuntimeAttempt,
    RuntimeEventState,
    RuntimeSession,
    RuntimeTurn,
    RuntimeTurnState,
    SessionRuntimeObservation,
    SessionRuntimeObservationSource,
    SessionRuntimeSignal,
    SessionRuntimeState,
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
from ..storage import IHandoffStorageScope, IStorageScope
from ..timerwheel import (
    Timer,
    TimerCancelledError,
    TimerWheel,
    TimerWheelClosedError,
)
from .command import SessionCommandService
from .delivery import OutboundDeliveryService
from .error_feedback import RuntimeErrorReporter
from .services import SessionAuditRecorder, SessionRuntimeStateMachine
from .turn import (
    SessionContext,
    SessionTurnCoordinator,
    handoff_notice,
    inbox_notice,
    reminder_notice,
)


def _current_time_ms() -> int:
    return time_ns() // 1_000_000


@dataclass(slots=True)
class _IngressItem:
    message: Message
    completion: asyncio.Future[RuntimeTurn | None]


@dataclass(slots=True)
class _DurableSessionContext:
    channel_session: ChannelSession
    bcn_session: BcnSession


@dataclass(slots=True)
class _RuntimeNotification:
    message: Message
    context: _DurableSessionContext
    completion: asyncio.Future[RuntimeTurn | None]


@dataclass(frozen=True, slots=True)
class _ReminderNotification:
    reminder_id: str
    occurrence_id: str
    anchor_message: Message
    context: _DurableSessionContext
    wake_id: str


@dataclass(frozen=True, slots=True)
class _HandoffNotification:
    anchor_message: Message
    context: _DurableSessionContext
    wake_id: str


@dataclass(frozen=True, slots=True)
class _RuntimeExpiry:
    bcn_session_id: str
    runtime_session_id: str
    timer_id: int
    generation: int


type _WakeNotification = (
    _RuntimeNotification | _ReminderNotification | _HandoffNotification
)
type _RuntimeQueueItem = (
    _RuntimeNotification
    | _ReminderNotification
    | _HandoffNotification
    | _RuntimeExpiry
    | RuntimeExpire
)


@dataclass(slots=True)
class _RuntimeTimerBinding:
    runtime_session_id: str
    timer: Timer
    watcher: asyncio.Task[None]
    expired: bool = False


class SessionOrchestrator(IAsyncLifecycle):
    """Route one Agent's Channel composition through provider-neutral contracts."""

    def __init__(
        self,
        *,
        agent_id: str,
        channel: IChannel,
        runtime: IRuntime,
        storage: IStorageScope,
        handoff_storage: IHandoffStorageScope | None = None,
        audit: IAudit,
        timeout_budget: TimeoutBudget,
        timer_wheel: TimerWheel,
        runtime_idle_timeout_ms: int = 0,
        workspace: Callable[[], Path],
        translator: Translator,
        error_feedback_detail: Callable[[str, str], str],
        concurrency: ISessionConcurrency | None = None,
        clock: Callable[[], int] | None = None,
    ) -> None:
        if not isinstance(agent_id, str) or not agent_id:
            raise ValueError("agent_id must be a non-empty string")
        if not isinstance(runtime.name, str) or not runtime.name:
            raise ValueError("runtime.name must be a non-empty string")
        if (
            isinstance(runtime_idle_timeout_ms, bool)
            or not isinstance(runtime_idle_timeout_ms, int)
            or runtime_idle_timeout_ms < 0
        ):
            raise ValueError("runtime_idle_timeout_ms must be a non-negative integer")
        self._agent_id = agent_id
        self._channel = channel
        self._runtime = runtime
        self._storage = storage
        self._handoff_storage = handoff_storage
        self._timeout_budget = timeout_budget
        self._timer_wheel = timer_wheel
        self._runtime_idle_timeout_ms = runtime_idle_timeout_ms
        self._concurrency = concurrency or SessionLockRegistry()
        self._clock = clock or _current_time_ms
        self._runtime_sessions: dict[str, RuntimeSession] = {}
        self._runtime_turns: dict[str, RuntimeTurn] = {}
        self._session_runtime_states: dict[str, SessionRuntimeState] = {}
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
        self._state_machine = SessionRuntimeStateMachine(
            storage=storage,
            concurrency=self._concurrency,
            states=self._session_runtime_states,
        )
        self._delivery = OutboundDeliveryService(
            channel,
            timeout=timeout_budget.provider_call_seconds,
        )
        self._error_reporter = RuntimeErrorReporter(
            agent_id=agent_id,
            delivery=self._delivery,
            audit=self._audit,
            translator=translator,
            detail=error_feedback_detail,
        )
        self._command_service = SessionCommandService(
            delivery=self._delivery,
            storage=storage,
            audit=self._audit,
            concurrency=self._concurrency,
            node_id=lambda: self.agent_id,
            workspace=workspace,
            clock=self._clock,
        )
        self._turns = SessionTurnCoordinator(
            agent_id=agent_id,
            channel=channel,
            runtime=runtime,
            storage=storage,
            audit=self._audit,
            state_machine=self._state_machine,
            timeout_budget=timeout_budget,
            concurrency=self._concurrency,
            turns=self._runtime_turns,
            clock=self._clock,
        )
        self._active_tasks: set[asyncio.Task[RuntimeTurn | None]] = set()
        self._runtime_teardown_tasks: set[asyncio.Task[None]] = set()
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
    def agent_id(self) -> str:
        return self._agent_id

    @property
    def command_service(self) -> SessionCommandService:
        return self._command_service

    @property
    def shutdown_errors(self) -> tuple[str, ...]:
        return tuple(self._shutdown_errors)

    def session_runtime_state(self, session_id: str) -> SessionRuntimeState | None:
        """Return process-local runtime lifecycle state for one BCN session."""

        return self._session_runtime_states.get(session_id)

    def runtime_session(self, session_id: str) -> RuntimeSession | None:
        """Return the process-local runtime session bound to one BCN session."""

        return self._runtime_sessions.get(session_id)

    async def publish_reminder_wake(self, session_id: str) -> None:
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("session_id must be a non-empty string")
        if self._stopping:
            return
        if not self._started:
            raise RuntimeError("session orchestrator is not started")
        wake = await self._storage.load_reminder_wake(session_id)
        if wake is None:
            return
        context = _DurableSessionContext(wake.channel_session, wake.bcn_session)
        self._runtime_queue_for_session(session_id).put_nowait(
            _ReminderNotification(
                reminder_id=wake.occurrence.reminder_id,
                occurrence_id=wake.occurrence.occurrence_id,
                anchor_message=wake.anchor_message,
                context=context,
                wake_id=str(uuid7()),
            )
        )

    async def publish_handoff_wake(self, session_id: str) -> None:
        if self._stopping:
            return
        if not self._started:
            raise RuntimeError("session orchestrator is not started")
        wake = await self._require_handoff_storage().load_handoff_wake(session_id)
        if wake is None:
            return
        self._runtime_queue_for_session(session_id).put_nowait(
            _HandoffNotification(
                anchor_message=wake.anchor_message,
                context=_DurableSessionContext(
                    wake.channel_session,
                    wake.bcn_session,
                ),
                wake_id=str(uuid7()),
            )
        )

    def _require_handoff_storage(self) -> IHandoffStorageScope:
        if self._handoff_storage is None:
            raise RuntimeError("handoff storage is not configured")
        return self._handoff_storage

    def _runtime_queue_for_session(
        self,
        session_id: str,
    ) -> asyncio.Queue[_RuntimeQueueItem]:
        runtime_queue = self._runtime_queues.get(session_id)
        if runtime_queue is not None:
            return runtime_queue
        runtime_queue = asyncio.Queue()
        self._runtime_queues[session_id] = runtime_queue
        self._runtime_workers[session_id] = asyncio.create_task(
            self._runtime_loop(session_id, runtime_queue),
            name=f"bcn-runtime-{self.agent_id}-{session_id}",
        )
        return runtime_queue

    def _create_runtime_session(
        self,
        context: _DurableSessionContext,
    ) -> SessionContext:
        now_ms = self._clock()
        runtime_session = RuntimeSession(
            id=str(uuid7()),
            bcn_session_id=context.bcn_session.id,
            channel_session_id=context.channel_session.id,
            runtime=self._runtime.name,
            workspace_id=self.agent_id,
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
            self._session_runtime_states.pop(runtime_session.bcn_session_id, None)

    async def _cancel_runtime_timer(self, runtime_session: RuntimeSession) -> None:
        binding = self._runtime_timers.get(runtime_session.bcn_session_id)
        if binding is None or binding.runtime_session_id != runtime_session.id:
            return
        self._runtime_timers.pop(runtime_session.bcn_session_id, None)
        binding.timer.cancel()
        if not binding.watcher.done():
            binding.watcher.cancel()
        await asyncio.gather(binding.watcher, return_exceptions=True)

    async def _start_runtime_timer(self, runtime_session: RuntimeSession) -> None:
        if self._runtime_idle_timeout_ms <= 0:
            return
        queue = self._runtime_queues.get(runtime_session.bcn_session_id)
        if queue is None:
            return
        await self._cancel_runtime_timer(runtime_session)
        timer = self._timer_wheel.create(self._runtime_idle_timeout_ms)
        watcher = asyncio.create_task(
            self._forward_runtime_session_expiry(runtime_session, timer, queue),
            name=f"bcn-runtime-expiry-{runtime_session.bcn_session_id}",
        )
        self._runtime_timers[runtime_session.bcn_session_id] = _RuntimeTimerBinding(
            runtime_session.id,
            timer,
            watcher,
        )

    async def _start_runtime_timer_if_idle(self, session_id: str) -> None:
        runtime_session = self.runtime_session(session_id)
        if (
            runtime_session is None
            or runtime_session.id in self._expired_runtime_ids
            or self._state_machine.get(session_id) is not SessionRuntimeState.IDLE
        ):
            return
        try:
            if await self._runtime.has_background_job(
                runtime_session,
                timeout=self._timeout_budget.provider_call_seconds,
            ):
                return
        except asyncio.CancelledError:
            raise
        except Exception:
            self._logger.exception("runtime background job check failed")
        await self._start_runtime_timer(runtime_session)

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
                await self._stop_runtime_session(
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
        try:
            await self._runtime.start(timeout=timeout)
            await self._channel.start(timeout=timeout)
        except BaseException:
            await self._runtime.stop(timeout=timeout)
            raise
        self._started = True
        self._receive_task = asyncio.create_task(
            self._receive_loop(),
            name=f"bcn-channel-receive-{self.agent_id}",
        )
        self._runtime_expire_task = asyncio.create_task(
            self._receive_runtime_expire_loop(),
            name=f"bcn-runtime-expire-events-{self.agent_id}",
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

        await self._wait_for_runtime_teardown_tasks(timeout=timeout)

        try:
            await self._runtime.stop(timeout=timeout)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            self._shutdown_errors.append(f"runtime.stop: {type(error).__name__}")
        self._started = False
        self._session_runtime_states.clear()
        self._runtime_sessions.clear()
        self._runtime_turns.clear()
        self._expired_runtime_ids.clear()

    def dispatch_inbound(
        self,
        message: Message,
    ) -> asyncio.Task[RuntimeTurn | None]:
        if self._stopping:
            raise RuntimeError("session orchestrator is stopping")
        task = asyncio.create_task(
            self.handle_inbound(message),
            name=f"bcn-inbound-{message.message_id}",
        )
        self._active_tasks.add(task)
        task.add_done_callback(self._forget_task)
        return task

    async def observe_runtime(
        self,
        session_id: str,
        observation: SessionRuntimeObservation,
    ) -> SessionRuntimeState:
        if not session_id:
            raise ValueError("session_id must be a non-empty string")
        return await self._state_machine.apply(session_id, observation)

    async def handle_inbound(self, message: Message) -> RuntimeTurn | None:
        loop = asyncio.get_running_loop()
        completion: asyncio.Future[RuntimeTurn | None] = loop.create_future()
        channel, provider_thread_id, _ = message.inbound_identity()
        conversation_key = (channel, provider_thread_id)
        ingress_queue = self._ingress_queues.get(conversation_key)
        if ingress_queue is None:
            ingress_queue = asyncio.Queue()
            self._ingress_queues[conversation_key] = ingress_queue
            self._ingress_workers[conversation_key] = asyncio.create_task(
                self._ingress_loop(ingress_queue),
                name=f"bcn-ingress-{message.channel_session_id}",
            )
        ingress_queue.put_nowait(_IngressItem(message, completion))
        return await completion

    async def _ingress_loop(self, queue: asyncio.Queue[_IngressItem]) -> None:
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
                    runtime_queue = self._runtime_queue_for_session(session_id)
                if runtime_queue is None or not message.notifies_runtime:
                    if not item.completion.done():
                        item.completion.set_result(None)
                else:
                    runtime_queue.put_nowait(
                        _RuntimeNotification(
                            message,
                            context,
                            item.completion,
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
            item = pending.pop(0) if pending else await queue.get()
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

            batch: list[_WakeNotification] = [item]
            if isinstance(item, _RuntimeNotification):
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
            runtime_session = self.runtime_session(batch[0].context.bcn_session.id)
            if runtime_session is not None:
                await self._cancel_runtime_timer(runtime_session)
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
                        if isinstance(
                            queued_item,
                            _RuntimeNotification
                            | _ReminderNotification
                            | _HandoffNotification,
                        ):
                            runtime_session = self.runtime_session(
                                queued_item.context.bcn_session.id
                            )
                            if runtime_session is not None:
                                await self._cancel_runtime_timer(runtime_session)
                            pending.append(queued_item)
                            await self._steer_active_turn(queued_item)
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
                await self._start_runtime_timer_if_idle(
                    batch[0].context.bcn_session.id,
                )
                route_message = (
                    batch[0].message
                    if isinstance(batch[0], _RuntimeNotification)
                    else batch[0].anchor_message
                )
                try:
                    await self._error_reporter.report(route_message, result)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self._logger.exception("runtime error feedback failed")
                for notification in batch:
                    if (
                        isinstance(notification, _RuntimeNotification)
                        and not notification.completion.done()
                    ):
                        notification.completion.set_result(result)
                await self._stop_expired_runtime_if_idle(
                    batch[0].context.bcn_session.id,
                    queue,
                    queue_quiescent=not pending and queue.empty(),
                )
            except asyncio.CancelledError:
                turn_task.cancel()
                queue_task.cancel()
                await asyncio.gather(turn_task, queue_task, return_exceptions=True)
                if (
                    not queue_item_consumed
                    and not queue_task.cancelled()
                    and queue_task.exception() is None
                ):
                    pending.append(queue_task.result())
                    queue_item_consumed = True
                for queued_item in (*batch, *pending):
                    if (
                        isinstance(queued_item, _RuntimeNotification)
                        and not queued_item.completion.done()
                    ):
                        queued_item.completion.cancel()
                for _ in range(len(pending)):
                    queue.task_done()
                pending.clear()
                raise
            except Exception as error:
                turn_task.cancel()
                await asyncio.gather(turn_task, return_exceptions=True)
                await self._start_runtime_timer_if_idle(
                    batch[0].context.bcn_session.id,
                )
                for notification in batch:
                    if (
                        isinstance(notification, _RuntimeNotification)
                        and not notification.completion.done()
                    ):
                        notification.completion.set_exception(error)
                if isinstance(batch[0], _ReminderNotification | _HandoffNotification):
                    self._logger.exception("wake runtime notification failed")
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

    async def _steer_active_turn(self, notification: _WakeNotification) -> None:
        session_id = notification.context.bcn_session.id
        runtime_session = self.runtime_session(session_id)
        if runtime_session is None:
            return
        if isinstance(notification, _RuntimeNotification):
            cursor = await self._storage.get_consumer_cursor(session_id)
            delivered_through_seq = (
                cursor.delivered_through_seq if cursor is not None else 0
            )
            unread = await self._storage.list_messages(
                session_id,
                after_seq=delivered_through_seq,
                direction=MessageDirection.INBOUND,
                notifying_only=True,
            )
            if not unread:
                return
            message = notification.message
            input_text = inbox_notice(session_id, len(unread))
        elif isinstance(notification, _ReminderNotification):
            pending_count = await self._storage.count_pending_reminder_occurrences(
                session_id
            )
            if pending_count == 0:
                return
            message = notification.anchor_message
            input_text = reminder_notice(session_id, pending_count)
        else:
            pending_count = (
                await self._require_handoff_storage().count_pending_handoffs(session_id)
            )
            if pending_count == 0:
                return
            message = notification.anchor_message
            input_text = handoff_notice(session_id, pending_count)
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
        if active_turn is None:
            return
        await self._turns.steer_turn(
            message,
            SessionContext(
                notification.context.channel_session,
                notification.context.bcn_session,
                runtime_session,
            ),
            active_turn,
            input_text=input_text,
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
            or self._state_machine.get(session_id) is not SessionRuntimeState.IDLE
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
                or self._state_machine.get(session_id) is not SessionRuntimeState.IDLE
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
                        "agent_id": self.agent_id,
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
        self,
        message: Message,
    ) -> tuple[_DurableSessionContext | None, Message, bool]:
        recorded = await self._storage.record_inbound(
            message,
            now_ms=self._clock(),
        )
        message = recorded.message
        context = _DurableSessionContext(
            recorded.channel_session,
            recorded.bcn_session,
        )
        if recorded.message_created:
            await self._audit.append(
                event_name="channel.inbound.persisted",
                state=RuntimeEventState.COMPLETED,
                correlation=CorrelationContext(
                    node_id=self.agent_id,
                    channel=message.channel,
                    channel_session_id=message.channel_session_id,
                    bcn_session_id=message.session_id,
                    provider_thread_id=message.provider_thread_id,
                    inbound_seq=message.seq,
                ),
                metadata={
                    "notifies_runtime": message.notifies_runtime,
                    "channel_session_mapping": (
                        "created" if recorded.channel_session_created else "reused"
                    ),
                    "bcn_session_mapping": (
                        "created" if recorded.bcn_session_created else "reused"
                    ),
                },
            )
        return context, message, recorded.message_created

    async def _run_notification(
        self,
        notification: _WakeNotification,
    ) -> RuntimeTurn | None:
        durable_context = notification.context
        if isinstance(notification, _RuntimeNotification):
            message = notification.message
            cursor = await self._storage.get_consumer_cursor(
                durable_context.bcn_session.id
            )
            delivered_through_seq = (
                cursor.delivered_through_seq if cursor is not None else 0
            )
            unread = await self._storage.list_messages(
                durable_context.bcn_session.id,
                after_seq=delivered_through_seq,
                direction=MessageDirection.INBOUND,
                notifying_only=True,
            )
            if not unread:
                return None
            client_user_message_id = message.message_id
            turn_id = f"turn-{client_user_message_id}"
            if await self._storage.get_runtime_attempt(turn_id) is not None:
                return self._runtime_turns.get(turn_id)
            input_text = inbox_notice(durable_context.bcn_session.id, len(unread))
            observation_source = SessionRuntimeObservationSource.CHANNEL
        elif isinstance(notification, _ReminderNotification):
            message = notification.anchor_message
            pending_count = await self._storage.count_pending_reminder_occurrences(
                durable_context.bcn_session.id
            )
            if pending_count == 0:
                return None
            client_user_message_id = notification.wake_id
            turn_id = f"turn-{client_user_message_id}"
            if await self._storage.get_runtime_attempt(turn_id) is not None:
                return self._runtime_turns.get(turn_id)
            input_text = reminder_notice(
                durable_context.bcn_session.id,
                pending_count,
            )
            observation_source = SessionRuntimeObservationSource.SESSION
        else:
            message = notification.anchor_message
            storage = self._require_handoff_storage()
            pending_count = await storage.count_pending_handoffs(
                durable_context.bcn_session.id
            )
            if pending_count == 0:
                return None
            client_user_message_id = notification.wake_id
            turn_id = f"turn-{client_user_message_id}"
            if await storage.get_runtime_attempt(turn_id) is not None:
                return self._runtime_turns.get(turn_id)
            input_text = handoff_notice(
                durable_context.bcn_session.id,
                pending_count,
            )
            observation_source = SessionRuntimeObservationSource.SESSION

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
            if (
                self._state_machine.get(context.bcn_session.id)
                is SessionRuntimeState.IDLE
            ):
                break
            if establishment_attempt == 0:
                await self._discard_runtime_session(context.runtime_session)
                context = self._create_runtime_session(durable_context)
        turn = RuntimeTurn(
            turn_id=turn_id,
            session_id=context.runtime_session.id,
            state=RuntimeTurnState.STARTING,
            started_at_ms=self._clock(),
            client_user_message_id=client_user_message_id,
        )
        try:
            await self._storage.save_runtime_attempt(
                RuntimeAttempt(
                    turn_id=turn.turn_id,
                    session_id=turn.session_id,
                    client_user_message_id=client_user_message_id,
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
            runtime_state = self._state_machine.get(context.bcn_session.id)
            if runtime_state is not SessionRuntimeState.IDLE:
                finish_state = (
                    RuntimeTurnState.UNKNOWN
                    if runtime_state is SessionRuntimeState.UNKNOWN
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
            self._state_machine.apply_observation(
                context.bcn_session.id,
                SessionRuntimeObservation(
                    source=observation_source,
                    signal=SessionRuntimeSignal.TURN_STARTED,
                    observed_at_ms=self._clock(),
                ),
            )
            try:
                result = await self._turns.run_turn(
                    message,
                    context,
                    turn,
                    input_text=input_text,
                )
                runtime_state = self._state_machine.get(context.bcn_session.id)
                while runtime_state is SessionRuntimeState.UNKNOWN:
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
                    runtime_state = self._state_machine.get(context.bcn_session.id)
                    recoverable_active_states = {
                        SessionRuntimeState.WORKING,
                        SessionRuntimeState.COMPACTION_STARTING,
                        SessionRuntimeState.COMPACTING,
                        SessionRuntimeState.COMPACTION_COMPLETED,
                    }
                    if recovered_stream is None or runtime_state not in (
                        recoverable_active_states
                    ):
                        if recovered_stream is not None:
                            await recovered_stream.aclose()
                        if runtime_state is not SessionRuntimeState.IDLE:
                            await self._stop_runtime_session(
                                context.runtime_session,
                                timeout=self._timeout_budget.provider_call_seconds,
                            )
                            runtime_state = self._state_machine.get(
                                context.bcn_session.id
                            )
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
                    runtime_state = self._state_machine.get(context.bcn_session.id)
                if (
                    runtime_state is SessionRuntimeState.FAILED
                    and self.runtime_session(context.bcn_session.id)
                    is context.runtime_session
                ):
                    await self._stop_runtime_session(
                        context.runtime_session,
                        timeout=self._timeout_budget.provider_call_seconds,
                    )
                elif (
                    runtime_state
                    not in {SessionRuntimeState.IDLE, SessionRuntimeState.WORKING}
                    and self.runtime_session(context.bcn_session.id) is not None
                ):
                    await self._discard_runtime_session(context.runtime_session)
                return result
            except RuntimeSessionUnavailable as error:
                self._state_machine.apply_observation(
                    context.bcn_session.id,
                    SessionRuntimeObservation(
                        source=SessionRuntimeObservationSource.RUNTIME,
                        signal=(
                            SessionRuntimeSignal.FAILED
                            if attempt == 1
                            else SessionRuntimeSignal.UNKNOWN
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
                            message,
                            context,
                            turn,
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
        runtime_state = self._state_machine.get(context.bcn_session.id)
        if runtime_state in {
            SessionRuntimeState.IDLE,
            SessionRuntimeState.WORKING,
            SessionRuntimeState.COMPACTION_STARTING,
            SessionRuntimeState.COMPACTING,
            SessionRuntimeState.COMPACTION_COMPLETED,
            SessionRuntimeState.STOPPING,
        }:
            return context, None

        if runtime_state is SessionRuntimeState.CREATED:
            runtime_state = self._state_machine.apply_observation(
                context.bcn_session.id,
                SessionRuntimeObservation(
                    source=SessionRuntimeObservationSource.SESSION,
                    signal=SessionRuntimeSignal.START_REQUESTED,
                    observed_at_ms=self._clock(),
                ),
            )
        elif runtime_state is SessionRuntimeState.UNKNOWN:
            runtime_state = self._state_machine.apply_observation(
                context.bcn_session.id,
                SessionRuntimeObservation(
                    source=SessionRuntimeObservationSource.RECOVERY,
                    signal=SessionRuntimeSignal.RECONCILE_REQUESTED,
                    observed_at_ms=self._clock(),
                ),
            )
        if runtime_state not in {
            SessionRuntimeState.STARTING,
            SessionRuntimeState.RECONCILING,
        }:
            return context, None

        process_operation = (
            "start" if runtime_state is SessionRuntimeState.STARTING else "reconcile"
        )
        process_correlation = CorrelationContext(
            node_id=self.agent_id,
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
        reconciliation_state: SessionRuntimeState | None = None
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
                or updated_runtime.workspace_id != self.agent_id
                or updated_runtime.created_at_ms != runtime_session.created_at_ms
            ):
                raise ValueError("runtime provider returned a mismatched session")
            runtime_session = updated_runtime
            current_state = self._state_machine.get(context.bcn_session.id)
            if current_state is SessionRuntimeState.STARTING:
                self._state_machine.apply_observation(
                    context.bcn_session.id,
                    SessionRuntimeObservation(
                        source=SessionRuntimeObservationSource.RUNTIME,
                        signal=SessionRuntimeSignal.START_CONFIRMED,
                        observed_at_ms=now_ms,
                    ),
                )
            elif current_state is SessionRuntimeState.RECONCILING:
                if reconciliation_state is None:
                    raise ValueError("confirmed reconcile has no session runtime state")
                self._state_machine.apply_reconciliation(
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
                SessionRuntimeSignal.FAILED
                if provider_result.status is ProviderCallStatus.FAILED
                else SessionRuntimeSignal.UNKNOWN
            )
            self._state_machine.apply_observation(
                context.bcn_session.id,
                SessionRuntimeObservation(
                    source=SessionRuntimeObservationSource.RUNTIME,
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
                    if signal is SessionRuntimeSignal.FAILED
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
        self,
        runtime_session: RuntimeSession,
        *,
        timeout: float,
    ) -> None:
        async with self._concurrency.for_session(runtime_session.bcn_session_id):
            await self._stop_runtime_session_locked(runtime_session, timeout=timeout)

    async def _wait_for_runtime_teardown_tasks(self, *, timeout: float) -> None:
        tasks = tuple(self._runtime_teardown_tasks)
        if not tasks:
            return
        gathered = asyncio.gather(*tasks, return_exceptions=True)
        try:
            await asyncio.wait_for(asyncio.shield(gathered), timeout=timeout)
        except TimeoutError:
            self._shutdown_errors.append("runtime session teardown: shutdown timeout")

    def _schedule_runtime_session_teardown(
        self,
        runtime_session: RuntimeSession,
        *,
        correlation: CorrelationContext,
        timeout: float,
    ) -> None:
        task = asyncio.create_task(
            self._complete_runtime_session_teardown(
                runtime_session,
                correlation=correlation,
                timeout=timeout,
            ),
            name=f"bcn-runtime-teardown-{runtime_session.id}",
        )
        self._runtime_teardown_tasks.add(task)
        task.add_done_callback(self._forget_runtime_teardown_task)

    async def _complete_runtime_session_teardown(
        self,
        runtime_session: RuntimeSession,
        *,
        correlation: CorrelationContext,
        timeout: float,
    ) -> None:
        try:
            result = await self._runtime.stop_session(runtime_session, timeout=timeout)
        except asyncio.CancelledError:
            raise
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
            correlation=correlation,
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

    def _forget_runtime_teardown_task(self, task: asyncio.Task[None]) -> None:
        self._runtime_teardown_tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is None:
            return
        self._logger.error(
            "runtime session teardown failed: %s",
            error,
            exc_info=(type(error), error, error.__traceback__),
        )

    async def _stop_runtime_session_locked(
        self,
        runtime_session: RuntimeSession,
        *,
        timeout: float,
    ) -> None:
        process_correlation = CorrelationContext(
            node_id=self.agent_id,
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
        await self._state_machine.apply_locked(
            runtime_session.bcn_session_id,
            SessionRuntimeObservation(
                source=SessionRuntimeObservationSource.SESSION,
                signal=SessionRuntimeSignal.STOP_REQUESTED,
                observed_at_ms=self._clock(),
            ),
        )
        self._schedule_runtime_session_teardown(
            runtime_session,
            correlation=process_correlation,
            timeout=timeout,
        )
        await self._discard_runtime_session(runtime_session)
