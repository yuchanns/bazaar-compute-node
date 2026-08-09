from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
from time import time_ns

from ..audit import ErrorKind
from ..channel import IChannel
from ..command import SessionNotFoundError
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
    ChannelSessionState,
    ChannelTargetKind,
    ConsumerCursor,
    InboundMessage,
    RuntimeEventState,
    RuntimeProcessState,
    RuntimeSession,
    RuntimeTurn,
    RuntimeTurnState,
)
from ..observability import IAudit
from ..outcomes import ProviderCallStatus
from ..runtime import IRuntime
from ..storage import IStorage, NodeIdentity
from .command import SessionCommandService
from .services import SessionAuditRecorder, SessionStateWriter
from .turn import SessionContext, SessionTurnCoordinator


def _current_time_ms() -> int:
    return time_ns() // 1_000_000


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
        self._node_id = node_id
        self._workspace_id = workspace_id
        self._on_node_initialized = on_node_initialized
        self._channel = channel
        self._runtime = runtime
        self._storage = storage
        self._timeout_budget = timeout_budget
        self._concurrency = concurrency or SessionLockRegistry()
        self._clock = clock or _current_time_ms
        self._runtime_sessions: dict[str, RuntimeSession] = {}
        self._bcn_sessions: dict[str, BcnSession] = {}
        self._audit = SessionAuditRecorder(
            sink=audit,
            timeout_budget=timeout_budget,
            clock=self._clock,
        )
        self._state_writer = SessionStateWriter(
            storage=storage,
            concurrency=self._concurrency,
            bcn_sessions=self._bcn_sessions,
        )
        self._command_service = SessionCommandService(
            channel=channel,
            storage=storage,
            audit=self._audit,
            provider_call_timeout=timeout_budget.provider_call_seconds,
            concurrency=self._concurrency,
            node_id=lambda: self.node_id,
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
            node_id=lambda: self.node_id,
            clock=self._clock,
        )
        self._active_tasks: set[asyncio.Task[RuntimeTurn | None]] = set()
        self._turn_in_flight: dict[str, asyncio.Event] = {}
        self._receive_task: asyncio.Task[None] | None = None
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

        for runtime_session in tuple(self._runtime_sessions.values()):
            if runtime_session.process_state not in {
                RuntimeProcessState.RUNNING,
                RuntimeProcessState.STARTING,
                RuntimeProcessState.RECONCILING,
            }:
                continue
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

    async def tick(self, session_id: str, tick: AgentTick) -> BcnSession:
        """Apply one serialized lifecycle observation to a bcn session."""

        if not session_id:
            raise ValueError("session_id must be a non-empty string")
        return await self._state_writer.apply(session_id, tick)

    async def handle_inbound(self, message: InboundMessage) -> RuntimeTurn | None:
        """Persist one inbound message and drive one runtime turn to an outcome."""

        lock = self._concurrency.for_session(message.session_id)
        finish_state: RuntimeTurnState | None = None
        finish_error_kind: ErrorKind | None = None
        finish_error_message: str | None = None
        context: SessionContext | None = None
        turn: RuntimeTurn | None = None
        while True:
            wait_for: asyncio.Event | None = None
            async with lock:
                if turn is None:
                    completion = self._turn_in_flight.get(message.session_id)
                    (
                        context,
                        message,
                        pending_turn,
                        turn_created,
                    ) = await self._record_inbound_and_turn(
                        message,
                        create_turn=completion is None,
                    )
                    if completion is not None:
                        if not message.notifies_runtime:
                            return None
                        if pending_turn is not None and pending_turn.state in {
                            RuntimeTurnState.COMPLETED,
                            RuntimeTurnState.FAILED,
                            RuntimeTurnState.CANCELLED,
                            RuntimeTurnState.UNKNOWN,
                        }:
                            return pending_turn
                        wait_for = completion
                    else:
                        turn = pending_turn
                        if turn is None:
                            return None
                        if not turn_created or turn.state in {
                            RuntimeTurnState.COMPLETED,
                            RuntimeTurnState.FAILED,
                            RuntimeTurnState.CANCELLED,
                            RuntimeTurnState.UNKNOWN,
                        }:
                            return turn

                if wait_for is None:
                    if context is None or turn is None:
                        raise RuntimeError("inbound turn context is not initialized")
                    completion = self._turn_in_flight.get(message.session_id)
                    if completion is not None:
                        wait_for = completion
                    else:
                        context = await self._ensure_runtime_session(context)
                        if (
                            context.runtime_session.process_state
                            is not RuntimeProcessState.RUNNING
                        ):
                            finish_state = (
                                RuntimeTurnState.UNKNOWN
                                if context.runtime_session.process_state
                                is RuntimeProcessState.UNKNOWN
                                else RuntimeTurnState.FAILED
                            )
                            finish_error_kind = (
                                ErrorKind.PROVIDER_UNKNOWN
                                if finish_state is RuntimeTurnState.UNKNOWN
                                else ErrorKind.PROVIDER_FAILED
                            )
                            finish_error_message = (
                                "runtime session start outcome is unknown"
                                if finish_state is RuntimeTurnState.UNKNOWN
                                else "runtime session failed to start"
                            )
                            self._turn_in_flight[message.session_id] = asyncio.Event()
                            break
                        bcn_session = await self._state_writer.apply_locked(
                            message.session_id,
                            AgentTick(
                                source=AgentTickSource.CHANNEL,
                                signal=AgentSignal.TURN_STARTED,
                                observed_at_ms=self._clock(),
                            ),
                        )
                        context = replace(context, bcn_session=bcn_session)
                        self._turn_in_flight[message.session_id] = asyncio.Event()
                        break
            if wait_for is not None:
                await wait_for.wait()
                context = None

        if context is None or turn is None:
            raise RuntimeError("inbound turn context is not initialized")

        try:
            if finish_state is not None:
                return await self._turns.finish_turn(
                    turn,
                    finish_state,
                    error_kind=finish_error_kind,
                    error_message=finish_error_message,
                    correlation=self._turns.turn_correlation(message, context, turn),
                    session_id=context.bcn_session.id,
                )
            return await self._turns.run_turn(message, context, turn)
        finally:
            async with lock:
                completion = self._turn_in_flight.pop(message.session_id, None)
                if completion is not None:
                    completion.set()

    async def _receive_loop(self) -> None:
        async for message in self._channel.receive():
            if self._stopping:
                break
            self.dispatch_inbound(message)

    def _forget_task(self, task: asyncio.Task[RuntimeTurn | None]) -> None:
        self._active_tasks.discard(task)
        if not task.cancelled():
            task.exception()

    async def _record_inbound_and_turn(
        self,
        message: InboundMessage,
        *,
        create_turn: bool,
    ) -> tuple[SessionContext | None, InboundMessage, RuntimeTurn | None, bool]:
        context: SessionContext | None = None
        async with self._storage.transaction() as transaction:
            existing_message = await transaction.find_inbound_message(
                message.channel, message.provider_message_id
            )
            if existing_message is not None:
                if (
                    existing_message.session_id != message.session_id
                    or existing_message.channel_session_id != message.channel_session_id
                ):
                    raise ValueError(
                        "provider message id is already bound to another session"
                    )
                message = existing_message
            channel_session = await transaction.find_channel_session(
                channel=message.channel,
                provider_conversation_key=message.channel_session_id,
                provider_thread_key=message.provider_thread_id or "",
            )
            now_ms = self._clock()
            if channel_session is None:
                channel_session = ChannelSession(
                    id=message.channel_session_id,
                    channel=message.channel,
                    provider_conversation_key=message.channel_session_id,
                    provider_thread_key=message.provider_thread_id or "",
                    state=ChannelSessionState.ACTIVE,
                    created_at_ms=now_ms,
                    updated_at_ms=now_ms,
                    target_kind=message.target_kind,
                    following=message.target_kind is ChannelTargetKind.DM
                    or message.mentions_agent,
                )
                await transaction.save_channel_session(channel_session)
            elif channel_session.id != message.channel_session_id:
                raise ValueError("inbound channel session identity does not match")
            elif channel_session.target_kind is not message.target_kind:
                raise ValueError("inbound channel target kind does not match")
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

            if existing_message is None:
                notifies_runtime = (
                    message.target_kind is ChannelTargetKind.DM
                    or channel_session.following
                    or message.mentions_agent
                )
                message = replace(message, notifies_runtime=notifies_runtime)

            bcn_session = await transaction.get_bcn_session(message.session_id)
            if bcn_session is None:
                bcn_session = BcnSession(
                    id=message.session_id,
                    channel_session_id=channel_session.id,
                    workspace_id=self.workspace_id,
                    state=AgentState.CREATED,
                    created_at_ms=now_ms,
                    updated_at_ms=now_ms,
                )
                await transaction.save_bcn_session(bcn_session)
            elif (
                bcn_session.channel_session_id != channel_session.id
                or bcn_session.workspace_id != self.workspace_id
            ):
                raise ValueError("inbound bcn session binding does not match")

            cursor = await transaction.get_consumer_cursor(message.session_id)
            if cursor is None:
                await transaction.save_consumer_cursor(
                    ConsumerCursor(session_id=message.session_id)
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

            runtime_session: RuntimeSession | None = None
            if message.notifies_runtime:
                runtime_id = f"runtime-{message.session_id}"
                runtime_session = await transaction.get_runtime_session(runtime_id)
                if runtime_session is None:
                    runtime_session = RuntimeSession(
                        id=runtime_id,
                        bcn_session_id=bcn_session.id,
                        channel_session_id=channel_session.id,
                        runtime=self._runtime.name,
                        workspace_id=self.workspace_id,
                        process_state=RuntimeProcessState.STARTING,
                        created_at_ms=now_ms,
                        updated_at_ms=now_ms,
                    )
                    await transaction.save_runtime_session(runtime_session)
                elif (
                    runtime_session.bcn_session_id != bcn_session.id
                    or runtime_session.channel_session_id != channel_session.id
                    or runtime_session.workspace_id != self.workspace_id
                ):
                    raise ValueError("runtime session binding does not match")
                context = SessionContext(channel_session, bcn_session, runtime_session)

            turn_id = f"turn-{message.message_id}"
            existing_turn = await transaction.get_runtime_turn(turn_id)
            if existing_turn is not None:
                turn = existing_turn
                turn_created = False
            elif not create_turn or not message.notifies_runtime:
                turn = None
                turn_created = False
            else:
                if runtime_session is None:
                    raise RuntimeError("notifying inbound has no runtime session")
                turn = RuntimeTurn(
                    turn_id=turn_id,
                    session_id=runtime_session.id,
                    state=RuntimeTurnState.STARTING,
                    started_at_ms=now_ms,
                    client_user_message_id=message.message_id,
                )
                await transaction.save_runtime_turn(turn)
                turn_created = True

        self._bcn_sessions[bcn_session.id] = bcn_session
        if runtime_session is not None:
            self._runtime_sessions[runtime_session.id] = runtime_session
        return context, message, turn, turn_created

    async def _ensure_runtime_session(self, context: SessionContext) -> SessionContext:
        runtime_session = context.runtime_session
        bcn_session = context.bcn_session
        if runtime_session.process_state is RuntimeProcessState.RUNNING:
            if bcn_session.state is AgentState.CREATED:
                bcn_session = await self._state_writer.apply_locked(
                    bcn_session.id,
                    AgentTick(
                        source=AgentTickSource.SESSION,
                        signal=AgentSignal.START_REQUESTED,
                        observed_at_ms=self._clock(),
                    ),
                )
            if bcn_session.state is AgentState.STARTING:
                bcn_session = await self._state_writer.apply_locked(
                    bcn_session.id,
                    AgentTick(
                        source=AgentTickSource.RUNTIME,
                        signal=AgentSignal.START_CONFIRMED,
                        observed_at_ms=self._clock(),
                    ),
                )
            elif bcn_session.state is AgentState.RECONCILING:
                bcn_session = await self._state_writer.apply_locked(
                    bcn_session.id,
                    AgentTick(
                        source=AgentTickSource.RECOVERY,
                        signal=AgentSignal.RECONCILE_CONFIRMED,
                        observed_at_ms=self._clock(),
                    ),
                )
            return replace(context, bcn_session=bcn_session)
        if runtime_session.process_state not in {
            RuntimeProcessState.STARTING,
            RuntimeProcessState.UNKNOWN,
            RuntimeProcessState.RECONCILING,
        }:
            return context
        process_operation = (
            "start"
            if runtime_session.process_state is RuntimeProcessState.STARTING
            else "resume"
        )
        process_correlation = CorrelationContext(
            node_id=self.node_id,
            channel=context.channel_session.channel,
            channel_session_id=context.channel_session.id,
            bcn_session_id=bcn_session.id,
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

        if runtime_session.process_state is RuntimeProcessState.STARTING:
            if bcn_session.state is AgentState.CREATED:
                bcn_session = await self._state_writer.apply_locked(
                    bcn_session.id,
                    AgentTick(
                        source=AgentTickSource.SESSION,
                        signal=AgentSignal.START_REQUESTED,
                        observed_at_ms=self._clock(),
                    ),
                )
            provider_result = await self._runtime.start_session(
                runtime_session,
                timeout=self._timeout_budget.provider_call_seconds,
            )
        elif runtime_session.process_state is RuntimeProcessState.UNKNOWN:
            if bcn_session.state not in {
                AgentState.UNKNOWN,
                AgentState.RECONCILING,
            }:
                bcn_session = await self._state_writer.apply_locked(
                    bcn_session.id,
                    AgentTick(
                        source=AgentTickSource.RUNTIME,
                        signal=AgentSignal.UNKNOWN,
                        observed_at_ms=self._clock(),
                    ),
                )
            if bcn_session.state is AgentState.UNKNOWN:
                bcn_session = await self._state_writer.apply_locked(
                    bcn_session.id,
                    AgentTick(
                        source=AgentTickSource.RECOVERY,
                        signal=AgentSignal.RECONCILE_REQUESTED,
                        observed_at_ms=self._clock(),
                    ),
                )
            runtime_session = runtime_session.transition_process_to(
                RuntimeProcessState.RECONCILING,
                updated_at_ms=self._clock(),
            )
            async with self._storage.transaction() as transaction:
                await transaction.save_runtime_session(runtime_session)
            provider_result = await self._runtime.resume_session(
                runtime_session,
                timeout=self._timeout_budget.provider_call_seconds,
            )
        elif runtime_session.process_state is RuntimeProcessState.RECONCILING:
            if bcn_session.state is AgentState.UNKNOWN:
                bcn_session = await self._state_writer.apply_locked(
                    bcn_session.id,
                    AgentTick(
                        source=AgentTickSource.RECOVERY,
                        signal=AgentSignal.RECONCILE_REQUESTED,
                        observed_at_ms=self._clock(),
                    ),
                )
            provider_result = await self._runtime.resume_session(
                runtime_session,
                timeout=self._timeout_budget.provider_call_seconds,
            )
        else:
            raise RuntimeError("unsupported runtime process state")

        now_ms = self._clock()
        if provider_result.status is ProviderCallStatus.CONFIRMED:
            updated_runtime = provider_result.value
            if updated_runtime is None:
                raise ValueError("confirmed runtime start has no session")
            if (
                updated_runtime.bcn_session_id != context.bcn_session.id
                or updated_runtime.channel_session_id != context.channel_session.id
                or updated_runtime.workspace_id != self.workspace_id
            ):
                raise ValueError("runtime provider returned a mismatched session")
            runtime_session = updated_runtime
            async with self._storage.transaction() as transaction:
                current_bcn = await transaction.get_bcn_session(context.bcn_session.id)
                if current_bcn is None:
                    raise SessionNotFoundError(
                        f"unknown bcn session: {context.bcn_session.id}"
                    )
                if current_bcn.state is AgentState.STARTING:
                    current_bcn = await self._state_writer.apply_in_transaction(
                        transaction,
                        current_bcn,
                        AgentTick(
                            source=AgentTickSource.RUNTIME,
                            signal=AgentSignal.START_CONFIRMED,
                            observed_at_ms=now_ms,
                        ),
                    )
                elif current_bcn.state is AgentState.RECONCILING:
                    current_bcn = await self._state_writer.apply_in_transaction(
                        transaction,
                        current_bcn,
                        AgentTick(
                            source=AgentTickSource.RECOVERY,
                            signal=AgentSignal.RECONCILE_CONFIRMED,
                            observed_at_ms=now_ms,
                        ),
                    )
                await transaction.save_runtime_session(runtime_session)
                bcn_session = current_bcn
            await self._audit.append(
                event_name=(
                    "runtime.process.started"
                    if process_operation == "start"
                    else "runtime.process.resumed"
                ),
                state=RuntimeEventState.COMPLETED,
                correlation=replace(
                    process_correlation,
                    provider_thread_id=runtime_session.provider_thread_id,
                ),
                metadata={
                    "runtime": runtime_session.runtime,
                    "workspace_id": runtime_session.workspace_id,
                },
            )
        else:
            target_state = (
                RuntimeProcessState.FAILED
                if provider_result.status is ProviderCallStatus.FAILED
                else RuntimeProcessState.UNKNOWN
            )
            runtime_session = runtime_session.transition_process_to(
                target_state,
                updated_at_ms=now_ms,
                error_kind=provider_result.error_kind,
                error_message=provider_result.error_message,
            )
            async with self._storage.transaction() as transaction:
                current_bcn = await transaction.get_bcn_session(context.bcn_session.id)
                if current_bcn is None:
                    raise SessionNotFoundError(
                        f"unknown bcn session: {context.bcn_session.id}"
                    )
                current_bcn = await self._state_writer.apply_in_transaction(
                    transaction,
                    current_bcn,
                    AgentTick(
                        source=AgentTickSource.RUNTIME,
                        signal=(
                            AgentSignal.FAILED
                            if target_state is RuntimeProcessState.FAILED
                            else AgentSignal.UNKNOWN
                        ),
                        observed_at_ms=now_ms,
                        error_kind=provider_result.error_kind,
                        error_message=provider_result.error_message,
                    ),
                )
                await transaction.save_runtime_session(runtime_session)
                bcn_session = current_bcn
            await self._audit.append(
                event_name=f"runtime.process.{target_state.value}",
                state=(
                    RuntimeEventState.FAILED
                    if target_state is RuntimeProcessState.FAILED
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

        self._runtime_sessions[runtime_session.id] = runtime_session
        self._bcn_sessions[bcn_session.id] = bcn_session
        return SessionContext(context.channel_session, bcn_session, runtime_session)

    async def _stop_runtime_session(
        self, runtime_session: RuntimeSession, *, timeout: float
    ) -> None:
        async with self._concurrency.for_session(runtime_session.bcn_session_id):
            await self._stop_runtime_session_locked(runtime_session, timeout=timeout)

    async def _stop_runtime_session_locked(
        self, runtime_session: RuntimeSession, *, timeout: float
    ) -> None:
        now_ms = self._clock()
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
        stopping = runtime_session.transition_process_to(
            RuntimeProcessState.STOPPING,
            updated_at_ms=now_ms,
        )
        async with self._storage.transaction() as transaction:
            bcn_session = await transaction.get_bcn_session(
                runtime_session.bcn_session_id
            )
            if bcn_session is None:
                raise SessionNotFoundError(
                    f"unknown bcn session: {runtime_session.bcn_session_id}"
                )
            bcn_session = await self._state_writer.apply_in_transaction(
                transaction,
                bcn_session,
                AgentTick(
                    source=AgentTickSource.SESSION,
                    signal=AgentSignal.STOP_REQUESTED,
                    observed_at_ms=now_ms,
                ),
            )
            await transaction.save_runtime_session(stopping)
        try:
            result = await self._runtime.stop_session(stopping, timeout=timeout)
        except Exception as error:  # noqa: BLE001
            result = None
            stop_error = error
        else:
            stop_error = None
        if result is not None and result.status is ProviderCallStatus.CONFIRMED:
            stopped = result.value
            if stopped is None:
                raise ValueError("confirmed runtime stop has no session")
        else:
            state = (
                RuntimeProcessState.UNKNOWN
                if result is None
                or result.status
                in {ProviderCallStatus.UNKNOWN, ProviderCallStatus.QUEUED}
                else RuntimeProcessState.FAILED
            )
            stopped = stopping.transition_process_to(
                state,
                updated_at_ms=self._clock(),
                error_kind=(
                    result.error_kind
                    if result is not None
                    else ErrorKind.PROVIDER_UNKNOWN.value
                ),
                error_message=(
                    result.error_message if result is not None else str(stop_error)
                ),
            )
        self._runtime_sessions[stopped.id] = stopped
        async with self._storage.transaction() as transaction:
            bcn_session = await transaction.get_bcn_session(stopped.bcn_session_id)
            if bcn_session is None:
                raise SessionNotFoundError(
                    f"unknown bcn session: {stopped.bcn_session_id}"
                )
            agent_signal = (
                AgentSignal.STOP_CONFIRMED
                if result is not None and result.status is ProviderCallStatus.CONFIRMED
                else AgentSignal.UNKNOWN
                if stopped.process_state is RuntimeProcessState.UNKNOWN
                else AgentSignal.FAILED
            )
            await self._state_writer.apply_in_transaction(
                transaction,
                bcn_session,
                AgentTick(
                    source=AgentTickSource.SESSION,
                    signal=agent_signal,
                    observed_at_ms=self._clock(),
                    error_kind=(
                        result.error_kind
                        if result is not None
                        else ErrorKind.PROVIDER_UNKNOWN.value
                    )
                    if agent_signal is not AgentSignal.STOP_CONFIRMED
                    else None,
                    error_message=(
                        result.error_message if result is not None else str(stop_error)
                    )
                    if agent_signal is not AgentSignal.STOP_CONFIRMED
                    else None,
                ),
            )
            await transaction.save_runtime_session(stopped)
        await self._audit.append(
            event_name=(
                "runtime.process.stopped"
                if stopped.process_state is RuntimeProcessState.STOPPED
                else f"runtime.process.{stopped.process_state.value}"
            ),
            state=(
                RuntimeEventState.COMPLETED
                if stopped.process_state is RuntimeProcessState.STOPPED
                else RuntimeEventState.UNKNOWN
                if stopped.process_state is RuntimeProcessState.UNKNOWN
                else RuntimeEventState.FAILED
            ),
            correlation=process_correlation,
            error_kind=(
                ErrorKind(stopped.last_error_kind)
                if stopped.last_error_kind in ErrorKind._value2member_map_
                else ErrorKind.INTERNAL
                if stopped.last_error_message
                else None
            ),
            error_message=stopped.last_error_message,
            metadata={
                "runtime": stopped.runtime,
                "workspace_id": stopped.workspace_id,
            },
        )
