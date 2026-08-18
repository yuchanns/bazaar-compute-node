from __future__ import annotations

import asyncio
import hmac
import logging
import math
import os
import re
import secrets
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from ..core.channel import AgentScopedChannel, ChannelContext, IChannel
from ..core.concurrency import ISessionConcurrency, SessionLockRegistry
from ..core.lifecycle import TimeoutBudget
from ..core.models import RuntimeSession
from ..core.observability import IAudit
from ..core.orchestration import SessionOrchestrator
from ..core.orchestration.reminder_command import ReminderCommandService
from ..core.paths import resolve_workspace_dir
from ..core.runtime import IRuntime, RuntimeCommandContext
from ..core.storage import IStorageScope
from ..core.timerwheel import TimerWheel
from ..i18n import Translator
from .attachments import AttachmentMaterializer
from .command import CommandDispatchError
from .config import AgentConfiguration
from .registry import AgentAdapterFactories
from .reminder_dispatch import CommandDispatcher
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


@dataclass(frozen=True, slots=True)
class _SessionCapabilityBinding:
    agent_id: str
    bcn_session_id: str
    runtime_session_id: str
    capability: str
    token_values: tuple[str, ...]


class AgentApplication:
    """Process-local composition for one configured BCN Agent."""

    def __init__(
        self,
        *,
        configuration: AgentConfiguration,
        factories: AgentAdapterFactories,
        storage: IStorageScope,
        audit: IAudit,
        timer_wheel: TimerWheel,
        reminder_concurrency: ISessionConcurrency,
        reminder_poke: Callable[[], None],
        endpoint: Callable[[], str],
        timeout_budget: TimeoutBudget,
        translator: Translator,
    ) -> None:
        self.configuration = configuration
        self.agent_id = configuration.id
        self.name = configuration.name
        self.storage = storage
        self.audit = audit
        self.timer_wheel = timer_wheel
        self.timeout_budget = timeout_budget
        self.translator = translator
        self._endpoint = endpoint
        self._wrapper_path: Path | None = None
        self.command_log: list[CommandRecord] = []
        self._session_capabilities: dict[str, _SessionCapabilityBinding] = {}
        self._concurrency = SessionLockRegistry()
        self._started = False
        self._stopping = False
        self._runtime_environment_include = configuration.runtime.env_include
        self._logger = logging.getLogger("bazaar_compute_node.application.agent")
        self._attachment_materializer = AttachmentMaterializer(
            self.workspace_path,
            self._referenced_attachment_paths,
        )
        provider_channel = factories.channel.build(
            ChannelContext(
                agent_id=self.agent_id,
                attachments=self._attachment_materializer,
                options=dict(configuration.channel.options),
                workspace=self.workspace_path,
            )
        )
        self.channel: IChannel = AgentScopedChannel(self.agent_id, provider_channel)
        runtime_options: dict[str, str] = {
            key: value
            for key, value in configuration.runtime.options.items()
            if isinstance(value, str)
        }
        if configuration.runtime.model is not None:
            runtime_options["model"] = configuration.runtime.model
        if configuration.runtime.effort is not None:
            runtime_options["effort"] = configuration.runtime.effort
        self._runtime_context = RuntimeCommandContext(
            run_command=self._run_runtime_command,
            environment_for_session=self._runtime_environment,
            agent_id=self.agent_id,
            agent_name=self.name,
            bot_name=self._bot_name,
            runtime_options=runtime_options,
            sandbox_mode=configuration.runtime.sandbox_mode,
            network_access=configuration.runtime.network_access,
            startup_timeout_seconds=timeout_budget.startup_seconds,
        )
        self.runtime: IRuntime = factories.runtime(self._runtime_context)
        idle_timeout_seconds = configuration.runtime.idle_timeout_seconds
        if (
            isinstance(idle_timeout_seconds, bool)
            or not isinstance(idle_timeout_seconds, int | float)
            or not math.isfinite(idle_timeout_seconds)
        ):
            raise ValueError("runtime idle timeout must be a finite number")
        if (
            idle_timeout_seconds > 0
            and idle_timeout_seconds * 1_000 > timer_wheel.maximum_delay_ms
        ):
            raise ValueError("runtime idle timeout exceeds the timer horizon")
        runtime_idle_timeout_ms = (
            math.ceil(idle_timeout_seconds * 1_000) if idle_timeout_seconds > 0 else 0
        )
        self.orchestrator = SessionOrchestrator(
            agent_id=self.agent_id,
            channel=self.channel,
            runtime=self.runtime,
            storage=self.storage,
            audit=self.audit,
            timeout_budget=self.timeout_budget,
            timer_wheel=self.timer_wheel,
            runtime_idle_timeout_ms=runtime_idle_timeout_ms,
            workspace=self.workspace_path,
            concurrency=self._concurrency,
        )
        self.reminder_service = ReminderCommandService(
            storage=self.storage,
            concurrency=reminder_concurrency,
            poke=reminder_poke,
        )
        self._provider_control_handler = (
            factories.control(self._adapter_context())
            if factories.control is not None
            else None
        )
        self.command_dispatcher = CommandDispatcher(
            self.orchestrator.command_service,
            reminder_service=self.reminder_service,
            timeout_budget=self.timeout_budget,
            control_handler=(
                self._handle_control
                if self._provider_control_handler is not None
                else None
            ),
            session_binding_validator=self._validate_session_binding,
        )

    @property
    def started(self) -> bool:
        return self._started

    def workspace_path(self) -> Path:
        return resolve_workspace_dir(self.agent_id)

    async def start(self) -> None:
        if self._started:
            return
        if self._stopping:
            raise RuntimeError("Agent application is stopping")
        workspace = self.workspace_path()
        await asyncio.to_thread(
            workspace.mkdir,
            parents=True,
            exist_ok=True,
            mode=0o700,
        )
        if os.name != "nt":
            await asyncio.to_thread(workspace.chmod, 0o700)
        try:
            self._wrapper_path = await asyncio.to_thread(
                install_bcc_wrapper,
                workspace / ".bcn" / "bin",
                agent_id=self.agent_id,
            )
            await self._attachment_materializer.reconcile()
            await self.orchestrator.start(
                timeout=self.timeout_budget.startup_seconds,
            )
        except BaseException:
            await self._cleanup_partial_start()
            raise
        self._started = True
        self.command_dispatcher.start_accepting()

    def _bot_name(self) -> str | None:
        identity = self.channel.get_identity()
        if identity is not None:
            if identity.name is not None:
                return identity.name
            if identity.id is not None:
                return identity.id
        return None

    async def stop(self) -> None:
        if self._stopping:
            return
        self._stopping = True
        self._started = False
        self.command_dispatcher.stop_accepting()
        errors: list[BaseException] = []
        try:
            await self.command_dispatcher.drain(
                timeout=self.timeout_budget.shutdown_seconds,
            )
        except BaseException as error:  # noqa: BLE001
            errors.append(error)
        try:
            await self.orchestrator.stop(
                timeout=self.timeout_budget.shutdown_seconds,
            )
        except BaseException as error:  # noqa: BLE001
            errors.append(error)
        try:
            await self._cleanup_bcc_wrapper()
        except BaseException as error:  # noqa: BLE001
            errors.append(error)
        self._session_capabilities.clear()
        if errors:
            raise RuntimeError(
                "; ".join(type(error).__name__ for error in errors)
            ) from errors[0]

    async def dispatch(
        self,
        request: Mapping[str, object],
    ) -> Mapping[str, object]:
        return await self.command_dispatcher(request)

    async def has_session(self, session_id: str) -> bool:
        if not isinstance(session_id, str) or not session_id:
            return False
        async with self.storage.transaction() as transaction:
            return await transaction.get_bcn_session(session_id) is not None

    async def publish_reminder_wake(self, session_id: str) -> None:
        if not self._started:
            raise RuntimeError("Agent application is not started")
        await self.orchestrator.publish_reminder_wake(session_id)

    def health_record(self) -> dict[str, object]:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "status": "started" if self.started else "stopped",
            "channel": self.channel.name,
            "runtime": self.runtime.name,
            "channel_health": dict(self.channel.health),
        }

    async def _cleanup_partial_start(self) -> None:
        try:
            await self.orchestrator.stop(
                timeout=self.timeout_budget.shutdown_seconds,
            )
        except BaseException as error:
            self._logger.debug("orchestrator cleanup failed", exc_info=error)
        try:
            await self._cleanup_bcc_wrapper()
        except BaseException as error:
            self._logger.debug("bcc wrapper cleanup failed", exc_info=error)
        self._session_capabilities.clear()

    async def _cleanup_bcc_wrapper(self) -> None:
        wrapper_path = self._wrapper_path
        if wrapper_path is None:
            return
        await asyncio.to_thread(remove_bcc_wrapper, wrapper_path)
        self._wrapper_path = None

    async def _referenced_attachment_paths(self) -> set[str]:
        async with self.storage.transaction() as transaction:
            return set(await transaction.list_ready_attachment_paths())

    def _adapter_context(self) -> Mapping[str, object]:
        return {
            "agent_id": self.agent_id,
            "agent_name": self.name,
            "channel": self.channel,
            "runtime": self.runtime,
            "storage": self.storage,
            "audit": self.audit,
            "command_log": self.command_log,
            "is_started": lambda: self._started,
        }

    async def _handle_control(
        self,
        request: Mapping[str, object],
    ) -> Mapping[str, object]:
        session_id = request.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise CommandDispatchError(
                "SESSION_REQUIRED",
                "session_id must be a non-empty string",
            )
        await self._validate_session_binding(session_id, request)
        handler = self._provider_control_handler
        if handler is None:
            raise CommandDispatchError(
                "INVALID_COMMAND",
                "control operation is not supported",
            )
        return await handler(request)

    async def _validate_session_binding(
        self,
        session_id: str,
        request: Mapping[str, object],
    ) -> None:
        request_agent_id = request.get("agent_id")
        if request_agent_id != self.agent_id:
            raise CommandDispatchError(
                "SESSION_BINDING_FAILED",
                "Agent binding is invalid",
            )
        runtime_session_id = request.get("runtime_session_id")
        session_capability = request.get("session_capability")
        async with self.storage.transaction() as transaction:
            if await transaction.get_bcn_session(session_id) is None:
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
        binding = self._session_capabilities.get(session_id)
        if (
            binding is None
            or binding.agent_id != self.agent_id
            or binding.bcn_session_id != session_id
            or binding.runtime_session_id != runtime_session.id
            or not isinstance(session_capability, str)
            or not hmac.compare_digest(
                session_capability.encode(), binding.capability.encode()
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
        binding = self._session_capabilities.get(session_id)
        if binding is None or binding.runtime_session_id != runtime_session_id:
            capability = secrets.token_urlsafe(32)
            token_values = [capability]
            for name in self._runtime_environment_include:
                if name != "TOKEN" and not name.endswith("_TOKEN"):
                    continue
                value = os.environ.get(name)
                if value and value not in token_values:
                    token_values.append(value)
            binding = _SessionCapabilityBinding(
                agent_id=self.agent_id,
                bcn_session_id=session_id,
                runtime_session_id=runtime_session_id,
                capability=capability,
                token_values=tuple(token_values),
            )
            self._session_capabilities[session_id] = binding
        allowed = set(_PLATFORM_ENVIRONMENT)
        for name in self.runtime.environment_variable_names():
            if not _ENVIRONMENT_NAME.fullmatch(name):
                raise ValueError(f"runtime environment name is invalid: {name}")
            allowed.add(name)
        for name in self._runtime_environment_include:
            if not _ENVIRONMENT_NAME.fullmatch(name):
                raise ValueError(f"runtime environment name is invalid: {name}")
            if name not in os.environ:
                raise ValueError(f"runtime environment variable is missing: {name}")
            allowed.add(name)
        environment = {
            name: os.environ[name] for name in sorted(allowed) if os.environ.get(name)
        }
        environment["PATH"] = os.pathsep.join(
            (str(wrapper_path.parent), environment.get("PATH", os.defpath))
        )
        environment.update(
            {
                "BCN_AGENT_ID": self.agent_id,
                "BCN_ENDPOINT": self._endpoint(),
                "BCN_SESSION_ID": session_id,
                "BCN_RUNTIME_SESSION_ID": runtime_session_id,
                "BCN_COMMAND_CAPABILITY": binding.capability,
            }
        )
        return environment

    def _redact_runtime_error(self, session_id: str, error_message: str) -> str:
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("session_id must be a non-empty string")
        if not isinstance(error_message, str):
            raise TypeError("error_message must be text")
        binding = self._session_capabilities.get(session_id)
        if binding is None:
            return error_message
        for token in binding.token_values:
            error_message = error_message.replace(token, "<redacted>")
        return error_message

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


__all__ = ["AgentApplication", "CommandRecord"]
