from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Container, Iterable, Iterator, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import uuid7

from ...i18n import Translator
from ..agent import Agent, State
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
)
from ..observability import IAudit
from ..outcomes import ProviderCallResult, ProviderCallStatus
from ..runtime import (
    IRuntime,
    IRuntimeTurnStream,
    Runtime,
    RuntimeBackgroundIdle,
    RuntimeExpire,
    RuntimeSessionReconciliation,
    RuntimeSessionUnavailable,
)
from ..storage import IStorageScope
from ..timerwheel import (
    Timer,
    TimerCancelledError,
    TimerWheel,
    TimerWheelClosedError,
)
from ..utils.clock import now_ms
from ..utils.text import format_exception
from .command import SessionCommandService
from .delivery import OutboundDeliveryService
from .error_feedback import MESSAGE_KEYS, RuntimeErrorReporter
from .services import SessionAuditRecorder
from .turn import (
    SessionContext,
    SessionTurnCoordinator,
    inbox_notice,
)
from .upgrade_notice import UpgradeAnnounced, UpgradeNotice, UpgradePending


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
    completion: asyncio.Future[RuntimeTurn | None] | None = None
    wake_id: str | None = None


@dataclass(frozen=True, slots=True)
class _RuntimeExpiry:
    bcn_session_id: str
    runtime_session_id: str
    timer_id: int
    generation: int


type _RuntimeQueueItem = (
    _RuntimeNotification | _RuntimeExpiry | RuntimeExpire | RuntimeBackgroundIdle
)


def _awaiting(
    items: Iterable[_RuntimeQueueItem],
) -> Iterator[asyncio.Future[RuntimeTurn | None]]:
    """Yield the completions still waiting on an answer."""

    for item in items:
        if (
            isinstance(item, _RuntimeNotification)
            and item.completion is not None
            and not item.completion.done()
        ):
            yield item.completion


def _take_notifications(
    first: _RuntimeNotification,
    pending: list[_RuntimeQueueItem],
    queue: asyncio.Queue[_RuntimeQueueItem],
) -> list[_RuntimeNotification]:
    """Collect the run of notifications starting here, leaving the rest queued."""

    batch = [first]
    while True:
        if pending:
            candidate = pending.pop(0)
        else:
            try:
                candidate = queue.get_nowait()
            except asyncio.QueueEmpty:
                return batch
        if not isinstance(candidate, _RuntimeNotification):
            pending.insert(0, candidate)
            return batch
        batch.append(candidate)


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
        runtimes: Sequence[IRuntime],
        storage: IStorageScope,
        audit: IAudit,
        timeout_budget: TimeoutBudget,
        timer_wheel: TimerWheel,
        runtime_idle_timeout_ms: int = 0,
        workspace: Callable[[], Path],
        translator: Translator,
        error_feedback_detail: Callable[[str, str], str],
        upgrade_notice: Callable[[], tuple[str, str] | None] = lambda: None,
        concurrency: ISessionConcurrency | None = None,
        clock: Callable[[], int] | None = None,
    ) -> None:
        if not isinstance(agent_id, str) or not agent_id:
            raise ValueError("agent_id must be a non-empty string")
        self._clock = clock or now_ms
        self._runtimes = Runtime(runtimes, clock=self._clock)
        if (
            isinstance(runtime_idle_timeout_ms, bool)
            or not isinstance(runtime_idle_timeout_ms, int)
            or runtime_idle_timeout_ms < 0
        ):
            raise ValueError("runtime_idle_timeout_ms must be a non-negative integer")
        self._agent_id = agent_id
        self._channel = channel
        self._upgrade_notice = upgrade_notice
        self._runtime_idle_timeout_ms = runtime_idle_timeout_ms
        self._storage = storage
        self._timeout_budget = timeout_budget
        self._timer_wheel = timer_wheel
        self._concurrency = concurrency or SessionLockRegistry()
        self._runtime_sessions: dict[str, RuntimeSession] = {}
        # the runtime a session last ran on outlives the session itself, so a
        # turn that failed while establishing it can still be attributed
        self._runtime_turns: dict[str, RuntimeTurn] = {}
        self._session_runtime_states: dict[str, State] = {}
        # what each conversation has been told about the release on offer, so a
        # session hears of one once and of the next one again
        self._session_upgrades: dict[str, UpgradeNotice] = {}
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
        self._agent = Agent(self._session_runtime_states)
        self._delivery = OutboundDeliveryService(
            channel,
            timeout=timeout_budget.provider_call_seconds,
        )
        self._error_reporter = RuntimeErrorReporter(
            agent_id=agent_id,
            delivery=self._delivery,
            storage=storage,
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
            publish_wake=self.publish_inbox_wake,
        )
        self._turns = SessionTurnCoordinator(
            agent_id=agent_id,
            channel=channel,
            runtimes=self._runtimes,
            storage=storage,
            audit=self._audit,
            agent=self._agent,
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
        self._runtime_event_tasks: list[asyncio.Task[None]] = []
        self._background_failures: dict[str, str] = {}
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

    @property
    def health(self) -> dict[str, object]:
        return {
            "state": "degraded" if self._background_failures else "ready",
            "background_failures": dict(self._background_failures),
        }

    def session_runtime_state(self, session_id: str) -> State | None:
        """Return process-local runtime lifecycle state for one BCN session."""

        return self._session_runtime_states.get(session_id)

    def runtime_session(self, session_id: str) -> RuntimeSession | None:
        """Return the process-local runtime session bound to one BCN session."""

        return self._runtime_sessions.get(session_id)

    async def publish_inbox_wake(self, message: Message) -> None:
        if self._stopping:
            return
        if not self._started:
            raise RuntimeError("session orchestrator is not started")
        bcn_session = await self._storage.get_bcn_session(message.session_id)
        if bcn_session is None:
            raise ValueError(f"unknown bcn session: {message.session_id}")
        channel_session = await self._storage.get_channel_session(
            bcn_session.channel_session_id
        )
        if channel_session is None:
            raise ValueError(
                f"unknown channel session: {bcn_session.channel_session_id}"
            )
        self._runtime_queue_for_session(message.session_id).put_nowait(
            _RuntimeNotification(
                message=message,
                context=_DurableSessionContext(channel_session, bcn_session),
                wake_id=str(uuid7()),
            )
        )

    def _runtime_queue_for_session(
        self,
        session_id: str,
    ) -> asyncio.Queue[_RuntimeQueueItem]:
        runtime_queue = self._runtime_queues.get(session_id)
        if runtime_queue is not None:
            return runtime_queue
        runtime_queue = asyncio.Queue()
        self._runtime_queues[session_id] = runtime_queue
        self._start_runtime_worker(session_id, runtime_queue)
        return runtime_queue

    def _start_runtime_worker(
        self,
        session_id: str,
        queue: asyncio.Queue[_RuntimeQueueItem],
    ) -> None:
        worker = asyncio.create_task(
            self._runtime_loop(session_id, queue),
            name=f"bcn-runtime-{self.agent_id}-{session_id}",
        )
        self._runtime_workers[session_id] = worker
        worker.add_done_callback(
            lambda completed: self._runtime_worker_done(
                session_id,
                queue,
                completed,
            )
        )

    def _create_runtime_session(
        self,
        context: _DurableSessionContext,
        *,
        exclude: Container[int] = (),
    ) -> SessionContext:
        now_ms = self._clock()
        runtime_index = self._runtimes.select(exclude=exclude)
        runtime_session = RuntimeSession(
            id=str(uuid7()),
            bcn_session_id=context.bcn_session.id,
            channel_session_id=context.channel_session.id,
            runtime=self._runtimes.get(runtime_index).name,
            runtime_index=runtime_index,
            workspace_id=self.agent_id,
            created_at_ms=now_ms,
            updated_at_ms=now_ms,
        )
        self._runtime_sessions[context.bcn_session.id] = runtime_session
        self._runtimes.bind(context.bcn_session.id, runtime_index)
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

    async def _cancel_session_timer(self, bcn_session_id: str) -> None:
        runtime_session = self.runtime_session(bcn_session_id)
        if runtime_session is not None:
            await self._cancel_runtime_timer(runtime_session)

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
            or self._agent.get(session_id) is not State.IDLE
        ):
            return
        runtime = self._runtimes.get(runtime_session.runtime_index)
        try:
            if await runtime.has_background_job(
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
            for runtime in self._runtimes.all():
                await runtime.start(timeout=timeout)
            await self._channel.start(timeout=timeout)
        except BaseException:
            for runtime in self._runtimes.all():
                await runtime.stop(timeout=timeout)
            raise
        self._started = True
        self._background_failures.clear()
        self._receive_task = asyncio.create_task(
            self._receive_loop(),
            name=f"bcn-channel-receive-{self.agent_id}",
        )
        self._observe_background_task("channel_receive", self._receive_task)
        for index, runtime in enumerate(self._runtimes.all()):
            event_task = asyncio.create_task(
                self._receive_runtime_event_loop(index, runtime),
                name=f"bcn-runtime-lifecycle-events-{self.agent_id}-{index}",
            )
            self._runtime_event_tasks.append(event_task)
            self._observe_background_task(f"runtime_event:{index}", event_task)

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

        for index, event_task in enumerate(self._runtime_event_tasks):
            if event_task.done():
                continue
            event_task.cancel()
            try:
                await asyncio.wait_for(event_task, timeout=timeout)
            except TimeoutError, asyncio.CancelledError:
                self._shutdown_errors.append(f"runtime.event:{index}: shutdown timeout")
        self._runtime_event_tasks.clear()

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

        for index, runtime in enumerate(self._runtimes.all()):
            try:
                await runtime.stop(timeout=timeout)
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001
                self._shutdown_errors.append(
                    f"runtime.stop:{index}: {type(error).__name__}"
                )
        self._started = False
        self._session_runtime_states.clear()
        self._session_upgrades.clear()
        self._runtime_sessions.clear()
        self._runtimes.release_all()
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

    async def _handle_queue_item(
        self,
        session_id: str,
        item: _RuntimeExpiry | RuntimeExpire | RuntimeBackgroundIdle,
        queue: asyncio.Queue[_RuntimeQueueItem],
        *,
        queue_quiescent: bool,
    ) -> None:
        """Act on a queue item that is not a turn to take."""

        match item:
            case _RuntimeExpiry():
                await self._handle_runtime_expiry(
                    item, queue, queue_quiescent=queue_quiescent
                )
            case RuntimeExpire():
                await self._handle_runtime_context_expire(session_id, item, queue)
            case RuntimeBackgroundIdle():
                await self._handle_runtime_background_idle(session_id, item)

    async def _absorb_queue_item(
        self,
        session_id: str,
        item: _RuntimeQueueItem,
        pending: list[_RuntimeQueueItem],
        queue: asyncio.Queue[_RuntimeQueueItem],
    ) -> None:
        """Take in an item that arrived while a turn was already running."""

        if isinstance(item, _RuntimeNotification):
            await self._cancel_session_timer(item.context.bcn_session.id)
            pending.append(item)
            await self._steer_active_turn(item)
            return
        await self._handle_queue_item(session_id, item, queue, queue_quiescent=False)
        queue.task_done()

    async def _runtime_loop(
        self,
        session_id: str,
        queue: asyncio.Queue[_RuntimeQueueItem],
    ) -> None:
        pending: list[_RuntimeQueueItem] = []
        while True:
            item = pending.pop(0) if pending else await queue.get()
            if not isinstance(item, _RuntimeNotification):
                try:
                    await self._handle_queue_item(
                        session_id,
                        item,
                        queue,
                        queue_quiescent=not pending and queue.empty(),
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self._logger.exception("runtime %s failed", type(item).__name__)
                finally:
                    queue.task_done()
                continue

            batch = _take_notifications(item, pending, queue)
            await self._cancel_session_timer(batch[0].context.bcn_session.id)
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
                        await self._absorb_queue_item(
                            session_id, queued_item, pending, queue
                        )
                    if turn_task in done:
                        break
                    queue_task = asyncio.create_task(queue.get())
                    queue_item_consumed = False

                result = turn_task.result()
                await self._start_runtime_timer_if_idle(
                    batch[0].context.bcn_session.id,
                )
                try:
                    await self._error_reporter.report(batch[0].message, result)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self._logger.exception("runtime error feedback failed")
                await self._record_runtime_outcome(batch[0].message, result)
                for completion in _awaiting(batch):
                    completion.set_result(result)
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
                for completion in _awaiting((*batch, *pending)):
                    completion.cancel()
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
                for completion in _awaiting(batch):
                    completion.set_exception(error)
                if batch[0].completion is None:
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

    async def _steer_active_turn(self, notification: _RuntimeNotification) -> None:
        session_id = notification.context.bcn_session.id
        runtime_session = self.runtime_session(session_id)
        if runtime_session is None:
            return
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
        # a message arriving mid-turn is consumed here, and the turn path that
        # would otherwise carry the offer then finds nothing unread to run for
        upgrade = self._upgrade_for(session_id)
        input_text = inbox_notice(
            (message,),
            total_unread_count=len(unread),
            closing_bracket_on_own_line=False,
            upgrade_version=upgrade[0] if upgrade is not None else None,
            installed_version=upgrade[1] if upgrade is not None else None,
        )
        active_turn = next(
            (
                turn
                for turn in self._runtime_turns.values()
                if turn.session_id == runtime_session.id
                and turn.state is RuntimeTurnState.RUNNING
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

    async def _handle_runtime_background_idle(
        self,
        session_id: str,
        event: RuntimeBackgroundIdle,
    ) -> None:
        runtime_session = self.runtime_session(session_id)
        if runtime_session is None or runtime_session.id != event.runtime_session_id:
            return
        await self._start_runtime_timer_if_idle(session_id)

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
            or self._agent.get(session_id) is not State.IDLE
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
                or self._agent.get(session_id) is not State.IDLE
            ):
                return
            await self._stop_runtime_session_locked(
                runtime_session,
                timeout=self._timeout_budget.provider_call_seconds,
            )

    async def _hand_turn_to_another_runtime(
        self,
        message: Message,
        context: SessionContext,
        turn: RuntimeTurn,
        attempted: set[int],
    ) -> bool:
        """Ban the runtime that just failed and say whether another can try."""

        if turn.state not in MESSAGE_KEYS:
            return False
        if len(attempted) >= len(self._runtimes.all()):
            return False
        await self._record_runtime_outcome(message, turn)
        await self._discard_runtime_session(context.runtime_session)
        return True

    async def _record_runtime_outcome(
        self,
        message: Message,
        turn: RuntimeTurn | None,
    ) -> None:
        if turn is None:
            return
        index = self._runtimes.holder(message.session_id)
        if index is None:
            return
        if turn.state in MESSAGE_KEYS:
            event_name = "runtime.pool.banned"
            ban_until_ms = self._runtimes.record_failure(index)
        elif turn.state is RuntimeTurnState.COMPLETED:
            lifted_ban_until_ms = self._runtimes.record_success(index)
            if lifted_ban_until_ms is None:
                return
            event_name = "runtime.pool.released"
            ban_until_ms = lifted_ban_until_ms
        else:
            return
        await self._audit.append(
            event_name=event_name,
            state=RuntimeEventState.COMPLETED,
            correlation=CorrelationContext(
                node_id=self.agent_id,
                channel=message.channel,
                channel_session_id=message.channel_session_id,
                bcn_session_id=message.session_id,
                runtime_session_id=turn.session_id,
                turn_id=turn.turn_id,
            ),
            metadata={
                "runtime_index": index,
                "runtime": self._runtimes.get(index).name,
                "ban_until_ms": ban_until_ms,
                "terminal_state": turn.state.value,
            },
        )

    async def _receive_loop(self) -> None:
        async for message in self._channel.receive():
            if self._stopping:
                break
            self.dispatch_inbound(message)

    async def _receive_runtime_event_loop(self, index: int, runtime: IRuntime) -> None:
        while True:
            event = await runtime.receive_event()
            if self._stopping:
                return
            if isinstance(event, RuntimeBackgroundIdle):
                source = next(
                    (
                        runtime_session
                        for runtime_session in self._runtime_sessions.values()
                        if runtime_session.id == event.runtime_session_id
                        and runtime_session.runtime_index == index
                    ),
                    None,
                )
                if source is not None:
                    self._runtime_queues[source.bcn_session_id].put_nowait(event)
                continue
            source = next(
                (
                    runtime_session
                    for runtime_session in self._runtime_sessions.values()
                    if runtime_session.id == event.runtime_session_id
                    and runtime_session.runtime_index == index
                ),
                None,
            )
            if source is None or source.id in self._expired_runtime_ids:
                continue
            targets = tuple(
                runtime_session
                for runtime_session in self._runtime_sessions.values()
                if runtime_session.runtime_index == index
            )
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

    def _observe_background_task(
        self,
        name: str,
        task: asyncio.Task[None],
    ) -> None:
        task.add_done_callback(
            lambda completed: self._background_task_done(name, completed)
        )

    def _background_task_done(
        self,
        name: str,
        task: asyncio.Task[None],
    ) -> None:
        if self._stopping:
            return
        error = (
            RuntimeError(f"{name} was canceled unexpectedly")
            if task.cancelled()
            else task.exception() or RuntimeError(f"{name} stopped unexpectedly")
        )
        self._background_failures[name] = type(error).__name__
        self._logger.error(
            "%s failed: %s",
            name,
            error,
            exc_info=(type(error), error, error.__traceback__),
        )

    def _runtime_worker_done(
        self,
        session_id: str,
        queue: asyncio.Queue[_RuntimeQueueItem],
        task: asyncio.Task[None],
    ) -> None:
        if self._stopping or self._runtime_workers.get(session_id) is not task:
            return
        error = (
            RuntimeError("runtime worker was canceled unexpectedly")
            if task.cancelled()
            else task.exception() or RuntimeError("runtime worker stopped unexpectedly")
        )
        self._background_failures[f"runtime_worker:{session_id}"] = type(error).__name__
        self._logger.error(
            "runtime worker failed for session %s: %s",
            session_id,
            error,
            exc_info=(type(error), error, error.__traceback__),
        )
        self._start_runtime_worker(session_id, queue)

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

    def _upgrade_for(self, session_id: str) -> tuple[str, str] | None:
        """Return the offer this conversation has not been told about yet."""

        offer = self._upgrade_notice()
        if offer is None:
            self._session_upgrades.pop(session_id, None)
            return None
        version = offer[0]
        match self._session_upgrades.get(session_id):
            case UpgradeAnnounced(announced) if announced == version:
                return None
            case UpgradeAnnounced() | UpgradePending() | None:
                pass
        self._session_upgrades[session_id] = UpgradeAnnounced(version)
        return offer

    async def _run_notification(
        self,
        notification: _RuntimeNotification,
    ) -> RuntimeTurn | None:
        durable_context = notification.context
        message = notification.message
        client_user_message_id = notification.wake_id or message.message_id
        turn_id = f"turn-{client_user_message_id}"
        input_text = await self._notice_for(durable_context)
        if input_text is None:
            return None
        if await self._storage.get_runtime_attempt(turn_id) is not None:
            return self._runtime_turns.get(turn_id)

        attempted: set[int] = set()
        recorded_attempt = False
        while True:
            runtime_session = self.runtime_session(durable_context.bcn_session.id)
            context = (
                SessionContext(
                    durable_context.channel_session,
                    durable_context.bcn_session,
                    runtime_session,
                )
                if runtime_session is not None
                else self._create_runtime_session(
                    durable_context, exclude=frozenset(attempted)
                )
            )
            attempted.add(context.runtime_session.runtime_index)
            retry_available = len(attempted) < len(self._runtimes.all())
            context = await self._establish_runtime_session(context, durable_context)
            turn = await self._open_turn(
                context,
                turn_id=turn_id,
                client_user_message_id=client_user_message_id,
                recorded=recorded_attempt,
            )
            recorded_attempt = True

            finished = await self._run_turn_on_runtime(
                message,
                context,
                turn,
                input_text=input_text,
                attempted=attempted,
                retry_available=retry_available,
            )
            if finished is not None:
                return finished

    async def _run_turn_on_runtime(
        self,
        message: Message,
        context: SessionContext,
        turn: RuntimeTurn,
        *,
        input_text: str,
        attempted: set[int],
        retry_available: bool,
    ) -> RuntimeTurn | None:
        """Run a turn on the bound runtime, or nothing when another one took it over."""

        for unavailable_attempt in range(2):
            runtime_state = self._agent.get(context.bcn_session.id)
            if runtime_state is not State.IDLE:
                unknown = runtime_state is State.RECOVERING
                return await self._fail_turn(
                    message,
                    context,
                    turn,
                    "runtime session start outcome is unknown"
                    if unknown
                    else "runtime session failed to start",
                    unknown=unknown,
                    attempted=attempted,
                    retry_available=retry_available,
                )
            self._agent.started_turn(context.bcn_session.id)
            try:
                result = await self._turns.run_turn(
                    message,
                    context,
                    turn,
                    input_text=input_text,
                    retry_available=retry_available,
                )
                context, result = await self._recover_turn(
                    message,
                    context,
                    result,
                    retry_available=retry_available,
                )
                runtime_state = self._agent.get(context.bcn_session.id)
                if (
                    runtime_state is State.FAILED
                    and self.runtime_session(context.bcn_session.id)
                    is context.runtime_session
                ):
                    await self._stop_runtime_session(
                        context.runtime_session,
                        timeout=self._timeout_budget.provider_call_seconds,
                    )
                elif (
                    runtime_state not in {State.IDLE, State.WORKING}
                    and self.runtime_session(context.bcn_session.id) is not None
                ):
                    await self._discard_runtime_session(context.runtime_session)
                concluded = await self._conclude(message, context, result, attempted)
                if (
                    concluded is not None
                    and concluded.state is RuntimeTurnState.UNKNOWN
                ):
                    self._turns.notify_terminal(concluded, context.bcn_session.id)
                return concluded
            except RuntimeSessionUnavailable as error:
                self._agent.lost_runtime(context.bcn_session.id)
                if unavailable_attempt == 0:
                    (
                        context,
                        recovered_stream,
                    ) = await self._ensure_runtime_session_or_discard(context)
                    if recovered_stream is not None:
                        await recovered_stream.aclose()
                        raise RuntimeError(
                            "runtime retry unexpectedly recovered an active turn"
                        )
                    continue
                return await self._fail_turn(
                    message,
                    context,
                    turn,
                    str(error),
                    attempted=attempted,
                    retry_available=retry_available,
                )

        raise AssertionError("runtime turn loop did not return")

    async def _fail_turn(
        self,
        message: Message,
        context: SessionContext,
        turn: RuntimeTurn,
        error_message: str,
        *,
        unknown: bool = False,
        attempted: set[int],
        retry_available: bool,
    ) -> RuntimeTurn | None:
        """Close out a turn the runtime never carried, and pass it on if it can be."""

        finished = await self._turns.finish_turn(
            turn,
            RuntimeTurnState.UNKNOWN if unknown else RuntimeTurnState.FAILED,
            error_kind=(
                ErrorKind.PROVIDER_UNKNOWN if unknown else ErrorKind.PROVIDER_FAILED
            ),
            error_message=error_message,
            correlation=self._turns.turn_correlation(message, context, turn),
            session_id=context.bcn_session.id,
            retry_available=retry_available,
        )
        if unknown:
            self._abandon_runtime_session(context.runtime_session)
        await self._discard_runtime_session(context.runtime_session)
        return await self._conclude(message, context, finished, attempted)

    async def _conclude(
        self,
        message: Message,
        context: SessionContext,
        finished: RuntimeTurn,
        attempted: set[int],
    ) -> RuntimeTurn | None:
        """Settle a spent runtime's turn, unless a fresh runtime picks it up."""

        if await self._hand_turn_to_another_runtime(
            message, context, finished, attempted
        ):
            return None
        return finished

    async def _open_turn(
        self,
        context: SessionContext,
        *,
        turn_id: str,
        client_user_message_id: str,
        recorded: bool,
    ) -> RuntimeTurn:
        """Start a turn, recording the first attempt so a replay recognises it."""

        turn = RuntimeTurn(
            turn_id=turn_id,
            session_id=context.runtime_session.id,
            state=RuntimeTurnState.STARTING,
            started_at_ms=self._clock(),
            client_user_message_id=client_user_message_id,
        )
        if not recorded:
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
        return turn

    async def _notice_for(self, durable_context: _DurableSessionContext) -> str | None:
        """Compose what a turn would say, or nothing when nothing is unread."""

        cursor = await self._storage.get_consumer_cursor(durable_context.bcn_session.id)
        unread = await self._storage.list_messages(
            durable_context.bcn_session.id,
            after_seq=cursor.delivered_through_seq if cursor is not None else 0,
            direction=MessageDirection.INBOUND,
            notifying_only=True,
        )
        if not unread:
            return None
        upgrade = self._upgrade_for(durable_context.bcn_session.id)
        return inbox_notice(
            unread,
            total_unread_count=len(unread),
            closing_bracket_on_own_line=True,
            upgrade_version=upgrade[0] if upgrade is not None else None,
            installed_version=upgrade[1] if upgrade is not None else None,
        )

    async def _establish_runtime_session(
        self,
        context: SessionContext,
        durable_context: _DurableSessionContext,
    ) -> SessionContext:
        """Bring a session to rest, replacing the runtime once if it will not."""

        for attempt in range(2):
            context, recovered_stream = await self._ensure_runtime_session_or_discard(
                context
            )
            if recovered_stream is not None:
                await recovered_stream.aclose()
                raise RuntimeError(
                    "runtime establishment unexpectedly recovered an active turn"
                )
            if self._agent.get(context.bcn_session.id) is State.IDLE or attempt == 1:
                return context
            if self._agent.get(context.bcn_session.id) is State.RECOVERING:
                self._abandon_runtime_session(context.runtime_session)
            await self._discard_runtime_session(context.runtime_session)
            context = self._create_runtime_session(durable_context)
        return context

    async def _recover_turn(
        self,
        message: Message,
        context: SessionContext,
        result: RuntimeTurn,
        *,
        retry_available: bool,
    ) -> tuple[SessionContext, RuntimeTurn]:
        """Reconcile until the runtime says what became of a turn it lost."""

        while self._agent.get(context.bcn_session.id) is State.RECOVERING:
            context, recovered_stream = await self._ensure_runtime_session_or_discard(
                context,
                turn=result,
                approval_handler=self._turns.approval_handler(
                    message,
                    context,
                    result,
                ),
            )
            working = self._agent.get(context.bcn_session.id) is State.WORKING
            if recovered_stream is None or not working:
                if recovered_stream is not None:
                    await recovered_stream.aclose()
                if self._agent.get(context.bcn_session.id) is not State.IDLE:
                    await self._stop_runtime_session(
                        context.runtime_session,
                        timeout=self._timeout_budget.provider_call_seconds,
                    )
                return context, result
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
                retry_available=retry_available,
            )
        return context, result

    async def _confirm_runtime_session(
        self,
        context: SessionContext,
        runtime_session: RuntimeSession,
        provider_result: (
            ProviderCallResult[RuntimeSession]
            | ProviderCallResult[RuntimeSessionReconciliation]
        ),
        *,
        operation: str,
        correlation: CorrelationContext,
    ) -> tuple[RuntimeSession, IRuntimeTurnStream | None]:
        """Take what the runtime confirmed, once it is the session we asked about."""

        recovered_stream: IRuntimeTurnStream | None = None
        confirmed_value = provider_result.value
        if confirmed_value is None:
            raise ValueError("confirmed runtime operation has no result")
        if operation == "start":
            if not isinstance(confirmed_value, RuntimeSession):
                raise TypeError("runtime start returned an invalid result")
            updated_runtime = confirmed_value
        else:
            if not isinstance(confirmed_value, RuntimeSessionReconciliation):
                raise TypeError("runtime reconcile returned an invalid result")
            recovered_stream = confirmed_value.stream
            updated_runtime = confirmed_value.session
        if (
            updated_runtime.id != runtime_session.id
            or updated_runtime.bcn_session_id != context.bcn_session.id
            or updated_runtime.channel_session_id != context.channel_session.id
            or updated_runtime.runtime != runtime_session.runtime
            or updated_runtime.runtime_index != runtime_session.runtime_index
            or updated_runtime.workspace_id != self.agent_id
            or updated_runtime.created_at_ms != runtime_session.created_at_ms
        ):
            raise ValueError("runtime provider returned a mismatched session")
        runtime_session = updated_runtime
        if recovered_stream is not None:
            self._agent.started_turn(context.bcn_session.id)
        else:
            self._agent.finished_turn(context.bcn_session.id)
        await self._audit.append(
            event_name=(
                "runtime.process.started"
                if operation == "start"
                else "runtime.process.reconciled"
            ),
            state=RuntimeEventState.COMPLETED,
            correlation=replace(
                correlation,
                provider_thread_id=runtime_session.provider_thread_id,
            ),
            metadata={
                "runtime": runtime_session.runtime,
                "workspace_id": runtime_session.workspace_id,
                **(
                    {"recovered_turn": recovered_stream is not None}
                    if operation == "reconcile"
                    else {}
                ),
            },
        )
        return runtime_session, recovered_stream

    async def _record_runtime_loss(
        self,
        context: SessionContext,
        runtime_session: RuntimeSession,
        provider_result: (
            ProviderCallResult[RuntimeSession]
            | ProviderCallResult[RuntimeSessionReconciliation]
        ),
        *,
        operation: str,
        correlation: CorrelationContext,
    ) -> None:
        """Write down how the runtime failed, and leave the Agent saying which."""

        if provider_result.status is ProviderCallStatus.FAILED:
            self._agent.refused_runtime(context.bcn_session.id)
        else:
            self._agent.lost_runtime(context.bcn_session.id)
        await self._audit.append(
            event_name=f"runtime.process.{provider_result.status.value}",
            state=(
                RuntimeEventState.FAILED
                if provider_result.status is ProviderCallStatus.FAILED
                else RuntimeEventState.UNKNOWN
            ),
            correlation=correlation,
            error_kind=(
                ErrorKind(provider_result.error_kind)
                if provider_result.error_kind in ErrorKind._value2member_map_
                else ErrorKind.INTERNAL
            ),
            error_message=provider_result.error_message,
            metadata={
                "operation": operation,
                "runtime": runtime_session.runtime,
                "workspace_id": runtime_session.workspace_id,
            },
        )

    async def _ensure_runtime_session(
        self,
        context: SessionContext,
        *,
        turn: RuntimeTurn | None,
        approval_handler: IApprovalHandler | None,
    ) -> tuple[SessionContext, IRuntimeTurnStream | None]:
        runtime_session = context.runtime_session
        recovered_stream: IRuntimeTurnStream | None = None
        # a session that lost sync has to be reconciled before anything else,
        # and one whose runtime never confirmed a thread has to be started
        if self._agent.get(context.bcn_session.id) is State.RECOVERING:
            process_operation = "reconcile"
        elif runtime_session.provider_thread_id is None:
            process_operation = "start"
        else:
            return context, None
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

        runtime = self._runtimes.get(runtime_session.runtime_index)
        if process_operation == "start":
            provider_result = await runtime.start_session(
                runtime_session,
                timeout=self._timeout_budget.provider_call_seconds,
            )
        else:
            provider_result = await runtime.reconcile_session(
                runtime_session,
                turn,
                approval_handler,
                timeout=self._timeout_budget.startup_seconds,
            )

        if provider_result.status is ProviderCallStatus.CONFIRMED:
            runtime_session, recovered_stream = await self._confirm_runtime_session(
                context,
                runtime_session,
                provider_result,
                operation=process_operation,
                correlation=process_correlation,
            )
        else:
            await self._record_runtime_loss(
                context,
                runtime_session,
                provider_result,
                operation=process_operation,
                correlation=process_correlation,
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

    def _abandon_runtime_session(self, runtime_session: RuntimeSession) -> None:
        process_correlation = CorrelationContext(
            node_id=self.agent_id,
            channel_session_id=runtime_session.channel_session_id,
            bcn_session_id=runtime_session.bcn_session_id,
            runtime_session_id=runtime_session.id,
            provider_thread_id=runtime_session.provider_thread_id,
        )
        task = asyncio.create_task(
            self._complete_abandoned_runtime_session_teardown(
                runtime_session,
                correlation=process_correlation,
            ),
            name=f"bcn-runtime-teardown-{runtime_session.id}",
        )
        self._runtime_teardown_tasks.add(task)
        task.add_done_callback(self._forget_runtime_teardown_task)

    async def _complete_abandoned_runtime_session_teardown(
        self,
        runtime_session: RuntimeSession,
        *,
        correlation: CorrelationContext,
    ) -> None:
        await self._audit.append(
            event_name="runtime.process.stop.requested",
            state=RuntimeEventState.STARTED,
            correlation=correlation,
            metadata={
                "runtime": runtime_session.runtime,
                "workspace_id": runtime_session.workspace_id,
            },
        )
        await self._complete_runtime_session_teardown(
            runtime_session,
            correlation=correlation,
            timeout=self._timeout_budget.provider_call_seconds,
        )

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
            result = await self._runtimes.get(
                runtime_session.runtime_index
            ).stop_session(runtime_session, timeout=timeout)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            result = None
            stop_message = format_exception(error)
        else:
            stop_message = ""
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
        error_message = result.error_message if result is not None else stop_message
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
        self._schedule_runtime_session_teardown(
            runtime_session,
            correlation=process_correlation,
            timeout=timeout,
        )
        await self._discard_runtime_session(runtime_session)
