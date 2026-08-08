from __future__ import annotations

import asyncio
import hmac
import os
import secrets
import signal
from collections.abc import Mapping, Sequence
from pathlib import Path

from ..core.channel import IChannel
from ..core.lifecycle import TimeoutBudget
from ..core.models import RuntimeSession
from ..core.observability import IAudit
from ..core.orchestration import SessionOrchestrator
from ..core.paths import resolve_data_dir, resolve_workspace_dir
from ..core.runtime import IRuntime, RuntimeCommandContext
from ..core.storage import IStorage, NodeIdentity
from .command import (
    CommandDispatcher,
    CommandDispatchError,
)
from .registry import AdapterFactories
from .transport import LocalCommandServer
from .wrapper import install_bcc_wrapper, remove_bcc_wrapper

CommandRecord = tuple[str, tuple[str, ...]]


class NodeApplication:
    """Generic application lifecycle for one dynamically composed node."""

    def __init__(
        self,
        *,
        factories: AdapterFactories,
        channel_slug: str,
        runtime_slug: str,
        endpoint_path: Path | None = None,
        node_id: str = "bcn-node",
        workspace_id: str | None = None,
        runtime_options: Mapping[str, str] | None = None,
        storage_slug: str,
        audit_slug: str,
        timeout_budget: TimeoutBudget | None = None,
    ) -> None:
        self.data_dir = resolve_data_dir()
        self.channel_slug = channel_slug
        self.runtime_slug = runtime_slug
        self.runtime_options = dict(runtime_options or {})
        self.storage_slug = storage_slug
        self.audit_slug = audit_slug
        self.timeout_budget = timeout_budget or TimeoutBudget(
            startup_seconds=5,
            provider_call_seconds=5,
            command_seconds=5,
            shutdown_seconds=5,
        )
        self.channel: IChannel = factories.channel()
        self.storage: IStorage = factories.storage()
        self.audit: IAudit = factories.audit()
        self.command_log: list[CommandRecord] = []
        self._wrapper_path: Path | None = None
        self._identity: NodeIdentity | None = None
        self._session_capabilities: dict[str, str] = {}
        self._runtime_session_ids: dict[str, str] = {}
        self._started = False
        self._stopped = asyncio.Event()
        self._runtime_context = RuntimeCommandContext(
            run_command=self._run_runtime_command,
            environment_for_session=self._runtime_environment,
            node_id=node_id,
            runtime_options=self.runtime_options,
        )
        self.runtime: IRuntime = factories.runtime(self._runtime_context)
        self.orchestrator = SessionOrchestrator(
            node_id=node_id,
            workspace_id=workspace_id,
            channel=self.channel,
            runtime=self.runtime,
            storage=self.storage,
            audit=self.audit,
            timeout_budget=self.timeout_budget,
            runtime_slug=runtime_slug,
            on_node_initialized=self._ensure_workspace,
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
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            self.data_dir.chmod(0o700)
        self._wrapper_path = install_bcc_wrapper(self.data_dir / "bin")
        try:
            await self.command_server.start()
            await self.orchestrator.start(
                timeout=self.timeout_budget.startup_seconds,
            )
        except BaseException:
            await self.stop()
            raise
        self._started = True
        self._stopped.clear()
        self.command_dispatcher.start_accepting()

    async def stop(self) -> None:
        if not self._started:
            try:
                await self.command_dispatcher.drain(
                    timeout=self.timeout_budget.shutdown_seconds,
                )
            finally:
                try:
                    await self.command_server.stop()
                finally:
                    self._cleanup_bcc_wrapper()
            return
        self._started = False
        self.command_dispatcher.stop_accepting()
        try:
            await self.command_dispatcher.drain(
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
                        self._cleanup_bcc_wrapper()
                    finally:
                        self._stopped.set()

    def _cleanup_bcc_wrapper(self) -> None:
        wrapper_path = self._wrapper_path
        if wrapper_path is None:
            return
        remove_bcc_wrapper(wrapper_path)
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
                "channel": self.channel_slug,
                "runtime": self.runtime_slug,
                "storage": self.storage_slug,
                "audit": self.audit_slug,
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
        bcn_session_id: str,
        request: Mapping[str, object],
    ) -> None:
        runtime_session_id = request.get("runtime_session_id")
        session_capability = request.get("session_capability")
        async with self.storage.transaction() as transaction:
            bcn_session = await transaction.get_bcn_session(bcn_session_id)
            if bcn_session is None:
                raise CommandDispatchError(
                    "SESSION_NOT_FOUND",
                    f"unknown bcn session: {bcn_session_id}",
                )
            runtime_session = await transaction.find_runtime_session(bcn_session_id)
        if runtime_session is None or runtime_session_id != (
            runtime_session.agent_runtime_session_id
        ):
            raise CommandDispatchError(
                "SESSION_BINDING_FAILED",
                "runtime session binding is invalid",
            )
        expected_capability = self._session_capabilities.get(bcn_session_id)
        if (
            expected_capability is None
            or not isinstance(session_capability, str)
            or not hmac.compare_digest(
                session_capability.encode(), expected_capability.encode()
            )
        ):
            raise CommandDispatchError(
                "SESSION_BINDING_FAILED",
                "session capability is invalid",
            )

    def _runtime_environment(self, session: RuntimeSession) -> Mapping[str, str]:
        self._runtime_session_ids[session.bcn_session_id] = (
            session.agent_runtime_session_id
        )
        return self._build_command_environment(
            session.bcn_session_id,
            session.agent_runtime_session_id,
        )

    def _build_command_environment(
        self,
        bcn_session_id: str,
        runtime_session_id: str,
    ) -> dict[str, str]:
        if not bcn_session_id:
            raise ValueError("bcn_session_id must be a non-empty string")
        if not runtime_session_id:
            raise ValueError("runtime_session_id must be a non-empty string")
        wrapper_path = self._wrapper_path
        if wrapper_path is None:
            raise RuntimeError("bcc wrapper is not installed")
        session_capability = self._session_capabilities.setdefault(
            bcn_session_id,
            secrets.token_urlsafe(32),
        )
        wrapper_directory = str(wrapper_path.parent)
        existing_path = os.environ.get("PATH") or os.defpath
        environment = {
            "PATH": os.pathsep.join((wrapper_directory, existing_path)),
            "BCN_ENDPOINT": self.endpoint,
            "BCN_SESSION_ID": bcn_session_id,
            "BCN_RUNTIME_SESSION_ID": runtime_session_id,
            "BCN_COMMAND_CAPABILITY": session_capability,
        }
        for name in ("PYTHONPATH", "SystemRoot"):
            value = os.environ.get(name)
            if value:
                environment[name] = value
        return environment

    async def _run_runtime_command(
        self,
        bcn_session_id: str,
        arguments: Sequence[str],
        body: str | None,
    ) -> None:
        if not bcn_session_id:
            raise ValueError("bcn_session_id must be a non-empty string")
        if not arguments or any(
            not isinstance(argument, str) for argument in arguments
        ):
            raise ValueError("runtime command arguments must be non-empty text")
        wrapper_path = self._wrapper_path
        if wrapper_path is None:
            raise RuntimeError("bcc wrapper is not installed")
        self.command_log.append((bcn_session_id, tuple(arguments)))
        runtime_session_id = self._runtime_session_ids.get(
            bcn_session_id,
            f"runtime-{bcn_session_id}",
        )
        environment = self._build_command_environment(
            bcn_session_id,
            runtime_session_id,
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
