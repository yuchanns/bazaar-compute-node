from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from dataclasses import dataclass, field, replace
from pathlib import Path
from time import time_ns
from typing import Any
from uuid import uuid4

from ...core.approval import IApprovalHandler
from ...core.instruction import DeveloperInstructionContext
from ...core.lifecycle import IAsyncLifecycle
from ...core.models import (
    RuntimeEventState,
    RuntimeSession,
    RuntimeTurn,
    RuntimeTurnState,
    SessionRuntimeState,
)
from ...core.outcomes import ProviderCallResult, ProviderCallStatus
from ...core.paths import resolve_workspace_dir
from ...core.runtime import (
    IRuntime,
    IRuntimeTurnStream,
    RuntimeCommandContext,
    RuntimeExpire,
    RuntimeSandboxMode,
    RuntimeSessionReconciliation,
    RuntimeSessionUnavailable,
)
from .approval import (
    build_approval_response,
    deny_approval,
    parse_approval_request,
)
from .client import Client
from .events import TurnEventStream
from .process import ProcessSpec, ProcessSupervisor, build_arguments
from .protocol import ClaudeControlError, ClaudeProtocolError, ClaudeTransportError

_MINIMUM_VERSION = (2, 1, 239)
_VERSION_PATTERN = re.compile(r"(?<!\d)(\d+)\.(\d+)\.(\d+)(?!\d)")
_UNUSABLE_CONNECTION_STOP_SECONDS = 10.0


@dataclass(slots=True)
class _Connection:
    supervisor: ProcessSupervisor
    client: Client
    workspace: Path
    provider_thread_id: str
    claude_version: tuple[int, int, int]
    state_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    active_turn_id: str | None = None
    pending_human_results: int = 0
    active_stream: TurnEventStream | None = None
    active_background_task_ids: set[str] = field(default_factory=set)


class Runtime(IRuntime, IAsyncLifecycle):
    """Run one persistent external Claude Code process per runtime session."""

    def __init__(
        self,
        context: RuntimeCommandContext,
        *,
        model: str | None = None,
        effort: str | None = None,
    ) -> None:
        self._context = context
        self._model = model
        self._effort = effort
        self._connections: dict[str, _Connection] = {}
        self._expire_events: asyncio.Queue[RuntimeExpire] = asyncio.Queue()
        self._started = False
        self._stopping = False

    @property
    def name(self) -> str:
        return "claudecode"

    def environment_variable_names(self) -> tuple[str, ...]:
        return (
            "CLAUDE_CONFIG_DIR",
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_BASE_URL",
            "CLAUDE_CODE_USE_BEDROCK",
            "CLAUDE_CODE_USE_VERTEX",
            "CLAUDE_CODE_USE_FOUNDRY",
            "CLAUDE_CODE_USE_MANTLE",
            "SSL_CERT_FILE",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "NO_PROXY",
        )

    async def start(self, *, timeout: float) -> None:
        del timeout
        if self._stopping:
            raise RuntimeError("Claude Code runtime is stopping")
        self._started = True

    async def stop(self, *, timeout: float) -> None:
        if self._stopping:
            return
        self._stopping = True
        connections = tuple(self._connections.values())
        self._connections.clear()
        for connection in connections:
            try:
                await self._stop_connection(connection, timeout=timeout)
            except asyncio.CancelledError:
                raise
            except OSError, TimeoutError, ClaudeTransportError:
                continue
        self._started = False

    async def receive_expire(self) -> RuntimeExpire:
        return await self._expire_events.get()

    async def start_session(
        self, session: RuntimeSession, *, timeout: float
    ) -> ProviderCallResult[RuntimeSession]:
        self._ensure_started()
        existing = self._connections.pop(session.id, None)
        if existing is not None:
            await self._stop_connection(existing, timeout=timeout)
        provider_thread_id = str(uuid4())
        connection: _Connection | None = None
        try:
            connection = await self._open_connection(
                session,
                provider_thread_id=provider_thread_id,
                resume=False,
                timeout=timeout,
            )
            self._connections[session.id] = connection
            updated = replace(
                session,
                provider_thread_id=provider_thread_id,
                updated_at_ms=time_ns() // 1_000_000,
            )
            return ProviderCallResult(
                status=ProviderCallStatus.CONFIRMED,
                value=updated,
                receipt={"provider_thread_id": provider_thread_id},
            )
        except asyncio.CancelledError:
            if connection is not None:
                await self._stop_connection(connection, timeout=timeout)
            raise
        except Exception as error:  # noqa: BLE001
            if connection is not None:
                await self._stop_connection(connection, timeout=timeout)
            return _provider_result(error)

    async def reconcile_session(
        self,
        session: RuntimeSession,
        turn: RuntimeTurn | None,
        approval_handler: IApprovalHandler | None,
        *,
        timeout: float,
    ) -> ProviderCallResult[RuntimeSessionReconciliation]:
        del turn, approval_handler
        self._ensure_started()
        provider_thread_id = session.provider_thread_id
        if provider_thread_id is None:
            return ProviderCallResult(
                status=ProviderCallStatus.FAILED,
                error_kind="provider_failed",
                error_message="cannot reconcile without a provider thread",
            )
        existing = self._connections.pop(session.id, None)
        if existing is not None:
            await self._stop_connection(existing, timeout=timeout)
        connection: _Connection | None = None
        try:
            connection = await self._open_connection(
                session,
                provider_thread_id=provider_thread_id,
                resume=True,
                timeout=timeout,
            )
            self._connections[session.id] = connection
            return ProviderCallResult(
                status=ProviderCallStatus.CONFIRMED,
                value=RuntimeSessionReconciliation(
                    session=session,
                    state=SessionRuntimeState.IDLE,
                ),
                receipt={"provider_thread_id": provider_thread_id},
            )
        except asyncio.CancelledError:
            if connection is not None:
                await self._stop_connection(connection, timeout=timeout)
            raise
        except Exception as error:  # noqa: BLE001
            if connection is not None:
                await self._stop_connection(connection, timeout=timeout)
            return _provider_result(error)

    async def stop_session(
        self, session: RuntimeSession, *, timeout: float
    ) -> ProviderCallResult[RuntimeSession]:
        connection = self._connections.pop(session.id, None)
        if connection is not None:
            await self._stop_connection(connection, timeout=timeout)
        return ProviderCallResult(status=ProviderCallStatus.CONFIRMED, value=session)

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
            raise RuntimeSessionUnavailable("Claude Code process is not running")
        initial_error: BaseException | None = None
        initial_error_state = RuntimeEventState.UNKNOWN
        initial_error_kind = "provider_unknown"
        inbox = None
        async with connection.state_lock:
            if connection.active_turn_id is not None:
                raise RuntimeError(
                    f"runtime session already has an active turn: "
                    f"{connection.active_turn_id}"
                )
            connection.active_turn_id = turn.turn_id
            connection.pending_human_results = 1

            async def handle_permission(
                message: dict[str, object],
            ) -> dict[str, object]:
                approval = parse_approval_request(
                    message,
                    session_id=session.bcn_session_id,
                    runtime_session_id=session.id,
                    turn_id=turn.turn_id,
                )
                try:
                    async with asyncio.timeout(timeout):
                        result = await approval_handler.request_approval(
                            approval.request, timeout=timeout
                        )
                except TimeoutError:
                    return deny_approval("Permission request timed out.")
                return build_approval_response(approval, result)

            connection.client.set_control_request_handler(handle_permission)
            try:
                (
                    inbox,
                    initial_error,
                ) = await connection.client.open_turn(input_text, timeout=timeout)
            except asyncio.CancelledError:
                connection.active_turn_id = None
                connection.pending_human_results = 0
                await connection.client.clear_control_request_handler(handle_permission)
                raise
            if initial_error is not None:
                await connection.client.clear_control_request_handler(handle_permission)
                if isinstance(initial_error, ClaudeControlError):
                    initial_error_state = RuntimeEventState.FAILED
                    initial_error_kind = "provider_failed"
                elif isinstance(initial_error, ClaudeProtocolError):
                    initial_error_state = RuntimeEventState.FAILED
                    initial_error_kind = "protocol"
        assert inbox is not None

        async def claim_result(message: dict[str, object]) -> bool:
            del message
            async with connection.state_lock:
                if connection.active_turn_id != turn.turn_id:
                    return False
                if connection.pending_human_results > 1:
                    connection.pending_human_results -= 1
                    return False
                connection.pending_human_results = 0
                return True

        async def close_turn() -> None:
            await connection.client.close_turn(inbox)
            async with connection.state_lock:
                if connection.active_turn_id == turn.turn_id:
                    connection.active_turn_id = None
                    connection.pending_human_results = 0
                    connection.active_stream = None

        async def retire_connection(error: BaseException) -> None:
            del error
            await self._retire_connection(session.id, connection)

        stream = TurnEventStream(
            inbox,
            session_id=session.bcn_session_id,
            turn_id=turn.turn_id,
            provider_thread_id=connection.provider_thread_id,
            claude_version=connection.claude_version,
            claim_result=claim_result,
            on_closed=close_turn,
            on_unusable=retire_connection,
            initial_error=initial_error,
            initial_error_state=initial_error_state,
            initial_error_kind=initial_error_kind,
        )
        async with connection.state_lock:
            connection.active_stream = stream
        return stream

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
        if connection is None or not connection.supervisor.is_running:
            return False
        async with connection.state_lock:
            if connection.active_turn_id != turn.turn_id:
                return False
            try:
                await connection.client.send_user_message(input_text, timeout=timeout)
            except asyncio.CancelledError:
                raise
            except ClaudeTransportError:
                return False
            connection.pending_human_results += 1
            return True

    async def interrupt_turn(
        self,
        session: RuntimeSession,
        turn: RuntimeTurn,
        *,
        timeout: float,
    ) -> ProviderCallResult[RuntimeTurn]:
        self._ensure_started()
        connection = self._connections.get(session.id)
        if connection is None:
            return ProviderCallResult(
                status=ProviderCallStatus.FAILED,
                error_kind="provider_failed",
                error_message="runtime turn has no active provider binding",
            )
        async with connection.state_lock:
            if connection.active_turn_id != turn.turn_id:
                return ProviderCallResult(
                    status=ProviderCallStatus.FAILED,
                    error_kind="provider_failed",
                    error_message="runtime turn has no active provider binding",
                )
            stream = connection.active_stream
        if stream is None:
            return ProviderCallResult(
                status=ProviderCallStatus.FAILED,
                error_kind="provider_failed",
                error_message="runtime turn stream is unavailable",
            )
        try:
            await connection.client.control({"subtype": "interrupt"}, timeout=timeout)
            terminal = await stream.wait_terminal(timeout=timeout)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            result = _provider_result(error)
            return ProviderCallResult(
                status=result.status,
                error_kind=result.error_kind,
                error_message=result.error_message,
            )
        state = {
            RuntimeEventState.COMPLETED: RuntimeTurnState.COMPLETED,
            RuntimeEventState.FAILED: RuntimeTurnState.FAILED,
            RuntimeEventState.CANCELLED: RuntimeTurnState.CANCELLED,
            RuntimeEventState.UNKNOWN: RuntimeTurnState.UNKNOWN,
        }.get(terminal.state)
        if state is None:
            return ProviderCallResult(
                status=ProviderCallStatus.UNKNOWN,
                error_kind="provider_unknown",
                error_message="interrupt completed without a terminal result",
            )
        return ProviderCallResult(
            status=ProviderCallStatus.CONFIRMED,
            value=turn.transition_to(
                state,
                at_ms=time_ns() // 1_000_000,
                error_kind=terminal.error_kind,
                error_message=terminal.error_message,
                latest_event_name=terminal.event_name,
            ),
            receipt={"provider_thread_id": connection.provider_thread_id},
        )

    async def has_background_job(
        self, session: RuntimeSession, *, timeout: float
    ) -> bool:
        del timeout
        connection = self._connections.get(session.id)
        if connection is None:
            return False
        async with connection.state_lock:
            return bool(connection.active_background_task_ids)

    async def _open_connection(
        self,
        session: RuntimeSession,
        *,
        provider_thread_id: str,
        resume: bool,
        timeout: float,
    ) -> _Connection:
        if session.workspace_id != self._context.agent_id:
            raise ValueError("runtime session workspace does not match Agent identity")
        executable = await asyncio.to_thread(shutil.which, "claude")
        if executable is None:
            raise FileNotFoundError("Claude executable not found: claude")
        workspace = resolve_workspace_dir(self._context.agent_id)
        await asyncio.to_thread(
            workspace.mkdir, parents=True, exist_ok=True, mode=0o700
        )
        if os.name != "nt":
            await asyncio.to_thread(workspace.chmod, 0o700)
        environment = dict(self._context.environment_for_session(session))
        deadline = asyncio.get_running_loop().time() + min(
            timeout, self._context.startup_timeout_seconds
        )
        claude_version = await _check_version(
            executable,
            workspace,
            environment,
            timeout=_remaining(deadline),
        )
        prompt = DeveloperInstructionContext(
            agent_name=self._context.agent_name,
            bot_name=self._context.bot_name(),
            agent_id=self._context.agent_id,
            runtime_session_id=session.id,
            runtime=session.runtime,
            workspace=str(workspace),
        ).render()
        settings = json.dumps(
            _sandbox_settings(
                self._context.sandbox_mode,
                network_access=self._context.network_access,
            ),
            separators=(",", ":"),
        )
        arguments = build_arguments(
            system_prompt=prompt,
            settings=settings,
            session_id=None if resume else provider_thread_id,
            resume=provider_thread_id if resume else None,
            model=self._model,
            effort=self._effort,
        )
        supervisor = ProcessSupervisor(
            ProcessSpec(
                executable=executable,
                arguments=arguments,
                cwd=workspace,
                environment=environment,
            )
        )
        client = Client(supervisor)
        try:
            await supervisor.start(timeout=_remaining(deadline))
            await client.initialize(timeout=_remaining(deadline))
        except BaseException:
            await supervisor.stop(timeout=timeout)
            await client.close()
            raise
        connection = _Connection(
            supervisor,
            client,
            workspace,
            provider_thread_id,
            claude_version,
        )
        client.set_message_observer(
            lambda message: _observe_background(connection, message)
        )
        return connection

    async def _stop_connection(
        self, connection: _Connection, *, timeout: float
    ) -> None:
        try:
            await connection.supervisor.stop(timeout=timeout)
        finally:
            await connection.client.close()

    async def _retire_connection(
        self, session_id: str, connection: _Connection
    ) -> None:
        if self._connections.get(session_id) is not connection:
            return
        self._connections.pop(session_id, None)
        try:
            await self._stop_connection(
                connection, timeout=_UNUSABLE_CONNECTION_STOP_SECONDS
            )
        except asyncio.CancelledError:
            raise
        except OSError, TimeoutError, ClaudeTransportError:
            return

    def _ensure_started(self) -> None:
        if not self._started or self._stopping:
            raise RuntimeError("Claude Code runtime is not started")


async def _check_version(
    executable: str,
    cwd: Path,
    environment: dict[str, str],
    *,
    timeout: float,
) -> tuple[int, int, int]:
    process = await asyncio.create_subprocess_exec(
        executable,
        "--version",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd),
        env=environment,
    )
    try:
        async with asyncio.timeout(timeout):
            stdout, stderr = await process.communicate()
    except BaseException:
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise
    text = stdout.decode(errors="replace").strip()
    match = _VERSION_PATTERN.search(text)
    if process.returncode != 0 or match is None:
        detail = stderr.decode(errors="replace").strip() or text
        raise ClaudeProtocolError(f"cannot determine Claude Code version: {detail}")
    version = (int(match[1]), int(match[2]), int(match[3]))
    if version < _MINIMUM_VERSION:
        raise ClaudeProtocolError(
            f"Claude Code {text} is below required version 2.1.239"
        )
    return version


def _sandbox_settings(
    mode: RuntimeSandboxMode, *, network_access: bool
) -> dict[str, object]:
    if mode is RuntimeSandboxMode.DANGER_FULL_ACCESS:
        return {"sandbox": {"enabled": False}}
    sandbox: dict[str, object] = {
        "enabled": True,
        "failIfUnavailable": True,
        "autoAllowBashIfSandboxed": True,
        "allowUnsandboxedCommands": True,
    }
    settings: dict[str, object] = {"sandbox": sandbox}
    if not network_access:
        sandbox["network"] = {"allowedDomains": []}
        settings["permissions"] = {"deny": ["WebFetch", "WebSearch"]}
    return settings


def _observe_background(connection: _Connection, message: dict[str, object]) -> None:
    if message.get("type") != "system":
        return
    subtype = message.get("subtype")
    task_id = message.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        return
    if subtype == "task_started" and message.get("task_type") in {
        "local_agent",
        "local_workflow",
    }:
        connection.active_background_task_ids.add(task_id)
        return
    if subtype == "task_notification":
        connection.active_background_task_ids.discard(task_id)
        return
    if subtype != "task_updated":
        return
    patch = message.get("patch")
    if isinstance(patch, dict) and patch.get("status") in {
        "completed",
        "failed",
        "stopped",
        "killed",
    }:
        connection.active_background_task_ids.discard(task_id)


def _provider_result(error: BaseException) -> ProviderCallResult[Any]:
    if isinstance(error, ClaudeControlError):
        return ProviderCallResult(
            status=ProviderCallStatus.FAILED,
            error_kind="provider_failed",
            error_message=str(error) or type(error).__name__,
        )
    if isinstance(error, ClaudeProtocolError):
        return ProviderCallResult(
            status=ProviderCallStatus.FAILED,
            error_kind="protocol",
            error_message=str(error) or type(error).__name__,
        )
    if isinstance(error, TimeoutError | ClaudeTransportError):
        return ProviderCallResult(
            status=ProviderCallStatus.UNKNOWN,
            error_kind="provider_unknown",
            error_message=str(error) or type(error).__name__,
        )
    return ProviderCallResult(
        status=ProviderCallStatus.FAILED,
        error_kind="provider_failed",
        error_message=str(error) or type(error).__name__,
    )


def _remaining(deadline: float) -> float:
    remaining = deadline - asyncio.get_running_loop().time()
    if remaining <= 0:
        raise TimeoutError
    return remaining


__all__ = ["Runtime"]
