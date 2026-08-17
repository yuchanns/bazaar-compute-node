from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ..core.lifecycle import TimeoutBudget
from ..core.observability import IAudit
from ..core.paths import resolve_data_dir
from ..core.storage import IStorage
from ..core.timerwheel import TimerWheel
from .agent import AgentApplication
from .config import AgentConfiguration, NodeConfiguration
from .registry import AdapterRegistry, SharedAdapterFactories
from .transport import LocalCommandServer
from .wrapper import install_bcc_wrapper, remove_bcc_wrapper


@dataclass(frozen=True, slots=True)
class AgentStartupResult:
    agent_id: str
    name: str
    channel: str
    runtime: str
    status: str
    error_type: str | None = None
    error: str | None = None

    def as_health_record(self) -> dict[str, object]:
        record: dict[str, object] = {
            "agent_id": self.agent_id,
            "name": self.name,
            "status": self.status,
            "channel": self.channel,
            "runtime": self.runtime,
        }
        if self.error_type is not None:
            record["error_type"] = self.error_type
        if self.error is not None:
            record["error"] = self.error
        return record


class NodeApplication:
    """Single-process daemon composition root for shared facilities and Agents."""

    def __init__(
        self,
        *,
        configuration: NodeConfiguration,
        shared_factories: SharedAdapterFactories,
        registry: AdapterRegistry | None = None,
        endpoint_path: Path | None = None,
        timeout_budget: TimeoutBudget | None = None,
    ) -> None:
        self.configuration = configuration
        self.data_dir = resolve_data_dir()
        self.timeout_budget = timeout_budget or TimeoutBudget(
            startup_seconds=60,
            provider_call_seconds=600,
            command_seconds=10,
            shutdown_seconds=5,
        )
        self.storage: IStorage = shared_factories.storage()
        self.audit: IAudit = shared_factories.audit()
        self.timer_wheel = TimerWheel()
        self.command_server = LocalCommandServer(
            self._dispatch,
            endpoint_path=endpoint_path,
        )
        self.agents: dict[str, AgentApplication] = {}
        self.agent_startup_results: dict[str, AgentStartupResult] = {}
        self._registry = registry or AdapterRegistry()
        self._wrapper_path: Path | None = None
        self._started = False
        self._ready = False
        self._accepting = False
        self._stopped = asyncio.Event()
        self._logger = logging.getLogger("bazaar_compute_node.application")

    @property
    def endpoint(self) -> str:
        return self.command_server.endpoint

    @property
    def ready(self) -> bool:
        return self._ready

    async def start(self) -> None:
        if self._started:
            return
        await asyncio.to_thread(self.data_dir.mkdir, parents=True, exist_ok=True)
        if os.name != "nt":
            await asyncio.to_thread(self.data_dir.chmod, 0o700)
        self._wrapper_path = await asyncio.to_thread(
            install_bcc_wrapper,
            self.data_dir / "bin",
        )
        try:
            await self.storage.start(timeout=self.timeout_budget.startup_seconds)
            await self.timer_wheel.start()
            await self.command_server.start()
        except BaseException:
            await self._stop_shared_after_failed_start()
            raise

        self._started = True
        self._stopped.clear()
        for agent_configuration in self.configuration.agents:
            await self._start_agent(agent_configuration)
        self._ready = True
        self._accepting = True
        started_count = len(self.agents)
        failed_count = len(self.configuration.agents) - started_count
        self._log(
            "bcn.ready",
            configured=len(self.configuration.agents),
            started=started_count,
            failed=failed_count,
            endpoint=self.endpoint,
        )

    async def _start_agent(self, configuration: AgentConfiguration) -> None:
        application: AgentApplication | None = None
        try:
            factories = await asyncio.to_thread(
                self._registry.load_agent,
                channel=configuration.channel.kind,
                runtime=configuration.runtime.kind,
                storage=self.configuration.storage,
            )
            storage_scope = self.storage.scope(configuration.id, configuration.name)
            wrapper_path = self._wrapper_path
            if wrapper_path is None:
                raise RuntimeError("bcc wrapper is not installed")
            application = AgentApplication(
                configuration=configuration,
                factories=factories,
                storage=storage_scope,
                audit=self.audit,
                timer_wheel=self.timer_wheel,
                endpoint=lambda: self.endpoint,
                wrapper_path=wrapper_path,
                timeout_budget=self.timeout_budget,
            )
            await application.start()
        except asyncio.CancelledError:
            if application is not None:
                await self._stop_agent_after_failed_start(application)
            raise
        except Exception as error:  # noqa: BLE001
            if application is not None:
                await self._stop_agent_after_failed_start(application)
            result = AgentStartupResult(
                agent_id=configuration.id,
                name=configuration.name,
                channel=configuration.channel.kind,
                runtime=configuration.runtime.kind,
                status="failed",
                error_type=type(error).__name__,
                error=_safe_error(error),
            )
            self.agent_startup_results[configuration.id] = result
            self._log("agent.start.failed", **result.as_health_record())
            return

        self.agents[configuration.id] = application
        result = AgentStartupResult(
            agent_id=configuration.id,
            name=configuration.name,
            channel=configuration.channel.kind,
            runtime=configuration.runtime.kind,
            status="started",
        )
        self.agent_startup_results[configuration.id] = result
        self._log("agent.start.succeeded", **result.as_health_record())

    async def stop(self) -> None:
        if not self._started:
            await self._stop_shared_after_failed_start()
            return
        self._ready = False
        self._accepting = False
        errors: list[str] = []
        for agent_id, agent in tuple(self.agents.items()):
            try:
                await agent.stop()
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001
                errors.append(f"agent[{agent_id}].stop:{type(error).__name__}")
                self._log(
                    "agent.stop.failed",
                    agent_id=agent_id,
                    error_type=type(error).__name__,
                    error=_safe_error(error),
                )
        self.agents.clear()
        try:
            await self.command_server.stop()
        except Exception as error:  # noqa: BLE001
            errors.append(f"command_server.stop:{type(error).__name__}")
        try:
            await self.timer_wheel.close()
        except Exception as error:  # noqa: BLE001
            errors.append(f"timer_wheel.close:{type(error).__name__}")
        try:
            await self.storage.stop(timeout=self.timeout_budget.shutdown_seconds)
        except Exception as error:  # noqa: BLE001
            errors.append(f"storage.stop:{type(error).__name__}")
        try:
            await self._cleanup_bcc_wrapper()
        except Exception as error:  # noqa: BLE001
            errors.append(f"bcc.cleanup:{type(error).__name__}")
        self._started = False
        self._stopped.set()
        if errors:
            self._log("bcn.stop.errors", errors=errors)

    async def wait(self) -> None:
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(signum, self._stopped.set)
            except NotImplementedError, RuntimeError:
                pass
        await self._stopped.wait()

    async def _dispatch(
        self,
        request: Mapping[str, object],
    ) -> Mapping[str, object]:
        kind = request.get("kind")
        if kind == "control":
            operation = request.get("operation")
            if operation == "health":
                return {"ok": True, "result": self._health()}
            if operation == "shutdown":
                self._stopped.set()
                return {
                    "ok": True,
                    "result": {"accepted": True, "operation": "shutdown"},
                }
            return {
                "ok": False,
                "code": "INVALID_COMMAND",
                "error": "control operation is not supported",
            }
        if kind != "command":
            return {
                "ok": False,
                "code": "INVALID_COMMAND",
                "error": "request kind is not supported",
            }
        if not self._accepting:
            return {
                "ok": False,
                "code": "SERVICE_NOT_READY",
                "error": "command service is not accepting requests",
            }
        session_id = request.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            return {
                "ok": False,
                "code": "SESSION_REQUIRED",
                "error": "session_id must be a non-empty string",
            }
        owner: AgentApplication | None = None
        for agent in self.agents.values():
            if await agent.has_session(session_id):
                if owner is not None:
                    return {
                        "ok": False,
                        "code": "SESSION_BINDING_FAILED",
                        "error": "session is bound to multiple Agents",
                    }
                owner = agent
        if owner is None:
            return {
                "ok": False,
                "code": "SESSION_NOT_FOUND",
                "error": f"unknown bcn session: {session_id}",
            }
        return await owner.dispatch(request)

    def _health(self) -> dict[str, object]:
        records: list[dict[str, object]] = []
        for configuration in self.configuration.agents:
            agent = self.agents.get(configuration.id)
            if agent is not None:
                records.append(agent.health_record())
                continue
            result = self.agent_startup_results.get(configuration.id)
            if result is not None:
                records.append(result.as_health_record())
                continue
            records.append(
                {
                    "agent_id": configuration.id,
                    "name": configuration.name,
                    "status": "pending",
                    "channel": configuration.channel.kind,
                    "runtime": configuration.runtime.kind,
                }
            )
        return {
            "started": self._started,
            "ready": self._ready,
            "accepting": self._accepting,
            "storage": self.storage.name,
            "audit": self.audit.name,
            "configured": len(self.configuration.agents),
            "started_agents": len(self.agents),
            "failed_agents": sum(
                1
                for result in self.agent_startup_results.values()
                if result.status == "failed"
            ),
            "agents": records,
        }

    async def _stop_agent_after_failed_start(self, agent: AgentApplication) -> None:
        try:
            await agent.stop()
        except BaseException as error:
            self._logger.debug(
                "agent cleanup after failed start failed",
                extra={"agent_id": agent.agent_id},
                exc_info=error,
            )

    async def _stop_shared_after_failed_start(self) -> None:
        try:
            await self.command_server.stop()
        except BaseException as error:
            self._logger.debug("command server cleanup failed", exc_info=error)
        try:
            await self.timer_wheel.close()
        except BaseException as error:
            self._logger.debug("timer wheel cleanup failed", exc_info=error)
        try:
            await self.storage.stop(timeout=self.timeout_budget.shutdown_seconds)
        except BaseException as error:
            self._logger.debug("storage cleanup failed", exc_info=error)
        try:
            await self._cleanup_bcc_wrapper()
        except BaseException as error:
            self._logger.debug("bcc wrapper cleanup failed", exc_info=error)

    async def _cleanup_bcc_wrapper(self) -> None:
        wrapper_path = self._wrapper_path
        if wrapper_path is None:
            return
        await asyncio.to_thread(remove_bcc_wrapper, wrapper_path)
        self._wrapper_path = None

    def _log(self, event_name: str, **metadata: object) -> None:
        self._logger.info(
            "%s",
            json.dumps(
                {"event_name": event_name, "metadata": metadata},
                separators=(",", ":"),
                sort_keys=True,
                default=str,
            ),
        )


def _safe_error(error: BaseException) -> str:
    message = str(error).strip()
    return message or type(error).__name__


__all__ = ["AgentStartupResult", "NodeApplication"]
