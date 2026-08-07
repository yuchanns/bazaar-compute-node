from __future__ import annotations

import asyncio
import os
import shutil
from dataclasses import dataclass, replace
from pathlib import Path
from time import time_ns

from ...core.approval import IApprovalHandler
from ...core.instruction import DeveloperInstructionContext
from ...core.lifecycle import IAsyncLifecycle
from ...core.models import (
    RuntimeEventState,
    RuntimeProcessState,
    RuntimeSession,
    RuntimeTurn,
)
from ...core.outcomes import ProviderCallResult, ProviderCallStatus
from ...core.paths import resolve_workspace_dir
from ...core.runtime import IRuntime, IRuntimeTurnStream, RuntimeCommandContext
from .client import (
    CodexAppServerClient,
    parse_thread_response,
    parse_turn_response,
)
from .events import CodexTurnEventStream
from .process import JsonlProcessSpec, JsonlProcessSupervisor
from .protocol import (
    CodexAppServerProtocolError,
    JsonlProcessExited,
    JsonlProcessNotRunning,
    JsonlProtocolError,
    JsonlRemoteError,
    JsonlRequestTimeout,
    JsonlTransportError,
)


@dataclass(slots=True)
class _CodexConnection:
    supervisor: JsonlProcessSupervisor
    client: CodexAppServerClient
    workspace: Path
    provider_thread_id: str
    active_turn_id: str | None = None


class CodexAppServerRuntime(IRuntime, IAsyncLifecycle):
    """Run one persistent Codex App Server process per bcn runtime session."""

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
        if not context.node_id:
            raise ValueError("runtime context node_id must be non-empty")
        if model is not None and not model:
            raise ValueError("model must be a non-empty string or None")
        if effort is not None and not effort:
            raise ValueError("effort must be a non-empty string or None")
        self._context = context
        self._executable = executable
        self._model = model
        self._effort = effort
        self._connections: dict[str, _CodexConnection] = {}
        self._started = False
        self._stopping = False

    async def start(self, *, timeout: float) -> None:
        del timeout
        if self._stopping:
            raise RuntimeError("Codex App Server runtime is stopping")
        self._started = True

    async def stop(self, *, timeout: float) -> None:
        if self._stopping:
            return
        self._stopping = True
        connections = tuple(self._connections.items())
        self._connections.clear()
        for _session_id, connection in connections:
            try:
                await connection.supervisor.stop(timeout=timeout)
            except asyncio.CancelledError:
                raise
            except OSError, TimeoutError, JsonlTransportError:
                continue
        self._started = False

    async def start_session(
        self, session: RuntimeSession, *, timeout: float
    ) -> ProviderCallResult[RuntimeSession]:
        self._ensure_started()
        existing = self._connections.pop(session.agent_runtime_session_id, None)
        if existing is not None:
            await self._stop_connection(existing, timeout=timeout)
        connection: _CodexConnection | None = None
        try:
            connection = await self._open_connection(session, timeout=timeout)
            response = await connection.client.start_thread(
                DeveloperInstructionContext(
                    node_id=self._context.node_id,
                    runtime_session_id=session.agent_runtime_session_id,
                    runtime=session.runtime_slug,
                    workspace=str(connection.workspace),
                ).render(),
                model=self._model,
                cwd=connection.workspace,
                timeout=timeout,
            )
            thread = parse_thread_response(response)
            connection.provider_thread_id = thread.thread_id
            self._connections[session.agent_runtime_session_id] = connection
            return ProviderCallResult(
                status=ProviderCallStatus.CONFIRMED,
                value=_running_session(
                    session, thread.thread_id, connection.supervisor.pid
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

    async def resume_session(
        self, session: RuntimeSession, *, timeout: float
    ) -> ProviderCallResult[RuntimeSession]:
        self._ensure_started()
        provider_thread_id = session.provider_thread_id
        if provider_thread_id is None:
            return ProviderCallResult(
                status=ProviderCallStatus.FAILED,
                error_kind="provider_failed",
                error_message="cannot resume a runtime session without a provider thread",
            )
        connection = self._connections.pop(session.agent_runtime_session_id, None)
        if connection is not None and not connection.supervisor.is_running:
            await self._stop_connection(connection, timeout=timeout)
            connection = None
        if connection is None:
            try:
                connection = await self._open_connection(session, timeout=timeout)
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001
                return _provider_result(error)
        try:
            response = await connection.client.resume_thread(
                provider_thread_id,
                model=self._model,
                cwd=connection.workspace,
                timeout=timeout,
            )
            thread = parse_thread_response(response)
            if thread.thread_id != provider_thread_id:
                raise CodexAppServerProtocolError(
                    "thread/resume returned a different provider thread"
                )
            connection.provider_thread_id = thread.thread_id
            self._connections[session.agent_runtime_session_id] = connection
            return ProviderCallResult(
                status=ProviderCallStatus.CONFIRMED,
                value=_running_session(
                    session, thread.thread_id, connection.supervisor.pid
                ),
                receipt={"provider_thread_id": thread.thread_id},
            )
        except asyncio.CancelledError:
            await self._stop_connection(connection, timeout=timeout)
            raise
        except Exception as error:  # noqa: BLE001
            await self._stop_connection(connection, timeout=timeout)
            return _provider_result(error)

    async def start_turn(
        self,
        session: RuntimeSession,
        turn: RuntimeTurn,
        input_text: str,
        approval_handler: IApprovalHandler,
        *,
        timeout: float,
    ) -> IRuntimeTurnStream:
        del approval_handler
        self._ensure_started()
        connection = self._connections.get(session.agent_runtime_session_id)
        if connection is None or not connection.supervisor.is_running:
            raise JsonlProcessNotRunning()
        if connection.active_turn_id is not None:
            raise RuntimeError(
                f"runtime session already has an active turn: {connection.active_turn_id}"
            )
        provider_turn_id: str | None = None
        initial_error: BaseException | None = None
        initial_error_kind = "provider_unknown"
        initial_error_state = RuntimeEventState.UNKNOWN
        try:
            response = await connection.client.start_turn(
                connection.provider_thread_id,
                input_text,
                client_user_message_id=turn.client_user_message_id,
                model=self._model,
                effort=self._effort,
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
            elif isinstance(error, CodexAppServerProtocolError):
                initial_error_kind = "protocol"
                initial_error_state = RuntimeEventState.FAILED
        connection.active_turn_id = turn.turn_id
        return CodexTurnEventStream(
            connection.supervisor,
            node_id=self._context.node_id,
            runtime_slug=session.runtime_slug,
            bcn_session_id=session.bcn_session_id,
            agent_runtime_session_id=session.agent_runtime_session_id,
            turn_id=turn.turn_id,
            provider_thread_id=connection.provider_thread_id,
            provider_turn_id=provider_turn_id,
            initial_error=initial_error,
            initial_error_kind=initial_error_kind,
            initial_error_state=initial_error_state,
            on_closed=lambda: self._clear_active_turn(
                session.agent_runtime_session_id, turn.turn_id
            ),
        )

    async def interrupt_turn(
        self,
        session: RuntimeSession,
        turn: RuntimeTurn,
        *,
        timeout: float,
    ) -> ProviderCallResult[RuntimeTurn]:
        self._ensure_started()
        connection = self._connections.get(session.agent_runtime_session_id)
        provider_turn_id = turn.provider_turn_id
        if connection is None or provider_turn_id is None:
            return ProviderCallResult(
                status=ProviderCallStatus.FAILED,
                error_kind="provider_failed",
                error_message="runtime turn has no active provider binding",
            )
        try:
            await connection.client.interrupt_turn(
                connection.provider_thread_id,
                provider_turn_id,
                timeout=timeout,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            result = _provider_result(error)
            return ProviderCallResult(
                status=result.status,
                error_kind=result.error_kind,
                error_message=result.error_message,
            )
        return ProviderCallResult(
            status=ProviderCallStatus.QUEUED,
            value=turn,
            receipt={
                "provider_thread_id": connection.provider_thread_id,
                "provider_turn_id": provider_turn_id,
            },
        )

    async def stop_session(
        self, session: RuntimeSession, *, timeout: float
    ) -> ProviderCallResult[RuntimeSession]:
        connection = self._connections.pop(session.agent_runtime_session_id, None)
        stopping = session
        if stopping.process_state not in {
            RuntimeProcessState.STOPPING,
            RuntimeProcessState.STOPPED,
        }:
            stopping = stopping.transition_process_to(
                RuntimeProcessState.STOPPING,
                updated_at_ms=_now_ms(),
            )
        if connection is None:
            if stopping.process_state is RuntimeProcessState.STOPPED:
                return ProviderCallResult(
                    status=ProviderCallStatus.CONFIRMED,
                    value=stopping,
                )
            stopped = stopping.transition_process_to(
                RuntimeProcessState.STOPPED,
                updated_at_ms=_now_ms(),
            )
            return ProviderCallResult(
                status=ProviderCallStatus.CONFIRMED,
                value=stopped,
            )
        try:
            await connection.supervisor.stop(timeout=timeout)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            return _provider_result(error)
        stopped = stopping.transition_process_to(
            stopping.process_state.STOPPED,
            updated_at_ms=_now_ms(),
        )
        return ProviderCallResult(
            status=ProviderCallStatus.CONFIRMED,
            value=stopped,
        )

    async def _open_connection(
        self,
        session: RuntimeSession,
        *,
        timeout: float,
    ) -> _CodexConnection:
        executable = shutil.which(self._executable)
        if executable is None:
            raise FileNotFoundError(f"Codex executable not found: {self._executable}")
        workspace = resolve_workspace_dir(session.workspace_id)
        await asyncio.to_thread(
            workspace.mkdir, parents=True, exist_ok=True, mode=0o700
        )
        if os.name != "nt":
            await asyncio.to_thread(workspace.chmod, 0o700)
        environment = dict(os.environ)
        if self._context.environment_for_session is not None:
            environment.update(self._context.environment_for_session(session))
        supervisor = JsonlProcessSupervisor(
            JsonlProcessSpec(
                executable=executable,
                arguments=("app-server", "--stdio"),
                cwd=workspace,
                environment=environment,
            )
        )
        client = CodexAppServerClient(supervisor)
        await supervisor.start(timeout=timeout)
        try:
            await client.initialize(
                client_info=self._context.client_info,
                timeout=timeout,
            )
        except BaseException:
            await supervisor.stop(timeout=timeout)
            raise
        return _CodexConnection(
            supervisor=supervisor,
            client=client,
            workspace=workspace,
            provider_thread_id=session.provider_thread_id or "",
        )

    async def _stop_connection(
        self,
        connection: _CodexConnection,
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


def _running_session(
    session: RuntimeSession,
    provider_thread_id: str,
    process_id: int | None,
) -> RuntimeSession:
    updated = replace(
        session,
        provider_thread_id=provider_thread_id,
        process_id=process_id,
    )
    if updated.process_state is not RuntimeProcessState.RUNNING:
        updated = updated.transition_process_to(
            RuntimeProcessState.RUNNING,
            updated_at_ms=_now_ms(),
        )
    return updated


def _provider_result(error: BaseException) -> ProviderCallResult[RuntimeSession]:
    if isinstance(
        error, (JsonlProcessExited, JsonlProcessNotRunning, JsonlRequestTimeout)
    ):
        return ProviderCallResult(
            status=ProviderCallStatus.UNKNOWN,
            error_kind="provider_unknown",
            error_message=_safe_error_message(error),
        )
    if isinstance(error, (JsonlProtocolError, CodexAppServerProtocolError)):
        return ProviderCallResult(
            status=ProviderCallStatus.FAILED,
            error_kind="protocol",
            error_message=_safe_error_message(error),
        )
    if isinstance(error, JsonlRemoteError):
        return ProviderCallResult(
            status=ProviderCallStatus.FAILED,
            error_kind="provider_failed",
            error_message=_safe_error_message(error),
        )
    return ProviderCallResult(
        status=ProviderCallStatus.FAILED,
        error_kind="provider_failed",
        error_message=_safe_error_message(error),
    )


def _safe_error_message(error: BaseException) -> str:
    message = str(error).strip()
    return message or type(error).__name__


def _now_ms() -> int:
    return time_ns() // 1_000_000


__all__ = ["CodexAppServerRuntime"]
