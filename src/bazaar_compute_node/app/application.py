from __future__ import annotations

import asyncio
import os
import signal
from collections.abc import Mapping, Sequence
from pathlib import Path

from ..core.channel import IChannel
from ..core.lifecycle import TimeoutBudget
from ..core.observability import IAudit
from ..core.orchestration import SessionOrchestrator
from ..core.runtime import IRuntime, RuntimeCommandContext
from ..core.storage import IStorage, NodeIdentity
from .command import CommandDispatcher, SessionCommandService
from .daemon import (
    new_runtime_metadata,
    remove_runtime_metadata,
    write_runtime_metadata,
)
from .registry import AdapterFactories
from .transport import LocalCommandServer
from .wrapper import install_bcc_wrapper

CommandRecord = tuple[str, tuple[str, ...]]


class NodeApplication:
    """Generic application lifecycle for one dynamically composed node."""

    def __init__(
        self,
        *,
        factories: AdapterFactories,
        channel_slug: str,
        runtime_slug: str,
        data_dir: Path,
        endpoint_path: Path | None = None,
        node_id: str = "bcn-node",
        workspace_id: str | None = None,
        storage_slug: str = "dummy",
        audit_slug: str = "dummy",
        runtime_metadata_path: Path | None = None,
        timeout_budget: TimeoutBudget | None = None,
    ) -> None:
        self.data_dir = data_dir.expanduser()
        self.channel_slug = channel_slug
        self.runtime_slug = runtime_slug
        self.storage_slug = storage_slug
        self.audit_slug = audit_slug
        self.runtime_metadata_path = (
            runtime_metadata_path or self.data_dir / "runtime.json"
        ).expanduser()
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
        self._started = False
        self._stopped = asyncio.Event()
        self._runtime_context = RuntimeCommandContext(
            run_command=self._run_runtime_command,
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
        self.command_service = SessionCommandService(
            self.orchestrator,
            self.timeout_budget,
        )
        control_handler = None
        if factories.control is not None:
            control_handler = factories.control(self._adapter_context())
        self.command_server = LocalCommandServer(
            CommandDispatcher(
                self.command_service,
                timeout_budget=self.timeout_budget,
                control_handler=self._handle_control,
            ),
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
        self._wrapper_path = install_bcc_wrapper(self.data_dir / "bin")
        await self.command_server.start()
        try:
            await self.orchestrator.start(
                timeout=self.timeout_budget.startup_seconds,
            )
        except BaseException:
            await self.command_server.stop()
            raise
        self._started = True
        self._stopped.clear()
        try:
            write_runtime_metadata(
                self.runtime_metadata_path,
                new_runtime_metadata(
                    endpoint=self.endpoint,
                    channel_slug=self.channel_slug,
                    runtime_slug=self.runtime_slug,
                    storage_slug=self.storage_slug,
                    audit_slug=self.audit_slug,
                ),
            )
        except BaseException:
            await self.stop()
            raise

    async def stop(self) -> None:
        if not self._started:
            await self.command_server.stop()
            return
        try:
            await self.orchestrator.stop(
                timeout=self.timeout_budget.shutdown_seconds,
            )
        finally:
            await self.command_server.stop()
            self._started = False
            self._stopped.set()
            remove_runtime_metadata(self.runtime_metadata_path, pid=os.getpid())

    async def wait(self) -> None:
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(signum, self._stopped.set)
            except NotImplementedError, RuntimeError:
                pass
        await self._stopped.wait()

    async def _ensure_workspace(self, identity: NodeIdentity) -> None:
        workspace_dir = self.data_dir / "workspaces" / identity.workspace_id
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
        if request.get("operation") == "shutdown":
            self._stopped.set()
            return {"accepted": True, "operation": "shutdown"}
        if self._provider_control_handler is None:
            raise ValueError("control operation is not supported")
        return await self._provider_control_handler(request)

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
        environment = os.environ.copy()
        environment["BCN_ENDPOINT"] = self.endpoint
        environment["BCN_SESSION_ID"] = bcn_session_id
        wrapper_directory = str(wrapper_path.parent)
        existing_path = environment.get("PATH")
        environment["PATH"] = (
            wrapper_directory
            if not existing_path
            else os.pathsep.join((wrapper_directory, existing_path))
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
