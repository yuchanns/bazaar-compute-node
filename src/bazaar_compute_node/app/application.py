from __future__ import annotations

import asyncio
import hmac
import math
import os
import re
import secrets
import signal
from collections.abc import Mapping, Sequence
from pathlib import Path

from ..core.channel import ChannelContext, IChannel
from ..core.concurrency import SessionLockRegistry
from ..core.lifecycle import TimeoutBudget
from ..core.models import RuntimeSession
from ..core.observability import IAudit
from ..core.orchestration import ReminderScheduler, SessionOrchestrator
from ..core.paths import resolve_data_dir, resolve_workspace_dir
from ..core.runtime import IRuntime, RuntimeCommandContext, RuntimeSandboxMode
from ..core.storage import IStorage, NodeIdentity
from ..core.timerwheel import TimerWheel
from .attachments import AttachmentMaterializer
from .command import (
    CommandDispatcher,
    CommandDispatchError,
)
from .registry import AdapterFactories
from .transport import LocalCommandServer
from .wrapper import install_bcc_wrapper, remove_bcc_wrapper

CommandRecord = tuple[str, tuple[str, ...]]

_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PLATFORM_ENVIRONMENT = {
    "HOME",
    "LANG",
    "LC_ALL",
    "PATH",
    "TMPDIR",
    "TEMP",
    "TMP",
    "SystemRoot",
    "ComSpec",
    "PATHEXT",
    "USERPROFILE",
}
_FORBIDDEN_ENVIRONMENT = {
    "BCN_WECOM_BOT_SECRET",
    "DATABASE_URL",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "OPENAI_API_KEY",
}


class NodeApplication:
    """Generic application lifecycle for one dynamically composed node."""

    def __init__(
        self,
        *,
        factories: AdapterFactories,
        endpoint_path: Path | None = None,
        node_id: str = "bcn-node",
        workspace_id: str | None = None,
        runtime_options: Mapping[str, str] | None = None,
        runtime_sandbox_mode: RuntimeSandboxMode = RuntimeSandboxMode.WORKSPACE_WRITE,
        runtime_network_access: bool = True,
        runtime_idle_timeout_seconds: float = 0,
        channel_options: Mapping[str, object] | None = None,
        runtime_environment_include: Sequence[str] = (),
        timeout_budget: TimeoutBudget | None = None,
    ) -> None:
        self.data_dir = resolve_data_dir()
        self.runtime_options = dict(runtime_options or {})
        self.timeout_budget = timeout_budget or TimeoutBudget(
            startup_seconds=60,
            provider_call_seconds=600,
            command_seconds=10,
            shutdown_seconds=5,
        )
        self.storage: IStorage = factories.storage()
        self.audit: IAudit = factories.audit()
        self.command_log: list[CommandRecord] = []
        self._wrapper_path: Path | None = None
        self._identity: NodeIdentity | None = None
        self._session_capabilities: dict[str, tuple[str, str]] = {}
        self._concurrency = SessionLockRegistry()
        self._started = False
        self._stopped = asyncio.Event()
        self._runtime_environment_include = tuple(runtime_environment_include)
        self._attachment_materializer = AttachmentMaterializer(
            self._workspace_path, self._referenced_attachment_paths
        )
        self.channel: IChannel = factories.channel(
            ChannelContext(
                attachments=self._attachment_materializer,
                options=dict(channel_options or {}),
                workspace=self._workspace_path,
            )
        )
        self._runtime_context = RuntimeCommandContext(
            run_command=self._run_runtime_command,
            environment_for_session=self._runtime_environment,
            node_id=node_id,
            runtime_options=self.runtime_options,
            sandbox_mode=runtime_sandbox_mode,
            network_access=runtime_network_access,
            startup_timeout_seconds=self.timeout_budget.startup_seconds,
        )
        self.runtime: IRuntime = factories.runtime(self._runtime_context)
        if (
            isinstance(runtime_idle_timeout_seconds, bool)
            or not isinstance(runtime_idle_timeout_seconds, int | float)
            or not math.isfinite(runtime_idle_timeout_seconds)
        ):
            raise ValueError("runtime_idle_timeout_seconds must be a finite number")
        self.timer_wheel = TimerWheel()
        if (
            runtime_idle_timeout_seconds > 0
            and runtime_idle_timeout_seconds * 1_000 > self.timer_wheel.maximum_delay_ms
        ):
            raise ValueError("runtime_idle_timeout_seconds exceeds the timer horizon")
        runtime_idle_timeout_ms = (
            math.ceil(runtime_idle_timeout_seconds * 1_000)
            if runtime_idle_timeout_seconds > 0
            else 0
        )
        self.orchestrator = SessionOrchestrator(
            node_id=node_id,
            workspace_id=workspace_id,
            channel=self.channel,
            runtime=self.runtime,
            storage=self.storage,
            audit=self.audit,
            timeout_budget=self.timeout_budget,
            timer_wheel=self.timer_wheel,
            runtime_idle_timeout_ms=runtime_idle_timeout_ms,
            workspace=self._workspace_path,
            concurrency=self._concurrency,
            on_node_initialized=self._ensure_workspace,
        )
        self.reminder_scheduler = ReminderScheduler(
            storage=self.storage,
            timer_wheel=self.timer_wheel,
            concurrency=self._concurrency,
            publish_wake=self.orchestrator.publish_reminder_wake,
        )
        self.command_service = self.orchestrator.command_service
        control_handler = None
        if factories.control is not None:
            control_handler = factories.control(self._adapter_context())
        self.command_dispatcher = CommandDispatcher(
            self.command_service,
            timeout_budget=self.timeout_budget,
            control_handler=self._handle_control,
            session_binding_validator=self._validate_session_binding,
        )
        self.command_server = LocalCommandServer(
            self.command_dispatcher,
            endpoint_path=endpoint_path,
        )
        self._provider_control_handler = control_handler

    @property
    def endpoint(self) -> str:
        return self.command_server.endpoint

    async def start(self) -> None:
        if self._started:
            return
        await asyncio.to_thread(self.data_dir.mkdir, parents=True, exist_ok=True)
        if os.name != "nt":
            await asyncio.to_thread(self.data_dir.chmod, 0o700)
        self._wrapper_path = await asyncio.to_thread(
            install_bcc_wrapper, self.data_dir / "bin"
        )
        try:
            await self.timer_wheel.start()
            await self.command_server.start()
            await self.orchestrator.start(
                timeout=self.timeout_budget.startup_seconds,
            )
            self._started = True
            await self.reminder_scheduler.start(
                timeout=self.timeout_budget.startup_seconds,
            )
        except BaseException:
            try:
                await self.stop()
            finally:
                await self.timer_wheel.close()
            raise
        self._stopped.clear()
        self.command_dispatcher.start_accepting()

    async def stop(self) -> None:
        if not self._started:
            try:
                await self.reminder_scheduler.stop(
                    timeout=self.timeout_budget.shutdown_seconds,
                )
            finally:
                try:
                    await self.command_dispatcher.drain(
                        timeout=self.timeout_budget.shutdown_seconds,
                    )
                finally:
                    try:
                        await self.command_server.stop()
                    finally:
                        try:
                            await self._cleanup_bcc_wrapper()
                        finally:
                            await self.timer_wheel.close()
            return
        self._started = False
        self.command_dispatcher.stop_accepting()
        try:
            await self.command_dispatcher.drain(
                timeout=self.timeout_budget.shutdown_seconds,
            )
        finally:
            try:
                await self.reminder_scheduler.stop(
                    timeout=self.timeout_budget.shutdown_seconds,
                )
            finally:
                try:
                    await self.orchestrator.stop(
                        timeout=self.timeout_budget.shutdown_seconds,
                    )
                finally:
                    try:
                        await self.command_server.stop()
                    finally:
                        try:
                            await self._cleanup_bcc_wrapper()
                        finally:
                            try:
                                await self.timer_wheel.close()
                            finally:
                                self._stopped.set()

    async def _cleanup_bcc_wrapper(self) -> None:
        wrapper_path = self._wrapper_path
        if wrapper_path is None:
            return
        await asyncio.to_thread(remove_bcc_wrapper, wrapper_path)
        self._wrapper_path = None

    async def wait(self) -> None:
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(signum, self._stopped.set)
            except NotImplementedError, RuntimeError:
                pass
        await self._stopped.wait()

    async def _ensure_workspace(self, identity: NodeIdentity) -> None:
        self._identity = identity
        workspace_dir = resolve_workspace_dir(identity.workspace_id)
        await asyncio.to_thread(
            workspace_dir.mkdir,
            parents=True,
            exist_ok=True,
            mode=0o700,
        )
        if os.name != "nt":
            await asyncio.to_thread(workspace_dir.chmod, 0o700)
        await self._attachment_materializer.reconcile()

    def _workspace_path(self) -> Path:
        identity = self._identity
        if identity is None:
            raise RuntimeError("node identity has not been initialized")
        return resolve_workspace_dir(identity.workspace_id)

    async def _referenced_attachment_paths(self) -> set[str]:
        async with self.storage.transaction() as transaction:
            return set(await transaction.list_ready_attachment_paths())

    def _adapter_context(self) -> Mapping[str, object]:
        return {
            "channel": self.channel,
            "runtime": self.runtime,
            "storage": self.storage,
            "audit": self.audit,
            "command_log": self.command_log,
            "is_started": lambda: self._started,
        }

    async def _handle_control(
        self, request: Mapping[str, object]
    ) -> Mapping[str, object]:
        if request.get("operation") == "health":
            identity = self._identity
            return {
                "started": self._started,
                "accepting": self.command_dispatcher.accepting,
                "channel": self.channel.name,
                "channel_health": dict(self.channel.health),
                "runtime": self.runtime.name,
                "storage": self.storage.name,
                "audit": self.audit.name,
                "node_id": identity.node_id if identity is not None else None,
                "workspace_id": (
                    identity.workspace_id if identity is not None else None
                ),
            }
        if request.get("operation") == "shutdown":
            self._stopped.set()
            return {"accepted": True, "operation": "shutdown"}
        if self._provider_control_handler is None:
            raise ValueError("control operation is not supported")
        return await self._provider_control_handler(request)

    async def _validate_session_binding(
        self,
        session_id: str,
        request: Mapping[str, object],
    ) -> None:
        runtime_session_id = request.get("runtime_session_id")
        session_capability = request.get("session_capability")
        async with self.storage.transaction() as transaction:
            bcn_session = await transaction.get_bcn_session(session_id)
            if bcn_session is None:
                raise CommandDispatchError(
                    "SESSION_NOT_FOUND",
                    f"unknown bcn session: {session_id}",
                )
        runtime_session = self.orchestrator.runtime_session(session_id)
        if runtime_session is None or runtime_session_id != runtime_session.id:
            raise CommandDispatchError(
                "SESSION_BINDING_FAILED",
                "runtime session binding is invalid",
            )
        capability_binding = self._session_capabilities.get(session_id)
        if (
            capability_binding is None
            or capability_binding[0] != runtime_session.id
            or not isinstance(session_capability, str)
            or not hmac.compare_digest(
                session_capability.encode(), capability_binding[1].encode()
            )
        ):
            raise CommandDispatchError(
                "SESSION_BINDING_FAILED",
                "session capability is invalid",
            )

    def _runtime_environment(self, session: RuntimeSession) -> Mapping[str, str]:
        runtime_session = self.orchestrator.runtime_session(session.bcn_session_id)
        if runtime_session is None or runtime_session.id != session.id:
            raise RuntimeError("runtime session is not the current live binding")
        return self._build_command_environment(
            session.bcn_session_id,
            session.id,
        )

    def _build_command_environment(
        self,
        session_id: str,
        runtime_session_id: str,
    ) -> dict[str, str]:
        if not session_id:
            raise ValueError("session_id must be a non-empty string")
        if not runtime_session_id:
            raise ValueError("runtime_session_id must be a non-empty string")
        wrapper_path = self._wrapper_path
        if wrapper_path is None:
            raise RuntimeError("bcc wrapper is not installed")
        capability_binding = self._session_capabilities.get(session_id)
        if capability_binding is None or capability_binding[0] != runtime_session_id:
            capability_binding = (runtime_session_id, secrets.token_urlsafe(32))
            self._session_capabilities[session_id] = capability_binding
        session_capability = capability_binding[1]
        wrapper_directory = str(wrapper_path.parent)
        allowed = set(_PLATFORM_ENVIRONMENT)
        for name in self.runtime.environment_variable_names():
            if not _ENVIRONMENT_NAME.fullmatch(name):
                raise ValueError(f"runtime environment name is invalid: {name}")
            if name.startswith("BCN_") or name in _FORBIDDEN_ENVIRONMENT:
                raise ValueError(f"runtime environment name is reserved: {name}")
            allowed.add(name)
        for name in self._runtime_environment_include:
            if not _ENVIRONMENT_NAME.fullmatch(name):
                raise ValueError(f"runtime environment name is invalid: {name}")
            if name.startswith("BCN_") or name in _FORBIDDEN_ENVIRONMENT:
                raise ValueError(f"runtime environment name is reserved: {name}")
            if name not in os.environ:
                raise ValueError(f"runtime environment variable is missing: {name}")
            allowed.add(name)
        environment = {
            name: os.environ[name] for name in sorted(allowed) if os.environ.get(name)
        }
        environment["PATH"] = os.pathsep.join(
            (wrapper_directory, environment.get("PATH", os.defpath))
        )
        environment.update(
            {
                "BCN_ENDPOINT": self.endpoint,
                "BCN_SESSION_ID": session_id,
                "BCN_RUNTIME_SESSION_ID": runtime_session_id,
                "BCN_COMMAND_CAPABILITY": session_capability,
            }
        )
        return environment

    async def _run_runtime_command(
        self,
        session_id: str,
        arguments: Sequence[str],
        body: str | None,
    ) -> None:
        if not session_id:
            raise ValueError("session_id must be a non-empty string")
        if not arguments or any(
            not isinstance(argument, str) for argument in arguments
        ):
            raise ValueError("runtime command arguments must be non-empty text")
        wrapper_path = self._wrapper_path
        if wrapper_path is None:
            raise RuntimeError("bcc wrapper is not installed")
        self.command_log.append((session_id, tuple(arguments)))
        runtime_session = self.orchestrator.runtime_session(session_id)
        if runtime_session is None:
            raise RuntimeError("runtime session is not live")
        environment = self._build_command_environment(
            session_id,
            runtime_session.id,
        )
        process = await asyncio.create_subprocess_exec(
            str(wrapper_path),
            *arguments,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=environment,
        )
        input_data = body.encode() if body is not None else None
        try:
            _stdout, stderr = await process.communicate(input=input_data)
        except asyncio.CancelledError:
            if process.returncode is None:
                process.terminate()
                await process.wait()
            raise
        if process.returncode != 0:
            error = stderr.decode(errors="replace").strip()
            command = " ".join(arguments)
            raise RuntimeError(f"bcc command failed ({command}): {error}")


__all__ = ["CommandRecord", "NodeApplication"]
