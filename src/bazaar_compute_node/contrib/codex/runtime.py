from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from functools import partial
from pathlib import Path
from typing import Any

from ...core.approval import IApprovalHandler
from ...core.instruction import DeveloperInstructionContext
from ...core.lifecycle import IAsyncLifecycle
from ...core.models import (
    RuntimeEventState,
    RuntimeSession,
    RuntimeTurn,
)
from ...core.outcomes import ProviderCallResult, ProviderCallStatus
from ...core.paths import resolve_workspace_dir
from ...core.runtime import (
    IRuntime,
    IRuntimeTurnStream,
    RuntimeBackgroundIdle,
    RuntimeCommandContext,
    RuntimeExpire,
    RuntimeLifecycleEvent,
    RuntimeSandboxMode,
    RuntimeSessionReconciliation,
    RuntimeSessionUnavailable,
)
from ...core.utils.clock import now_ms
from ...core.utils.text import format_exception
from .client import (
    Client,
    ThreadInfo,
    parse_background_terminals_response,
    parse_fs_changed_notification,
    parse_fs_watch_response,
    parse_initialize_response,
    parse_skills_changed_notification,
    parse_thread_response,
    parse_turn_response,
    parse_turn_steer_response,
)
from .events import TurnEventStream
from .process import JsonlProcessSpec, JsonlProcessSupervisor
from .protocol import (
    AppServerProtocolError,
    JsonlProcessExited,
    JsonlProcessNotRunning,
    JsonlProtocolError,
    JsonlRemoteError,
    JsonlRequestTimeout,
    JsonlTransportError,
)

_INITIALIZE_ATTEMPTS = 2
_RECONCILE_ATTEMPTS = 2
_WORKSPACE_AGENTS_WATCH_ID = "bcn-agents-workspace"
_CODEX_HOME_AGENTS_WATCH_ID = "bcn-agents-codex-home"
_BACKGROUND_REFRESH_TIMEOUT_SECONDS = 10.0


@dataclass(slots=True)
class _Connection:
    supervisor: JsonlProcessSupervisor
    client: Client
    workspace: Path
    provider_thread_id: str
    active_turn_id: str | None = None
    background_state_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    background_job_present: bool = False


class Runtime(IRuntime, IAsyncLifecycle):
    """Run one persistent Codex App Server process per BCN runtime session."""

    @property
    def name(self) -> str:
        return "codex"

    def environment_variable_names(self) -> tuple[str, ...]:
        return (
            "CODEX_HOME",
            "CODEX_SQLITE_HOME",
            "CODEX_CA_CERTIFICATE",
            "SSL_CERT_FILE",
        )

    def __init__(
        self,
        context: RuntimeCommandContext,
        *,
        executable: str = "codex",
        model: str | None = None,
        effort: str | None = None,
    ) -> None:
        if not executable:
            raise ValueError("executable must be a non-empty string")
        if not context.agent_id:
            raise ValueError("runtime context agent_id must be non-empty")
        if model is not None and not model:
            raise ValueError("model must be a non-empty string or None")
        if effort is not None and not effort:
            raise ValueError("effort must be a non-empty string or None")
        self._context = context
        self._executable = executable
        self._model = model
        self._effort = effort
        self._connections: dict[str, _Connection] = {}
        self._lifecycle_events: asyncio.Queue[RuntimeLifecycleEvent] = asyncio.Queue()
        self._background_refresh_tasks: set[asyncio.Task[None]] = set()
        self._logger = logging.getLogger("bazaar_compute_node.runtime.codex")
        self._started = False
        self._stopping = False

    async def start(self, *, timeout: float) -> None:
        del timeout
        if self._stopping:
            raise RuntimeError("Codex App Server runtime is stopping")
        if os.name == "nt":
            self._logger.warning(
                "Codex idle timeout is not supported on Windows; "
                "see openai/codex#15461."
            )
        self._started = True

    async def stop(self, *, timeout: float) -> None:
        if self._stopping:
            return
        self._stopping = True
        connections = tuple(self._connections.items())
        self._connections.clear()
        refresh_tasks = tuple(self._background_refresh_tasks)
        for task in refresh_tasks:
            task.cancel()
        if refresh_tasks:
            await asyncio.gather(*refresh_tasks, return_exceptions=True)
        for _, connection in connections:
            try:
                await connection.supervisor.stop(timeout=timeout)
            except asyncio.CancelledError:
                raise
            except OSError, TimeoutError, JsonlTransportError:
                continue
        self._started = False

    async def receive_event(self) -> RuntimeLifecycleEvent:
        return await self._lifecycle_events.get()

    async def start_session(
        self,
        session: RuntimeSession,
        *,
        timeout: float,
    ) -> ProviderCallResult[RuntimeSession]:
        self._ensure_started()
        existing = self._connections.pop(session.id, None)
        if existing is not None:
            await self._stop_connection(existing, timeout=timeout)
        connection: _Connection | None = None
        try:
            connection = await self._open_connection(session, timeout=timeout)
            response = await connection.client.start_thread(
                DeveloperInstructionContext(
                    agent_name=self._context.agent_name,
                    bot_name=self._context.bot_name(),
                    agent_id=self._context.agent_id,
                    runtime_session_id=session.id,
                    runtime=session.runtime,
                    workspace=str(connection.workspace),
                    mode=self._context.mode,
                ).render(),
                model=self._model,
                cwd=connection.workspace,
                timeout=timeout,
            )
            thread = parse_thread_response(response)
            connection.provider_thread_id = thread.thread_id
            self._connections[session.id] = connection
            return ProviderCallResult(
                status=ProviderCallStatus.CONFIRMED,
                value=replace(
                    session,
                    provider_thread_id=thread.thread_id,
                    updated_at_ms=now_ms(),
                ),
                receipt={"provider_thread_id": thread.thread_id},
            )
        except asyncio.CancelledError:
            if connection is not None:
                await self._stop_connection(connection, timeout=timeout)
            raise
        except Exception as error:  # noqa: BLE001
            if connection is not None:
                await self._stop_connection(connection, timeout=timeout)
            return _provider_result(error)

    @staticmethod
    def _log(
        write: Callable[[str, str], None], event_name: str, **metadata: object
    ) -> None:
        write(
            "%s",
            json.dumps(
                {"event_name": event_name, "metadata": metadata},
                separators=(",", ":"),
                sort_keys=True,
            ),
        )

    async def _pick_up_active_turn(
        self,
        session: RuntimeSession,
        turn: RuntimeTurn | None,
        approval_handler: IApprovalHandler | None,
        connection: _Connection,
        read_thread: ThreadInfo,
        *,
        provider_thread_id: str,
        timeout: float,
    ) -> ProviderCallResult[RuntimeSessionReconciliation] | None:
        """Take back the turn a live thread is still running, if that is what it is."""

        if read_thread.status != "active":
            return None
        active_turns = tuple(
            item for item in read_thread.turns if item.status == "inProgress"
        )
        if (
            turn is None
            or approval_handler is None
            or turn.provider_turn_id is None
            or len(active_turns) != 1
            or active_turns[0].turn_id != turn.provider_turn_id
        ):
            raise AppServerProtocolError("active runtime turn cannot be reconciled")
        connection.provider_thread_id = provider_thread_id
        connection.active_turn_id = turn.turn_id
        self._connections[session.id] = connection
        stream = TurnEventStream(
            connection.supervisor,
            actor=session.actor,
            runtime_session_id=session.id,
            turn_id=turn.turn_id,
            provider_thread_id=provider_thread_id,
            provider_turn_id=turn.provider_turn_id,
            approval_handler=approval_handler,
            approval_timeout=timeout,
            on_closed=lambda: self._clear_active_turn(
                session.id,
                turn.turn_id,
            ),
        )
        return ProviderCallResult(
            status=ProviderCallStatus.CONFIRMED,
            value=RuntimeSessionReconciliation(
                session=replace(session, updated_at_ms=now_ms()),
                stream=stream,
            ),
            receipt={
                "provider_thread_id": provider_thread_id,
                "provider_turn_id": turn.provider_turn_id,
            },
        )

    async def _reconcile_once(
        self,
        session: RuntimeSession,
        turn: RuntimeTurn | None,
        approval_handler: IApprovalHandler | None,
        connection: _Connection,
        *,
        provider_thread_id: str,
        timeout: float,
        attempt: int,
    ) -> ProviderCallResult[RuntimeSessionReconciliation]:
        """Read the provider thread and either take its turn back or resume it."""

        read_response = await connection.client.read_thread(
            provider_thread_id,
            timeout=timeout,
        )
        read_thread = parse_thread_response(read_response)
        if read_thread.thread_id != provider_thread_id:
            raise AppServerProtocolError(
                "thread/read returned a different provider thread"
            )
        picked_up = await self._pick_up_active_turn(
            session,
            turn,
            approval_handler,
            connection,
            read_thread,
            provider_thread_id=provider_thread_id,
            timeout=timeout,
        )
        if picked_up is not None:
            return picked_up
        if read_thread.status not in {"idle", "notLoaded"}:
            raise AppServerProtocolError(
                f"unsupported runtime thread status: {read_thread.status}"
            )
        response = await connection.client.resume_thread(
            provider_thread_id,
            model=self._model,
            cwd=connection.workspace,
            timeout=timeout,
        )
        thread = parse_thread_response(response)
        if thread.thread_id != provider_thread_id:
            raise AppServerProtocolError(
                "thread/resume returned a different provider thread"
            )
        connection.provider_thread_id = thread.thread_id
        self._connections[session.id] = connection
        if attempt > 0:
            self._log(
                self._logger.info,
                "runtime.process.reconcile.retry_succeeded",
                attempt=attempt + 1,
                session_id=session.actor.id,
            )
        return ProviderCallResult(
            status=ProviderCallStatus.CONFIRMED,
            value=RuntimeSessionReconciliation(
                session=replace(
                    session,
                    provider_thread_id=thread.thread_id,
                    updated_at_ms=now_ms(),
                ),
            ),
            receipt={"provider_thread_id": thread.thread_id},
        )

    async def reconcile_session(
        self,
        session: RuntimeSession,
        turn: RuntimeTurn | None,
        approval_handler: IApprovalHandler | None,
        *,
        timeout: float,
    ) -> ProviderCallResult[RuntimeSessionReconciliation]:
        self._ensure_started()
        provider_thread_id = session.provider_thread_id
        if provider_thread_id is None:
            return ProviderCallResult(
                status=ProviderCallStatus.FAILED,
                error_kind="provider_failed",
                error_message="cannot reconcile a runtime session without a provider thread",
            )
        connection = self._connections.pop(session.id, None)
        if connection is not None and not connection.supervisor.is_running:
            await self._stop_connection(connection, timeout=timeout)
            connection = None
        for attempt in range(_RECONCILE_ATTEMPTS):
            if connection is None:
                try:
                    connection = await self._open_connection(session, timeout=timeout)
                except asyncio.CancelledError:
                    raise
                except Exception as error:  # noqa: BLE001
                    return _provider_result(error)
            try:
                return await self._reconcile_once(
                    session,
                    turn,
                    approval_handler,
                    connection,
                    provider_thread_id=provider_thread_id,
                    timeout=timeout,
                    attempt=attempt,
                )
            except asyncio.CancelledError:
                await self._stop_connection(connection, timeout=timeout)
                raise
            except (
                JsonlProcessExited,
                JsonlProcessNotRunning,
                JsonlRequestTimeout,
            ) as error:
                await self._stop_connection(connection, timeout=timeout)
                connection = None
                if attempt + 1 == _RECONCILE_ATTEMPTS:
                    self._log(
                        self._logger.error,
                        "runtime.process.reconcile.retry_exhausted",
                        attempt=attempt + 1,
                        error_type=type(error).__name__,
                        session_id=session.actor.id,
                    )
                    return _provider_result(error)
                self._log(
                    self._logger.warning,
                    "runtime.process.reconcile.retrying",
                    attempt=attempt + 1,
                    error_type=type(error).__name__,
                    next_attempt=attempt + 2,
                    session_id=session.actor.id,
                )
            except Exception as error:  # noqa: BLE001
                await self._stop_connection(connection, timeout=timeout)
                return _provider_result(error)
        raise AssertionError("Codex reconcile retry loop did not return")

    async def start_turn(
        self,
        session: RuntimeSession,
        turn: RuntimeTurn,
        input_text: str,
        approval_handler: IApprovalHandler,
        *,
        timeout: float,
    ) -> IRuntimeTurnStream:
        self._ensure_started()
        connection = self._connections.get(session.id)
        if connection is None or not connection.supervisor.is_running:
            raise RuntimeSessionUnavailable("Codex App Server process is not running")
        if connection.active_turn_id is not None:
            raise RuntimeError(
                f"runtime session already has an active turn: {connection.active_turn_id}"
            )
        provider_turn_id: str | None = None
        initial_error: BaseException | None = None
        initial_error_kind = "provider_unknown"
        initial_error_state = RuntimeEventState.UNKNOWN
        try:
            if self._context.sandbox_mode is RuntimeSandboxMode.WORKSPACE_WRITE:
                sandbox_policy: dict[str, object] = {
                    "type": "workspaceWrite",
                    "writableRoots": [str(connection.workspace)],
                    "networkAccess": self._context.network_access,
                }
            elif self._context.sandbox_mode is RuntimeSandboxMode.DANGER_FULL_ACCESS:
                sandbox_policy = {"type": "dangerFullAccess"}
            else:
                raise AssertionError(
                    f"unsupported runtime sandbox mode: {self._context.sandbox_mode}"
                )
            response = await connection.client.start_turn(
                connection.provider_thread_id,
                input_text,
                client_user_message_id=turn.client_user_message_id,
                model=self._model,
                effort=self._effort,
                cwd=connection.workspace,
                sandbox_policy=sandbox_policy,
                timeout=timeout,
            )
            provider_turn = parse_turn_response(response)
            provider_turn_id = provider_turn.turn_id
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            initial_error = error
            if isinstance(error, JsonlRemoteError):
                initial_error_kind = "provider_failed"
                initial_error_state = RuntimeEventState.FAILED
            elif isinstance(error, AppServerProtocolError):
                initial_error_kind = "protocol"
                initial_error_state = RuntimeEventState.FAILED
        connection.active_turn_id = turn.turn_id
        return TurnEventStream(
            connection.supervisor,
            actor=session.actor,
            runtime_session_id=session.id,
            turn_id=turn.turn_id,
            provider_thread_id=connection.provider_thread_id,
            provider_turn_id=provider_turn_id,
            approval_handler=approval_handler,
            approval_timeout=timeout,
            initial_error=initial_error,
            initial_error_kind=initial_error_kind,
            initial_error_state=initial_error_state,
            on_closed=lambda: self._clear_active_turn(session.id, turn.turn_id),
        )

    async def steer_turn(
        self,
        session: RuntimeSession,
        turn: RuntimeTurn,
        input_text: str,
        *,
        timeout: float,
    ) -> bool:
        self._ensure_started()
        connection = self._connections.get(session.id)
        provider_turn_id = turn.provider_turn_id
        if (
            connection is None
            or not connection.supervisor.is_running
            or connection.active_turn_id != turn.turn_id
            or provider_turn_id is None
        ):
            return False
        try:
            response = await connection.client.steer_turn(
                connection.provider_thread_id,
                provider_turn_id,
                input_text,
                timeout=timeout,
            )
            accepted_turn_id = parse_turn_steer_response(response)
            if accepted_turn_id != provider_turn_id:
                raise AppServerProtocolError(
                    "turn/steer returned a different provider turn"
                )
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            self._log(
                self._logger.warning,
                "runtime.turn.steer.not_accepted",
                error_type=type(error).__name__,
                session_id=session.actor.id,
            )
            return False
        return True

    async def has_background_job(
        self,
        session: RuntimeSession,
        *,
        timeout: float,
    ) -> bool:
        if os.name == "nt":
            # Detached OS processes are not exposed by the Windows Codex provider.
            return True
        connection = self._connections.get(session.id)
        provider_thread_id = session.provider_thread_id
        if connection is None or not provider_thread_id:
            return False
        try:
            async with connection.background_state_lock:
                response = await connection.client.list_background_terminals(
                    provider_thread_id,
                    timeout=timeout,
                )
                present = parse_background_terminals_response(response)
                if self._connections.get(session.id) is connection:
                    _record_background_state(connection, present)
        except AppServerProtocolError, JsonlTransportError:
            return False
        return present

    async def stop_session(
        self,
        session: RuntimeSession,
        *,
        timeout: float,
    ) -> ProviderCallResult[RuntimeSession]:
        connection = self._connections.pop(session.id, None)
        if connection is None:
            return ProviderCallResult(
                status=ProviderCallStatus.CONFIRMED,
                value=session,
            )
        await self._stop_connection(connection, timeout=timeout)
        return ProviderCallResult(
            status=ProviderCallStatus.CONFIRMED,
            value=session,
        )

    def _route_notification(
        self,
        message: dict[str, object],
        *,
        targets: dict[str, Path],
        runtime_session_id: str,
        origin_holder: list[_Connection],
    ) -> bool:
        """Decide what a provider notification means for the session that owns it."""

        method = message.get("method")
        if method == "skills/changed":
            parse_skills_changed_notification(message)
            self._lifecycle_events.put_nowait(RuntimeExpire(runtime_session_id))
            return True
        if method == "item/completed" and os.name != "nt":
            params = message.get("params")
            item = params.get("item") if isinstance(params, Mapping) else None
            if (
                isinstance(params, Mapping)
                and isinstance(params.get("threadId"), str)
                and isinstance(item, Mapping)
                and item.get("type") == "commandExecution"
            ):
                if not origin_holder:
                    return False
                origin = origin_holder[0]
                task = asyncio.create_task(
                    self._refresh_background_state(
                        runtime_session_id,
                        origin,
                    ),
                    name=f"bcn-codex-background-{runtime_session_id}",
                )
                self._background_refresh_tasks.add(task)
                task.add_done_callback(self._background_refresh_tasks.discard)
        if method != "fs/changed":
            return False
        changed = parse_fs_changed_notification(message)
        target = targets.get(changed.watch_id)
        if target is None or target not in changed.changed_paths:
            return False
        self._lifecycle_events.put_nowait(RuntimeExpire(runtime_session_id))
        return True

    async def _watch_agents_files(
        self, client: Client, workspace: Path, targets: dict[str, Path]
    ) -> None:
        """Start the connection and watch for the AGENTS.md files it should honour."""

        initialize_response = await client.initialize(
            client_info=self._context.client_info,
            timeout=self._context.startup_timeout_seconds,
        )
        codex_home = parse_initialize_response(initialize_response)
        for watch_id, target in (
            (_WORKSPACE_AGENTS_WATCH_ID, workspace / "AGENTS.md"),
            (_CODEX_HOME_AGENTS_WATCH_ID, codex_home / "AGENTS.md"),
        ):
            targets[watch_id] = target
            watch_response = await client.watch_path(
                target,
                watch_id,
                timeout=self._context.startup_timeout_seconds,
            )
            if parse_fs_watch_response(watch_response) != target:
                raise AppServerProtocolError("fs/watch returned a different path")

    async def _open_connection(
        self,
        session: RuntimeSession,
        *,
        timeout: float,
    ) -> _Connection:
        executable = await asyncio.to_thread(shutil.which, self._executable)
        if executable is None:
            raise FileNotFoundError(f"Codex executable not found: {self._executable}")
        if session.workspace_id != self._context.agent_id:
            raise ValueError("runtime session workspace does not match Agent identity")
        workspace = resolve_workspace_dir(self._context.agent_id)
        await asyncio.to_thread(
            workspace.mkdir,
            parents=True,
            exist_ok=True,
            mode=0o700,
        )
        if os.name != "nt":
            await asyncio.to_thread(workspace.chmod, 0o700)
        environment = dict(self._context.environment_for_session(session))
        for attempt in range(_INITIALIZE_ATTEMPTS):
            watch_targets: dict[str, Path] = {}
            connection_holder: list[_Connection] = []

            supervisor = JsonlProcessSupervisor(
                JsonlProcessSpec(
                    executable=executable,
                    arguments=("app-server", "--stdio"),
                    cwd=workspace,
                    environment=environment,
                ),
                notification_router=partial(
                    self._route_notification,
                    targets=watch_targets,
                    runtime_session_id=session.id,
                    origin_holder=connection_holder,
                ),
            )
            client = Client(supervisor)
            await supervisor.start(timeout=self._context.startup_timeout_seconds)
            try:
                await self._watch_agents_files(client, workspace, watch_targets)
            except JsonlRequestTimeout:
                await supervisor.stop(timeout=timeout)
                if attempt + 1 == _INITIALIZE_ATTEMPTS:
                    raise
                continue
            except BaseException:
                await supervisor.stop(timeout=timeout)
                raise
            connection = _Connection(
                supervisor=supervisor,
                client=client,
                workspace=workspace,
                provider_thread_id=session.provider_thread_id or "",
            )
            connection_holder.append(connection)
            return connection
        raise AssertionError("Codex initialization retry loop did not return")

    async def _refresh_background_state(
        self,
        session_id: str,
        connection: _Connection,
    ) -> None:
        if self._connections.get(session_id) is not connection:
            return
        async with connection.background_state_lock:
            if self._connections.get(session_id) is not connection:
                return
            try:
                response = await connection.client.list_background_terminals(
                    connection.provider_thread_id,
                    timeout=_BACKGROUND_REFRESH_TIMEOUT_SECONDS,
                )
                present = parse_background_terminals_response(response)
            except asyncio.CancelledError:
                raise
            except AppServerProtocolError, JsonlTransportError:
                return
            if self._connections.get(session_id) is not connection:
                return
            if _record_background_state(connection, present):
                self._lifecycle_events.put_nowait(RuntimeBackgroundIdle(session_id))

    async def _stop_connection(
        self,
        connection: _Connection,
        *,
        timeout: float,
    ) -> None:
        try:
            await connection.supervisor.stop(timeout=timeout)
        except asyncio.CancelledError:
            raise
        except OSError, TimeoutError, JsonlTransportError:
            return

    def _clear_active_turn(self, session_id: str, turn_id: str) -> None:
        connection = self._connections.get(session_id)
        if connection is not None and connection.active_turn_id == turn_id:
            connection.active_turn_id = None

    def _ensure_started(self) -> None:
        if not self._started or self._stopping:
            raise RuntimeError("Codex App Server runtime is not started")


def _record_background_state(connection: _Connection, present: bool) -> bool:
    was_present = connection.background_job_present
    connection.background_job_present = present
    return was_present and not present


def _provider_result(error: BaseException) -> ProviderCallResult[Any]:
    if isinstance(
        error,
        (JsonlProcessExited, JsonlProcessNotRunning, JsonlRequestTimeout),
    ):
        return ProviderCallResult(
            status=ProviderCallStatus.UNKNOWN,
            error_kind="provider_unknown",
            error_message=format_exception(error),
        )
    if isinstance(error, (JsonlProtocolError, AppServerProtocolError)):
        return ProviderCallResult(
            status=ProviderCallStatus.FAILED,
            error_kind="protocol",
            error_message=format_exception(error),
        )
    if isinstance(error, JsonlRemoteError):
        return ProviderCallResult(
            status=ProviderCallStatus.FAILED,
            error_kind="provider_failed",
            error_message=format_exception(error),
        )
    return ProviderCallResult(
        status=ProviderCallStatus.FAILED,
        error_kind="provider_failed",
        error_message=format_exception(error),
    )


__all__ = ["Runtime"]
