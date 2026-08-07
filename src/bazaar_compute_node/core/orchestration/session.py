from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from time import time_ns

from ..approval import ApprovalBinding
from ..audit import AuditEvent, ErrorKind
from ..channel import IChannel
from ..command import (
    ICommandService,
    MessageCheckResult,
    MessageReadResult,
    SessionNotFoundError,
)
from ..concurrency import ISessionConcurrency, SessionLockRegistry
from ..correlation import CorrelationContext
from ..lifecycle import IAsyncLifecycle, TimeoutBudget
from ..models import (
    ApprovalRequest,
    ApprovalResult,
    BcnSession,
    BcnSessionState,
    ChannelSession,
    ChannelSessionState,
    ConsumerCursor,
    FreshCheckState,
    InboundMessage,
    OutboundDeliveryState,
    OutboundMessage,
    RuntimeEvent,
    RuntimeEventState,
    RuntimeProcessState,
    RuntimeSession,
    RuntimeTurn,
    RuntimeTurnState,
)
from ..observability import IAudit, LogLevel
from ..outcomes import ProviderCallStatus
from ..runtime import IRuntime, IRuntimeTurnStream
from ..storage import IStorage, NodeIdentity


def _current_time_ms() -> int:
    return time_ns() // 1_000_000


@dataclass(frozen=True, slots=True)
class _SessionContext:
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


class SessionOrchestrator(ICommandService, IAsyncLifecycle):
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
        runtime_slug: str = "dummy",
        concurrency: ISessionConcurrency | None = None,
        clock: Callable[[], int] | None = None,
        on_node_initialized: Callable[[NodeIdentity], Awaitable[None]] | None = None,
    ) -> None:
        for value, field_name in (
            (node_id, "node_id"),
            (runtime_slug, "runtime_slug"),
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
        self._audit_sink = audit
        self._timeout_budget = timeout_budget
        self._runtime_slug = runtime_slug
        self._concurrency = concurrency or SessionLockRegistry()
        self._clock = clock or _current_time_ms
        self._runtime_sessions: dict[str, RuntimeSession] = {}
        self._bcn_sessions: dict[str, BcnSession] = {}
        self._active_tasks: set[asyncio.Task[RuntimeTurn]] = set()
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

    def dispatch_inbound(self, message: InboundMessage) -> asyncio.Task[RuntimeTurn]:
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

    async def handle_inbound(self, message: InboundMessage) -> RuntimeTurn:
        """Persist one inbound message and drive one runtime turn to an outcome."""

        lock = self._concurrency.for_session(message.bcn_session_id)
        while True:
            async with lock:
                completion = self._turn_in_flight.get(message.bcn_session_id)
                if completion is not None:
                    pass
                else:
                    context = await self._prepare_session(message)
                    message, turn, is_new = await self._record_inbound_and_turn(
                        message, context
                    )
                    if not is_new or turn.state in {
                        RuntimeTurnState.COMPLETED,
                        RuntimeTurnState.FAILED,
                        RuntimeTurnState.CANCELLED,
                        RuntimeTurnState.UNKNOWN,
                    }:
                        return turn

                    context = await self._ensure_runtime_session(context)
                    if (
                        context.runtime_session.process_state
                        is not RuntimeProcessState.RUNNING
                    ):
                        if (
                            context.runtime_session.process_state
                            is RuntimeProcessState.UNKNOWN
                        ):
                            return await self._finish_turn(
                                turn,
                                RuntimeTurnState.UNKNOWN,
                                error_kind=ErrorKind.PROVIDER_UNKNOWN,
                                error_message="runtime session start outcome is unknown",
                                correlation=self._turn_correlation(
                                    message, context, turn
                                ),
                            )
                        return await self._finish_turn(
                            turn,
                            RuntimeTurnState.FAILED,
                            error_kind=ErrorKind.PROVIDER_FAILED,
                            error_message="runtime session failed to start",
                            correlation=self._turn_correlation(message, context, turn),
                        )
                    completion = asyncio.Event()
                    self._turn_in_flight[message.bcn_session_id] = completion
                    break
            await completion.wait()

        try:
            return await self._run_turn(message, context, turn)
        finally:
            async with lock:
                completion = self._turn_in_flight.pop(message.bcn_session_id, None)
                if completion is not None:
                    completion.set()

    async def check(self, bcn_session_id: str, *, timeout: float) -> MessageCheckResult:
        async with self._concurrency.for_session(bcn_session_id):
            async with self._storage.transaction() as transaction:
                if await transaction.get_bcn_session(bcn_session_id) is None:
                    raise SessionNotFoundError(f"unknown bcn session: {bcn_session_id}")
                cursor = await transaction.get_consumer_cursor(bcn_session_id)
                if cursor is None:
                    cursor = ConsumerCursor(bcn_session_id=bcn_session_id)
                latest_seq = await transaction.get_latest_inbound_seq(bcn_session_id)
                messages = await transaction.list_inbound_messages(
                    bcn_session_id,
                    after_seq=cursor.delivered_through_seq,
                )
                now_ms = self._clock()
                cursor = replace(
                    cursor,
                    delivered_through_seq=latest_seq,
                    inbox_snapshot_seq=latest_seq,
                    inbox_snapshot_source="check",
                    inbox_snapshot_at_ms=now_ms,
                    last_check_at_ms=now_ms,
                    updated_at_ms=now_ms,
                )
                await transaction.save_consumer_cursor(cursor)
            return MessageCheckResult(
                messages=messages,
                snapshot_seq=latest_seq,
                delivered_through_seq=latest_seq,
            )

    async def read(
        self,
        bcn_session_id: str,
        *,
        target: str,
        around_message_id: str | None = None,
        limit: int = 100,
        timeout: float,
    ) -> MessageReadResult:
        if not target:
            raise ValueError("target must be a non-empty string")
        if limit <= 0:
            raise ValueError("limit must be positive")
        async with self._concurrency.for_session(bcn_session_id):
            async with self._storage.transaction() as transaction:
                if await transaction.get_bcn_session(bcn_session_id) is None:
                    raise SessionNotFoundError(f"unknown bcn session: {bcn_session_id}")
                messages = await transaction.list_inbound_messages(
                    bcn_session_id,
                    target=target,
                    around_message_id=around_message_id,
                    limit=limit,
                )
                latest_seq = await transaction.get_latest_inbound_seq(bcn_session_id)
                cursor = await transaction.get_consumer_cursor(bcn_session_id)
                if cursor is None:
                    cursor = ConsumerCursor(bcn_session_id=bcn_session_id)
                now_ms = self._clock()
                cursor = replace(
                    cursor,
                    inbox_snapshot_seq=latest_seq,
                    inbox_snapshot_source="read",
                    inbox_snapshot_at_ms=now_ms,
                    last_read_at_ms=now_ms,
                    updated_at_ms=now_ms,
                )
                await transaction.save_consumer_cursor(cursor)
            return MessageReadResult(
                messages=messages,
                snapshot_seq=latest_seq,
                first_seq=messages[0].seq if messages else None,
                last_seq=messages[-1].seq if messages else None,
            )

    async def send(
        self,
        *,
        bcn_session_id: str,
        command_id: str,
        target: str,
        body: str,
        created_at_ms: int,
        timeout: float,
    ) -> OutboundMessage:
        if not command_id:
            raise ValueError("command_id must be a non-empty string")
        if not target:
            raise ValueError("target must be a non-empty string")
        outbound_id = f"outbound-{bcn_session_id}-{command_id}"
        async with self._concurrency.for_session(bcn_session_id):
            async with self._storage.transaction() as transaction:
                bcn_session = await transaction.get_bcn_session(bcn_session_id)
                if bcn_session is None:
                    raise SessionNotFoundError(f"unknown bcn session: {bcn_session_id}")
                channel_session = await transaction.get_channel_session(
                    bcn_session.channel_session_id
                )
                if channel_session is None:
                    raise ValueError(
                        f"unknown channel session: {bcn_session.channel_session_id}"
                    )
                cursor = await transaction.get_consumer_cursor(bcn_session_id)
                if cursor is None:
                    cursor = ConsumerCursor(bcn_session_id=bcn_session_id)
                current_seq = await transaction.get_latest_inbound_seq(bcn_session_id)
                target_messages = await transaction.list_inbound_messages(
                    bcn_session_id,
                    target=target,
                    limit=1,
                )
                outbound = OutboundMessage(
                    outbound_message_id=outbound_id,
                    command_id=command_id,
                    bcn_session_id=bcn_session_id,
                    channel_session_id=channel_session.channel_session_id,
                    target=target,
                    body=body,
                    state=OutboundDeliveryState.DRAFT,
                    fresh_check_state=FreshCheckState.REQUIRED,
                    created_at_ms=created_at_ms,
                )
                outbound = await transaction.save_outbound_message(outbound)
                outbound_id = outbound.outbound_message_id
                rejection_event_name = "bcc.send.fresh_check.failed"
                if not target_messages:
                    outbound = outbound.transition_to(
                        OutboundDeliveryState.REJECTED,
                        at_ms=self._clock(),
                        error_kind=ErrorKind.TARGET_NOT_REPLYABLE.value,
                        error_message=(
                            f"Thread target is not found or is not replyable: {target}"
                        ),
                        next_action=(
                            "Run `bcc message read` or `bcc message check` for this "
                            "target to verify whether the message already landed; "
                            "retry only after stable verification."
                        ),
                    )
                    await transaction.save_outbound_message(outbound)
                    audit_context = CorrelationContext(
                        node_id=self.node_id,
                        channel_slug=channel_session.channel_slug,
                        channel_session_id=channel_session.channel_session_id,
                        bcn_session_id=bcn_session_id,
                        command_id=command_id,
                        inbound_seq=current_seq,
                        outbound_message_id=outbound_id,
                    )
                    audit_state = RuntimeEventState.FAILED
                    audit_kind = ErrorKind.TARGET_NOT_REPLYABLE
                    rejection_event_name = "bcc.send.target.failed"
                elif cursor.inbox_snapshot_seq is None:
                    outbound = outbound.record_fresh_check(
                        FreshCheckState.FAILED,
                        snapshot_seq=None,
                        current_inbound_seq=current_seq,
                    )
                    outbound = outbound.transition_to(
                        OutboundDeliveryState.REJECTED,
                        at_ms=self._clock(),
                        error_kind=ErrorKind.FRESH_CHECK_REQUIRED.value,
                        error_message=(
                            "No inbox snapshot is available; outbound send was refused."
                        ),
                        next_action=(
                            "Run `bcc message check` or `bcc message read` before "
                            "retrying."
                        ),
                    )
                    await transaction.save_outbound_message(outbound)
                    audit_context = CorrelationContext(
                        node_id=self.node_id,
                        channel_slug=channel_session.channel_slug,
                        channel_session_id=channel_session.channel_session_id,
                        bcn_session_id=bcn_session_id,
                        command_id=command_id,
                        inbound_seq=current_seq,
                        outbound_message_id=outbound_id,
                    )
                    audit_state = RuntimeEventState.FAILED
                    audit_kind = ErrorKind.FRESH_CHECK_REQUIRED
                elif current_seq > cursor.inbox_snapshot_seq:
                    outbound = outbound.record_fresh_check(
                        FreshCheckState.FAILED,
                        snapshot_seq=cursor.inbox_snapshot_seq,
                        current_inbound_seq=current_seq,
                    )
                    outbound = outbound.transition_to(
                        OutboundDeliveryState.REJECTED,
                        at_ms=self._clock(),
                        error_kind=ErrorKind.FRESH_CHECK_FAILED.value,
                        error_message=(
                            "New inbound message(s) arrived after the latest inbox "
                            "snapshot; outbound send was refused."
                        ),
                        next_action=(
                            "Run `bcc message check` to read the new messages, then "
                            "retry `bcc message send` if still appropriate."
                        ),
                    )
                    await transaction.save_outbound_message(outbound)
                    audit_context = CorrelationContext(
                        node_id=self.node_id,
                        channel_slug=channel_session.channel_slug,
                        channel_session_id=channel_session.channel_session_id,
                        bcn_session_id=bcn_session_id,
                        command_id=command_id,
                        inbound_seq=current_seq,
                        outbound_message_id=outbound_id,
                    )
                    audit_state = RuntimeEventState.FAILED
                    audit_kind = ErrorKind.FRESH_CHECK_FAILED
                else:
                    outbound = outbound.record_fresh_check(
                        FreshCheckState.PASSED,
                        snapshot_seq=cursor.inbox_snapshot_seq,
                        current_inbound_seq=current_seq,
                    )
                    outbound = outbound.transition_to(
                        OutboundDeliveryState.PENDING,
                        at_ms=self._clock(),
                    )
                    outbound = replace(
                        outbound,
                        provider_attempted_at_ms=self._clock(),
                    )
                    await transaction.save_outbound_message(outbound)
                    audit_context = CorrelationContext(
                        node_id=self.node_id,
                        channel_slug=channel_session.channel_slug,
                        channel_session_id=channel_session.channel_session_id,
                        bcn_session_id=bcn_session_id,
                        command_id=command_id,
                        inbound_seq=current_seq,
                        outbound_message_id=outbound_id,
                    )
                    audit_state = RuntimeEventState.STARTED
                    audit_kind = None

            if outbound.state is OutboundDeliveryState.REJECTED:
                await self._append_audit(
                    event_name=rejection_event_name,
                    state=audit_state,
                    correlation=audit_context,
                    error_kind=audit_kind,
                    error_message=outbound.error_message,
                )
                return outbound

            await self._append_audit(
                event_name="bcc.send.fresh_check.passed",
                state=RuntimeEventState.COMPLETED,
                correlation=audit_context,
            )
            await self._append_audit(
                event_name="channel.outbound.pending",
                state=RuntimeEventState.STARTED,
                correlation=audit_context,
            )
            try:
                provider_result = await self._channel.send(
                    outbound, timeout=self._timeout_budget.provider_call_seconds
                )
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001
                provider_result = None
                provider_error = error
            else:
                provider_error = None

            attempted_at_ms = outbound.provider_attempted_at_ms or self._clock()
            outbound = replace(outbound, provider_attempted_at_ms=attempted_at_ms)
            if provider_result is None:
                outbound = outbound.transition_to(
                    OutboundDeliveryState.UNKNOWN,
                    at_ms=self._clock(),
                    error_kind=ErrorKind.PROVIDER_UNKNOWN.value,
                    error_message=str(provider_error),
                    next_action="reconcile channel delivery before retrying",
                )
                terminal_kind = ErrorKind.PROVIDER_UNKNOWN
                terminal_state = RuntimeEventState.UNKNOWN
            elif provider_result.status is ProviderCallStatus.CONFIRMED:
                receipt = provider_result.value
                if receipt is None:
                    raise ValueError("confirmed channel delivery has no receipt")
                outbound = outbound.transition_to(
                    OutboundDeliveryState.SENT,
                    at_ms=self._clock(),
                    provider_message_id=receipt.provider_message_id,
                    provider_receipt_ref=receipt.provider_receipt_ref,
                )
                terminal_kind = None
                terminal_state = RuntimeEventState.COMPLETED
            elif provider_result.status is ProviderCallStatus.QUEUED:
                receipt = provider_result.value
                if receipt is None:
                    raise ValueError("queued channel delivery has no receipt")
                outbound = outbound.transition_to(
                    OutboundDeliveryState.QUEUED,
                    at_ms=self._clock(),
                    provider_message_id=receipt.provider_message_id,
                    provider_receipt_ref=receipt.provider_receipt_ref,
                )
                terminal_kind = None
                terminal_state = RuntimeEventState.STARTED
            elif provider_result.status is ProviderCallStatus.FAILED:
                outbound = outbound.transition_to(
                    OutboundDeliveryState.FAILED,
                    at_ms=self._clock(),
                    error_kind=provider_result.error_kind
                    or ErrorKind.PROVIDER_FAILED.value,
                    error_message=provider_result.error_message,
                )
                terminal_kind = ErrorKind.PROVIDER_FAILED
                terminal_state = RuntimeEventState.FAILED
            else:
                outbound = outbound.transition_to(
                    OutboundDeliveryState.UNKNOWN,
                    at_ms=self._clock(),
                    error_kind=provider_result.error_kind
                    or ErrorKind.PROVIDER_UNKNOWN.value,
                    error_message=provider_result.error_message,
                    next_action="reconcile channel delivery before retrying",
                )
                terminal_kind = ErrorKind.PROVIDER_UNKNOWN
                terminal_state = RuntimeEventState.UNKNOWN

            async with self._storage.transaction() as transaction:
                await transaction.save_outbound_message(outbound)
            await self._append_audit(
                event_name=f"channel.outbound.{outbound.state.value}",
                state=terminal_state,
                correlation=audit_context,
                error_kind=terminal_kind,
                error_message=outbound.error_message,
            )
            return outbound

    async def _receive_loop(self) -> None:
        async for message in self._channel.receive():
            if self._stopping:
                break
            self.dispatch_inbound(message)

    def _forget_task(self, task: asyncio.Task[RuntimeTurn]) -> None:
        self._active_tasks.discard(task)
        if not task.cancelled():
            task.exception()

    async def _prepare_session(self, message: InboundMessage) -> _SessionContext:
        async with self._storage.transaction() as transaction:
            channel_session = await transaction.find_channel_session(
                channel_slug=message.channel_slug,
                provider_conversation_key=message.channel_session_id,
                provider_thread_key=message.provider_thread_id or "",
            )
            now_ms = self._clock()
            if channel_session is None:
                channel_session = ChannelSession(
                    channel_session_id=message.channel_session_id,
                    channel_slug=message.channel_slug,
                    provider_conversation_key=message.channel_session_id,
                    provider_thread_key=message.provider_thread_id or "",
                    state=ChannelSessionState.ACTIVE,
                    created_at_ms=now_ms,
                    updated_at_ms=now_ms,
                )
                await transaction.save_channel_session(channel_session)
            elif channel_session.channel_session_id != message.channel_session_id:
                raise ValueError("inbound channel session identity does not match")

            bcn_session = await transaction.get_bcn_session(message.bcn_session_id)
            if bcn_session is None:
                bcn_session = BcnSession(
                    bcn_session_id=message.bcn_session_id,
                    channel_session_id=channel_session.channel_session_id,
                    workspace_id=self.workspace_id,
                    state=BcnSessionState.CREATED,
                    created_at_ms=now_ms,
                    updated_at_ms=now_ms,
                )
                await transaction.save_bcn_session(bcn_session)
            elif (
                bcn_session.channel_session_id != channel_session.channel_session_id
                or bcn_session.workspace_id != self.workspace_id
            ):
                raise ValueError("inbound bcn session binding does not match")

            cursor = await transaction.get_consumer_cursor(message.bcn_session_id)
            if cursor is None:
                await transaction.save_consumer_cursor(
                    ConsumerCursor(bcn_session_id=message.bcn_session_id)
                )

            runtime_id = f"runtime-{message.bcn_session_id}"
            runtime_session = await transaction.get_runtime_session(runtime_id)
            if runtime_session is None:
                runtime_session = RuntimeSession(
                    agent_runtime_session_id=runtime_id,
                    bcn_session_id=bcn_session.bcn_session_id,
                    channel_session_id=channel_session.channel_session_id,
                    runtime_slug=self._runtime_slug,
                    workspace_id=self.workspace_id,
                    process_state=RuntimeProcessState.STARTING,
                    created_at_ms=now_ms,
                    updated_at_ms=now_ms,
                )
                await transaction.save_runtime_session(runtime_session)
            elif (
                runtime_session.bcn_session_id != bcn_session.bcn_session_id
                or runtime_session.channel_session_id
                != channel_session.channel_session_id
                or runtime_session.workspace_id != self.workspace_id
            ):
                raise ValueError("runtime session binding does not match")

            self._bcn_sessions[bcn_session.bcn_session_id] = bcn_session
            self._runtime_sessions[runtime_session.agent_runtime_session_id] = (
                runtime_session
            )
            return _SessionContext(channel_session, bcn_session, runtime_session)

    async def _record_inbound_and_turn(
        self, message: InboundMessage, context: _SessionContext
    ) -> tuple[InboundMessage, RuntimeTurn, bool]:
        async with self._storage.transaction() as transaction:
            existing_turn = await transaction.get_runtime_turn(
                f"turn-{message.message_id}"
            )
            if existing_turn is not None:
                return message, existing_turn, False
            message = await transaction.append_inbound_message(message)
            turn_id = f"turn-{message.message_id}"
            existing_turn = await transaction.get_runtime_turn(turn_id)
            if existing_turn is not None:
                return message, existing_turn, False
            now_ms = self._clock()
            channel_session = replace(
                context.channel_session,
                last_inbound_at_ms=message.received_at_ms,
                updated_at_ms=now_ms,
            )
            bcn_session = replace(
                context.bcn_session,
                last_activity_at_ms=message.received_at_ms,
                updated_at_ms=now_ms,
            )
            await transaction.save_channel_session(channel_session)
            await transaction.save_bcn_session(bcn_session)
            turn = RuntimeTurn(
                turn_id=turn_id,
                agent_runtime_session_id=context.runtime_session.agent_runtime_session_id,
                state=RuntimeTurnState.STARTING,
                started_at_ms=now_ms,
                client_user_message_id=message.message_id,
            )
            await transaction.save_runtime_turn(turn)
        return message, turn, True

    async def _ensure_runtime_session(
        self, context: _SessionContext
    ) -> _SessionContext:
        runtime_session = context.runtime_session
        if runtime_session.process_state is RuntimeProcessState.RUNNING:
            return context
        if runtime_session.process_state is RuntimeProcessState.STARTING:
            provider_result = await self._runtime.start_session(
                runtime_session,
                timeout=self._timeout_budget.provider_call_seconds,
            )
        elif runtime_session.process_state is RuntimeProcessState.UNKNOWN:
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
            provider_result = await self._runtime.resume_session(
                runtime_session,
                timeout=self._timeout_budget.provider_call_seconds,
            )
        else:
            return context

        now_ms = self._clock()
        if provider_result.status is ProviderCallStatus.CONFIRMED:
            updated_runtime = provider_result.value
            if updated_runtime is None:
                raise ValueError("confirmed runtime start has no session")
            if (
                updated_runtime.bcn_session_id != context.bcn_session.bcn_session_id
                or updated_runtime.channel_session_id
                != context.channel_session.channel_session_id
                or updated_runtime.workspace_id != self.workspace_id
            ):
                raise ValueError("runtime provider returned a mismatched session")
            runtime_session = updated_runtime
            bcn_session = context.bcn_session
            if bcn_session.state is BcnSessionState.CREATED:
                bcn_session = bcn_session.transition_to(
                    BcnSessionState.RUNNING,
                    updated_at_ms=now_ms,
                )
            async with self._storage.transaction() as transaction:
                await transaction.save_runtime_session(runtime_session)
                await transaction.save_bcn_session(bcn_session)
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
            bcn_session = context.bcn_session
            if bcn_session.state in {BcnSessionState.CREATED, BcnSessionState.RUNNING}:
                bcn_session = bcn_session.transition_to(
                    BcnSessionState.FAILED,
                    updated_at_ms=now_ms,
                )
            async with self._storage.transaction() as transaction:
                await transaction.save_runtime_session(runtime_session)
                await transaction.save_bcn_session(bcn_session)

        self._runtime_sessions[runtime_session.agent_runtime_session_id] = (
            runtime_session
        )
        self._bcn_sessions[bcn_session.bcn_session_id] = bcn_session
        return _SessionContext(context.channel_session, bcn_session, runtime_session)

    async def _run_turn(
        self,
        message: InboundMessage,
        context: _SessionContext,
        turn: RuntimeTurn,
    ) -> RuntimeTurn:
        binding = ApprovalBinding(
            request_id="pending",
            bcn_session_id=context.bcn_session.bcn_session_id,
            channel_session_id=context.channel_session.channel_session_id,
            agent_runtime_session_id=context.runtime_session.agent_runtime_session_id,
            turn_id=turn.turn_id,
        )
        turn_correlation = self._turn_correlation(message, context, turn)

        async def request_approval(
            request: ApprovalRequest, *, timeout: float
        ) -> ApprovalResult:
            request_id = request.request_id
            current_binding = replace(binding, request_id=request_id)
            if not current_binding.matches(request):
                raise ValueError("runtime approval request correlation mismatch")
            result = await self._channel.request_approval(request, timeout=timeout)
            if result.request_id != request_id:
                raise ValueError("channel approval result correlation mismatch")
            await self._append_audit(
                event_name="approval.decided",
                state=RuntimeEventState.COMPLETED,
                correlation=CorrelationContext(
                    node_id=self.node_id,
                    channel_slug=context.channel_session.channel_slug,
                    channel_session_id=context.channel_session.channel_session_id,
                    bcn_session_id=context.bcn_session.bcn_session_id,
                    agent_runtime_session_id=context.runtime_session.agent_runtime_session_id,
                    turn_id=turn.turn_id,
                    request_id=request_id,
                    inbound_seq=message.seq,
                ),
                metadata={"decision": result.decision.value},
            )
            return result

        stream: IRuntimeTurnStream | None = None
        observed_terminal = False
        try:
            approval_handler = _ApprovalHandler(
                lambda request, timeout: request_approval(request, timeout=timeout)
            )
            stream = await self._runtime.start_turn(
                context.runtime_session,
                turn,
                f"[inbox notice session={context.bcn_session.bcn_session_id}]\n"
                "Inbox update: 1 unread message. Use the message command to read it.",
                approval_handler,
                timeout=self._timeout_budget.provider_call_seconds,
            )
            async for event in stream:
                turn = await self._apply_runtime_event(message, context, turn, event)
                if event.state in {
                    RuntimeEventState.COMPLETED,
                    RuntimeEventState.FAILED,
                    RuntimeEventState.UNKNOWN,
                }:
                    observed_terminal = True
                    break
            if not observed_terminal:
                return await self._finish_turn(
                    turn,
                    RuntimeTurnState.UNKNOWN,
                    error_kind=ErrorKind.PROVIDER_UNKNOWN,
                    error_message="runtime stream ended without a terminal event",
                    correlation=turn_correlation,
                )
            return turn
        except asyncio.CancelledError:
            await self._close_stream(stream)
            await self._finish_turn(
                turn,
                RuntimeTurnState.CANCELLED,
                error_kind=ErrorKind.CANCELLED,
                error_message="runtime turn cancelled",
                correlation=turn_correlation,
            )
            raise
        except Exception as error:  # noqa: BLE001
            await self._close_stream(stream)
            return await self._finish_turn(
                turn,
                RuntimeTurnState.FAILED,
                error_kind=ErrorKind.PROVIDER_FAILED,
                error_message=str(error),
                correlation=turn_correlation,
            )
        finally:
            await self._close_stream(stream)

    async def _close_stream(self, stream: IRuntimeTurnStream | None) -> None:
        if stream is not None:
            await stream.aclose()

    async def _apply_runtime_event(
        self,
        message: InboundMessage,
        context: _SessionContext,
        turn: RuntimeTurn,
        event: RuntimeEvent,
    ) -> RuntimeTurn:
        if event.turn_id is not None and event.turn_id != turn.turn_id:
            raise ValueError("runtime event turn correlation mismatch")
        async with self._storage.transaction() as transaction:
            event = await transaction.append_runtime_event(event)
            current_turn = await transaction.get_runtime_turn(turn.turn_id)
            if current_turn is None:
                raise ValueError(f"unknown runtime turn: {turn.turn_id}")
            if event.state is RuntimeEventState.STARTED:
                target_state = RuntimeTurnState.RUNNING
            elif event.state is RuntimeEventState.COMPLETED:
                target_state = RuntimeTurnState.COMPLETED
            elif event.state is RuntimeEventState.FAILED:
                target_state = RuntimeTurnState.FAILED
            else:
                target_state = RuntimeTurnState.UNKNOWN
            error_kind = event.error_kind
            if event.state is RuntimeEventState.FAILED and error_kind is None:
                error_kind = ErrorKind.PROVIDER_FAILED.value
            if event.state is RuntimeEventState.UNKNOWN and error_kind is None:
                error_kind = ErrorKind.PROVIDER_UNKNOWN.value
            updated_turn = current_turn.transition_to(
                target_state,
                at_ms=event.created_at_ms,
                error_kind=error_kind,
                error_message=event.error_message,
                latest_event_name=event.event_name,
            )
            await transaction.save_runtime_turn(updated_turn)
        try:
            audit_kind = ErrorKind(event.error_kind) if event.error_kind else None
        except ValueError:
            audit_kind = ErrorKind.INTERNAL
        await self._append_audit(
            event_name=event.event_name,
            state=event.state,
            correlation=self._turn_correlation(message, context, turn),
            error_kind=audit_kind,
            error_message=event.error_message,
        )
        return updated_turn

    async def _finish_turn(
        self,
        turn: RuntimeTurn,
        state: RuntimeTurnState,
        *,
        error_kind: ErrorKind | None = None,
        error_message: str | None = None,
        correlation: CorrelationContext | None = None,
    ) -> RuntimeTurn:
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
        await self._append_audit(
            event_name=f"runtime.turn.{state.value}",
            state=(
                RuntimeEventState.COMPLETED
                if state is RuntimeTurnState.COMPLETED
                else RuntimeEventState.FAILED
                if state is not RuntimeTurnState.UNKNOWN
                else RuntimeEventState.UNKNOWN
            ),
            correlation=correlation or CorrelationContext(turn_id=turn.turn_id),
            error_kind=error_kind,
            error_message=error_message,
        )
        return current_turn

    def _turn_correlation(
        self,
        message: InboundMessage,
        context: _SessionContext,
        turn: RuntimeTurn,
    ) -> CorrelationContext:
        return CorrelationContext(
            node_id=self.node_id,
            channel_slug=context.channel_session.channel_slug,
            channel_session_id=context.channel_session.channel_session_id,
            bcn_session_id=context.bcn_session.bcn_session_id,
            agent_runtime_session_id=context.runtime_session.agent_runtime_session_id,
            turn_id=turn.turn_id,
            inbound_seq=message.seq,
            provider_thread_id=context.runtime_session.provider_thread_id,
            provider_turn_id=turn.provider_turn_id,
        )

    async def _append_audit(
        self,
        *,
        event_name: str,
        state: RuntimeEventState,
        correlation: CorrelationContext,
        error_kind: ErrorKind | None = None,
        error_message: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> None:
        await self._audit_sink.append(
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

    async def _stop_runtime_session(
        self, runtime_session: RuntimeSession, *, timeout: float
    ) -> None:
        stopping = runtime_session.transition_process_to(
            RuntimeProcessState.STOPPING,
            updated_at_ms=self._clock(),
        )
        async with self._storage.transaction() as transaction:
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
        self._runtime_sessions[stopped.agent_runtime_session_id] = stopped
        async with self._storage.transaction() as transaction:
            await transaction.save_runtime_session(stopped)
