from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from .. import __version__
from ..core.audit import AuditRecorder
from ..core.concurrency import ThreadLockRegistry
from ..core.control import AgentCommands, ControlContext, IControl
from ..core.correlation import CorrelationContext
from ..core.lifecycle import ITaskFailureSource, TimeoutBudget
from ..core.models import InboundAttachment, Message, RuntimeEventState
from ..core.observability import AuditContext, IAudit
from ..core.orchestration import ReminderScheduler
from ..core.paths import resolve_data_dir
from ..core.restart import RESTART_EXIT_CODE
from ..core.storage import IStorage
from ..core.timerwheel import TimerWheel
from ..core.utils.clock import now_ms
from ..core.utils.text import format_exception
from ..i18n import create_translator
from .agent import AgentApplication
from .config import (
    AgentConfiguration,
    ChannelConfiguration,
    NodeConfiguration,
    load_node_configuration,
    write_configuration,
)
from .health_report import HealthReporter
from .registry import AdapterRegistry, SharedAdapterFactories
from .server_management import check_env_value, write_env_value
from .transport import LocalCommandServer
from .upgrade import UpgradeService
from .version_check import VersionWatcher


@dataclass(frozen=True, slots=True)
class AgentStartupResult:
    agent_id: str
    name: str
    channels: tuple[str, ...]
    runtimes: tuple[str, ...]
    status: str
    error_type: str | None = None
    error: str | None = None

    def as_health_record(self) -> dict[str, object]:
        record: dict[str, object] = {
            "agent_id": self.agent_id,
            "name": self.name,
            "status": self.status,
            "channels": self.channels,
            "runtimes": self.runtimes,
        }
        if self.error_type is not None:
            record["error_type"] = self.error_type
        if self.error is not None:
            record["error"] = self.error
        return record


def _channel_kinds(configuration: AgentConfiguration) -> tuple[str, ...]:
    return tuple(channel.kind for channel in configuration.channels)


def _runtime_kinds(configuration: AgentConfiguration) -> tuple[str, ...]:
    return tuple(runtime.kind for runtime in configuration.runtimes)


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
        # where the configuration and the credentials live on disk; a change
        # to an agent made while the node runs is written there, and a node
        # given neither keeps its changes to the process
        config_path: Path | None = None,
        env_path: Path | None = None,
    ) -> None:
        self.configuration = configuration
        self._config_path = config_path
        self._env_path = env_path
        self.translator = create_translator(configuration.lang)
        self.data_dir = resolve_data_dir()
        self.timeout_budget = timeout_budget or TimeoutBudget(
            startup_seconds=60,
            provider_call_seconds=600,
            command_seconds=10,
            shutdown_seconds=5,
        )
        self.storage: IStorage = shared_factories.storage()
        self.timer_wheel = TimerWheel()
        self.audit: IAudit = shared_factories.audit(
            AuditContext(
                options=configuration.audit_options,
                timer_wheel=self.timer_wheel,
                timeout_budget=self.timeout_budget,
            )
        )
        # where requests come from besides the socket, if anywhere
        self.control: IControl | None = None
        if shared_factories.control is not None:
            self.control = shared_factories.control(
                ControlContext(
                    options=configuration.control_options,
                    timer_wheel=self.timer_wheel,
                    timeout_budget=self.timeout_budget,
                    audit=self.audit,
                    commands_of=self._agent_commands,
                )
            )
        self._reminder_concurrency = ThreadLockRegistry()
        self._audit_recorder = AuditRecorder(
            sink=self.audit,
            timeout_budget=self.timeout_budget,
            clock=now_ms,
        )
        self.reminder_scheduler = ReminderScheduler(
            storage=self.storage,
            timer_wheel=self.timer_wheel,
            concurrency=self._reminder_concurrency,
            publish_wake=self._publish_inbox_wake,
            audit=self._audit_recorder,
        )
        self.version_watcher = VersionWatcher(
            timer_wheel=self.timer_wheel,
            current_version=__version__,
            request_timeout_seconds=self.timeout_budget.command_seconds,
        )
        self.health_reporter = HealthReporter(
            timer_wheel=self.timer_wheel,
            audit=self._audit_recorder,
            health=self._health,
            version=__version__,
            interval_seconds=self.timeout_budget.startup_seconds,
        )
        self._restart_requested = False
        # Windows has nothing that brings the node back after it exits, so
        # there it offers no upgrade command at all
        self.upgrade_service = (
            None
            if os.name == "nt"
            else UpgradeService(
                available_version=self.version_watcher.available_version,
                installed_version=__version__,
                request_restart=self.request_restart,
            )
        )
        self.command_server = LocalCommandServer(
            self._dispatch,
            endpoint_path=endpoint_path,
        )
        self.agents: dict[str, AgentApplication] = {}
        self.agent_startup_results: dict[str, AgentStartupResult] = {}
        self._registry = registry or AdapterRegistry()
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
        try:
            await self.storage.start(timeout=self.timeout_budget.startup_seconds)
            await self.audit.start(timeout=self.timeout_budget.startup_seconds)
            await self.timer_wheel.start()
            await self.command_server.start()
        except BaseException:
            await self._stop_shared_after_failed_start()
            raise

        self._started = True
        self._stopped.clear()
        async with asyncio.TaskGroup() as group:
            for agent_configuration in self.configuration.agents:
                group.create_task(
                    self._start_agent(agent_configuration),
                    name=f"bcn-agent-start-{agent_configuration.id}",
                )
        try:
            await self.reminder_scheduler.start(
                timeout=self.timeout_budget.startup_seconds,
            )
            if self.configuration.version_check:
                await self.version_watcher.start(
                    timeout=self.timeout_budget.startup_seconds,
                )
            self._ready = True
            self._accepting = True
            # the first beat goes out once the node is ready, so it describes
            # the state a consumer can act on rather than a node still coming up
            await self.health_reporter.start(
                timeout=self.timeout_budget.startup_seconds
            )
            # requests are taken once the node can answer them
            if self.control is not None:
                await self.control.start(timeout=self.timeout_budget.startup_seconds)
        except BaseException:
            await self.stop()
            raise
        started_count = len(self.agents)
        failed_count = len(self.configuration.agents) - started_count
        self._log(
            "bcn.ready",
            configured=len(self.configuration.agents),
            started=started_count,
            failed=failed_count,
            endpoint=self.endpoint,
        )

    def _upgrade_notice(self) -> tuple[str, str] | None:
        available = self.version_watcher.available_version()
        return None if available is None else (available, __version__)

    async def _start_agent(self, configuration: AgentConfiguration) -> None:
        application: AgentApplication | None = None
        try:
            async with asyncio.timeout(self.timeout_budget.startup_seconds):
                factories = await asyncio.to_thread(
                    self._registry.load_agent,
                    channels=_channel_kinds(configuration),
                    runtimes=_runtime_kinds(configuration),
                )
                storage_scope = self.storage.scope(configuration.id, configuration.name)
                application = AgentApplication(
                    configuration=configuration,
                    factories=factories,
                    storage=storage_scope,
                    audit=self._audit_recorder,
                    timer_wheel=self.timer_wheel,
                    reminder_concurrency=self._reminder_concurrency,
                    reminder_poke=self.reminder_scheduler.poke,
                    endpoint=lambda: self.endpoint,
                    timeout_budget=self.timeout_budget,
                    translator=self.translator,
                    upgrade_notice=self._upgrade_notice,
                    upgrade_service=self.upgrade_service,
                    # a control is someone to ask about a conversation
                    reviewed=self.control is not None,
                )
                # on the list from the moment it exists: its state says
                # whether it is up, and a change to it in the meantime is
                # refused on that state
                self.agents[configuration.id] = application
                await application.start()
        except asyncio.CancelledError:
            if application is not None:
                self.agents.pop(configuration.id, None)
                await self._stop_agent_after_failed_start(application)
            raise
        except Exception as error:  # noqa: BLE001
            if application is not None:
                self.agents.pop(configuration.id, None)
                await self._stop_agent_after_failed_start(application)
            result = AgentStartupResult(
                agent_id=configuration.id,
                name=configuration.name,
                channels=_channel_kinds(configuration),
                runtimes=_runtime_kinds(configuration),
                status="failed",
                error_type=type(error).__name__,
                error=format_exception(error),
            )
            self.agent_startup_results[configuration.id] = result
            self._log("agent.start.failed", **result.as_health_record())
            return

        result = AgentStartupResult(
            agent_id=configuration.id,
            name=configuration.name,
            channels=_channel_kinds(configuration),
            runtimes=_runtime_kinds(configuration),
            status="started",
        )
        self.agent_startup_results[configuration.id] = result
        self._log("agent.start.succeeded", **result.as_health_record())

    async def add_agent(
        self,
        configuration: AgentConfiguration,
        secrets: Mapping[int, Mapping[str, str]] | None = None,
    ) -> AgentConfiguration:
        """Take a new agent in while running: its credentials kept, its
        configuration written down, and itself started. One that fails to
        start stays on the list with the failure in the health record, the
        same as at startup, for the operator to fix and try again."""

        if any(agent.id == configuration.id for agent in self.configuration.agents):
            raise ValueError(f"agent already configured: {configuration.id}")
        self._check_agents((*self.configuration.agents, configuration))
        configuration = self._keep_secrets(configuration, secrets or {})
        self._write_agents((*self.configuration.agents, configuration))
        await self._start_agent(configuration)
        await self._agents_changed("agent.added", configuration)
        return configuration

    async def update_agent(
        self,
        configuration: AgentConfiguration,
        secrets: Mapping[int, Mapping[str, str]] | None = None,
    ) -> AgentConfiguration:
        """Change an agent while running: the one running under that id is
        stopped, the new configuration written down, and an agent started
        from it. In between, the agent hears nothing; what its channels
        hold for it is picked up when they poll again."""

        if not any(agent.id == configuration.id for agent in self.configuration.agents):
            raise ValueError(f"agent is not configured: {configuration.id}")
        self._settled(configuration.id)
        self._check_agents(
            tuple(
                configuration if agent.id == configuration.id else agent
                for agent in self.configuration.agents
            )
        )
        configuration = self._keep_secrets(configuration, secrets or {})
        self._write_agents(
            tuple(
                configuration if agent.id == configuration.id else agent
                for agent in self.configuration.agents
            )
        )
        await self._stop_agent(configuration.id)
        await self._start_agent(configuration)
        await self._agents_changed("agent.updated", configuration)
        return configuration

    async def remove_agent(self, agent_id: str) -> AgentConfiguration:
        """Stop an agent and strike it from the configuration. Its workspace
        and its records stay, and so do the variables its credentials went
        under: another agent may share a token, and clearing is the
        operator's call."""

        removed = next(
            (agent for agent in self.configuration.agents if agent.id == agent_id),
            None,
        )
        if removed is None:
            raise ValueError(f"agent is not configured: {agent_id}")
        self._settled(agent_id)
        self._write_agents(
            tuple(agent for agent in self.configuration.agents if agent.id != agent_id)
        )
        await self._stop_agent(agent_id)
        await self._agents_changed("agent.removed", removed)
        return removed

    def _settled(self, agent_id: str) -> None:
        """Refuse to change an agent that is not up: one still coming up,
        or one already done for and on its way out. The turn to terminated
        is taken before the stopping is awaited, so it is what stands
        between two changes to one agent. One with no instance - it never
        started, or failed to - is settled too."""

        agent = self.agents.get(agent_id)
        if agent is not None and not agent.started:
            raise ValueError(f"agent is {agent.state.value}: {agent_id}")

    def _keep_secrets(
        self,
        configuration: AgentConfiguration,
        secrets: Mapping[int, Mapping[str, str]],
    ) -> AgentConfiguration:
        """Put each credential under a variable of the node's naming, in the
        environment file for the next start and in the process for this one;
        the channel's configuration names the variable, never the value. A
        value the file cannot hold is refused before any value is kept."""

        # the whole id: two agents made within the same minute share the start
        # of theirs; and the card's place, for two cards of one kind
        prefix = f"BCN_{configuration.id.replace('-', '_').upper()}"
        kept: dict[str, str] = {}
        channels = list(configuration.channels)
        for index, values in secrets.items():
            channel = channels[index]
            options = dict(channel.options)
            for key, value in values.items():
                name = f"{prefix}_CHANNEL{index}_{channel.kind.upper()}_{key.upper()}"
                kept[name] = value
                options[f"{key}_env"] = name
            channels[index] = ChannelConfiguration(kind=channel.kind, options=options)
        for value in kept.values():
            check_env_value(value)
        for name, value in kept.items():
            if self._env_path is not None:
                write_env_value(self._env_path, name, value)
            os.environ[name] = value
        return replace(configuration, channels=tuple(channels))

    def _write_agents(self, agents: tuple[AgentConfiguration, ...]) -> None:
        """The agents as they now are, in memory and on disk - in one go,
        with nothing awaited in between, so two changes cannot cross and
        the file always holds what the process holds. The file is read back
        before it is written so that only the agents change: what the
        process runs with may carry command-line overrides the file never
        held. The files are small; the write is over in the time a message
        takes to arrive. The file goes first: a write that fails leaves the
        process holding what it held."""

        path = self._config_path
        if path is not None:
            write_configuration(
                path, replace(load_node_configuration(path), agents=agents)
            )
        self.configuration = replace(self.configuration, agents=agents)

    def _check_agents(self, agents: tuple[AgentConfiguration, ...]) -> None:
        """The node's configuration with these agents, checked as a whole -
        two agents may not share a name - before anything is kept for them."""

        replace(self.configuration, agents=agents)

    async def _stop_agent(self, agent_id: str) -> None:
        """Stop an agent and take it off the list; the state turns to
        stopping before the stop is awaited, and the list entry stays
        until it is over so that the turn is seen. A stop that reports
        something going wrong on its way down has still stopped: that is
        recorded, and what comes after goes on."""

        agent = self.agents.get(agent_id)
        self.agent_startup_results.pop(agent_id, None)
        if agent is not None:
            try:
                await agent.stop()
            except Exception as error:  # noqa: BLE001
                self._log(
                    "agent.stop.failed",
                    agent_id=agent_id,
                    error_type=type(error).__name__,
                    error=format_exception(error),
                )
            finally:
                self.agents.pop(agent_id, None)

    async def _agents_changed(self, event_name: str, agent: AgentConfiguration) -> None:
        """Say what changed, and beat once so a consumer sees the new list
        now rather than at the next interval."""

        result = self.agent_startup_results.get(agent.id)
        await self._audit_recorder.append(
            event_name=event_name,
            state=RuntimeEventState.COMPLETED,
            correlation=CorrelationContext(node_id=agent.id),
            metadata={
                "agent_id": agent.id,
                "name": agent.name,
                "channels": _channel_kinds(agent),
                "runtimes": _runtime_kinds(agent),
                **(
                    {"error": result.error, "error_type": result.error_type}
                    if result is not None and result.status == "failed"
                    else {}
                ),
            },
        )
        if self._ready:
            await self.health_reporter.report()

    async def stop(self) -> None:
        if not self._started:
            await self._stop_shared_after_failed_start()
            return
        self._ready = False
        self._accepting = False
        errors: list[str] = []
        # requests stop being taken before anything they need goes
        if self.control is not None:
            try:
                await self.control.stop(timeout=self.timeout_budget.shutdown_seconds)
            except Exception as error:  # noqa: BLE001
                errors.append(f"control.stop:{type(error).__name__}")
        try:
            await self.health_reporter.stop(
                timeout=self.timeout_budget.shutdown_seconds,
            )
        except Exception as error:  # noqa: BLE001
            errors.append(f"health_reporter.stop:{type(error).__name__}")
        try:
            await self.version_watcher.stop(
                timeout=self.timeout_budget.shutdown_seconds,
            )
        except Exception as error:  # noqa: BLE001
            errors.append(f"version_watcher.stop:{type(error).__name__}")
        try:
            await self.reminder_scheduler.stop(
                timeout=self.timeout_budget.shutdown_seconds,
            )
        except Exception as error:  # noqa: BLE001
            errors.append(f"reminder_scheduler.stop:{type(error).__name__}")
        for agent_id, agent in tuple(self.agents.items()):
            try:
                await agent.stop()
            except Exception as error:  # noqa: BLE001
                errors.append(f"agent[{agent_id}].stop:{type(error).__name__}")
                self._log(
                    "agent.stop.failed",
                    agent_id=agent_id,
                    error_type=type(error).__name__,
                    error=format_exception(error),
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
            await self.audit.stop(timeout=self.timeout_budget.shutdown_seconds)
        except Exception as error:  # noqa: BLE001
            errors.append(f"audit.stop:{type(error).__name__}")
        try:
            await self.storage.stop(timeout=self.timeout_budget.shutdown_seconds)
        except Exception as error:  # noqa: BLE001
            errors.append(f"storage.stop:{type(error).__name__}")
        self._started = False
        self._stopped.set()
        if errors:
            self._log("bcn.stop.errors", errors=errors)

    def request_restart(self) -> None:
        """Stop, and tell whatever hosts this node to start it again."""

        self._restart_requested = True
        self._stopped.set()

    @property
    def exit_code(self) -> int:
        return RESTART_EXIT_CODE if self._restart_requested else 0

    async def wait(self) -> None:
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(signum, self._stopped.set)
            except NotImplementedError, RuntimeError:
                pass
        stop_task = asyncio.create_task(self._stopped.wait(), name="bcn-stop-signal")
        sources: list[tuple[str, ITaskFailureSource]] = [
            ("timer_wheel", self.timer_wheel),
            ("reminder_scheduler", self.reminder_scheduler),
            ("command_server", self.command_server),
        ]
        if isinstance(self.storage, ITaskFailureSource):
            sources.append(("storage", self.storage))
        failure_tasks = {
            asyncio.create_task(
                source.wait_failure(),
                name=f"bcn-critical-{name}",
            ): name
            for name, source in sources
        }
        tasks = {stop_task, *failure_tasks}
        try:
            done, _ = await asyncio.wait(
                tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )
            failure: tuple[str, BaseException] | None = None
            for task in done:
                if task is stop_task:
                    continue
                try:
                    task.result()
                except asyncio.CancelledError:
                    caught: BaseException = RuntimeError(
                        f"{failure_tasks[task]} failure monitor stopped unexpectedly"
                    )
                except BaseException as error:  # noqa: BLE001
                    caught = error
                else:
                    caught = RuntimeError(
                        f"{failure_tasks[task]} failure monitor returned unexpectedly"
                    )
                if failure is None:
                    failure = failure_tasks[task], caught
            if failure is not None:
                component, error = failure
                self._ready = False
                self._accepting = False
                self._log(
                    "bcn.critical.failed",
                    component=component,
                    error_type=type(error).__name__,
                    error=format_exception(error),
                )
                raise error
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _publish_inbox_wake(
        self,
        agent_id: str,
        message: Message[InboundAttachment],
    ) -> bool:
        agent = self.agents.get(agent_id)
        if agent is None or not agent.started:
            self._log(
                "reminder.wake.agent_unavailable",
                agent_id=agent_id,
                owner_thread_id=message.thread_id,
            )
            return False
        try:
            await agent.publish_inbox_wake(message)
        except Exception as error:  # noqa: BLE001
            self._log(
                "reminder.wake.failed",
                agent_id=agent_id,
                owner_thread_id=message.thread_id,
                error_type=type(error).__name__,
                error=format_exception(error),
            )
            return False
        return True

    def _agent_commands(self, agent_id: str) -> AgentCommands | None:
        agent = self.agents.get(agent_id)
        if agent is None or not agent.started or not self._accepting:
            return None
        return AgentCommands(agent.orchestrator.command_service, agent.actors)

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
        elif kind != "command":
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
        agent_id = request.get("agent_id")
        if not isinstance(agent_id, str) or not agent_id:
            return {
                "ok": False,
                "code": "AGENT_REQUIRED",
                "error": "agent_id must be a non-empty string",
            }
        agent = self.agents.get(agent_id)
        if agent is None or not agent.started:
            return {
                "ok": False,
                "code": "AGENT_NOT_AVAILABLE",
                "error": "Agent is not available",
            }
        return await agent.dispatch(request)

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
                    "channels": _channel_kinds(configuration),
                    "runtimes": _runtime_kinds(configuration),
                }
            )
        return {
            "started": self._started,
            "ready": self._ready,
            "accepting": self._accepting,
            "storage": self.storage.name,
            "audit": {"name": self.audit.name, **self.audit.health},
            "control": (
                None
                if self.control is None
                else {"name": self.control.name, **self.control.health}
            ),
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
            async with asyncio.timeout(self.timeout_budget.shutdown_seconds):
                await agent.stop()
        except BaseException as error:
            self._logger.debug(
                "agent cleanup after failed start failed",
                extra={"agent_id": agent.agent_id},
                exc_info=error,
            )

    async def _stop_shared_after_failed_start(self) -> None:
        try:
            await self.reminder_scheduler.stop(
                timeout=self.timeout_budget.shutdown_seconds,
            )
        except BaseException as error:
            self._logger.debug("reminder scheduler cleanup failed", exc_info=error)
        try:
            await self.command_server.stop()
        except BaseException as error:
            self._logger.debug("command server cleanup failed", exc_info=error)
        if self.control is not None:
            try:
                await self.control.stop(timeout=self.timeout_budget.shutdown_seconds)
            except BaseException as error:
                self._logger.debug("control cleanup failed", exc_info=error)
        try:
            await self.timer_wheel.close()
        except BaseException as error:
            self._logger.debug("timer wheel cleanup failed", exc_info=error)
        try:
            await self.audit.stop(timeout=self.timeout_budget.shutdown_seconds)
        except BaseException as error:
            self._logger.debug("audit cleanup failed", exc_info=error)
        try:
            await self.storage.stop(timeout=self.timeout_budget.shutdown_seconds)
        except BaseException as error:
            self._logger.debug("storage cleanup failed", exc_info=error)

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


__all__ = ["AgentStartupResult", "NodeApplication"]
