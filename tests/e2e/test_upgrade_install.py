from __future__ import annotations

import asyncio
import os
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest
from bcn_test_support import TestChannel, TestRuntime

import bazaar_compute_node.app.agent as agent_module
from bazaar_compute_node.app.application import NodeApplication
from bazaar_compute_node.app.config import (
    AgentConfiguration,
    ChannelConfiguration,
    NodeConfiguration,
    RuntimeConfiguration,
)
from bazaar_compute_node.app.registry import (
    AdapterRegistry,
    AgentAdapterFactories,
)
from bazaar_compute_node.app.resource_dispatch import CommandDispatcher
from bazaar_compute_node.app.upgrade import UpgradeError, UpgradeService
from bazaar_compute_node.app.version_check import VersionWatcher
from bazaar_compute_node.contrib.sqlite import SqliteDatabase
from bazaar_compute_node.core.channel import ChannelContext, IChannel
from bazaar_compute_node.core.lifecycle import TimeoutBudget
from bazaar_compute_node.core.models import (
    Message,
    MessageDirection,
    ReminderState,
    RuntimeSession,
    SenderIdentity,
)
from bazaar_compute_node.core.runtime import IRuntime, RuntimeCommandContext
from bazaar_compute_node.core.timerwheel import TimerWheel

pytestmark = pytest.mark.e2e

AGENT_ID = "0198d4e6-29c5-7465-b74b-88db31f0c118"


class _StaticRegistry(AdapterRegistry):
    def __init__(self, channel: IChannel, runtime: IRuntime) -> None:
        self._channel = channel
        self._runtime = runtime

    def load_agent(
        self,
        *,
        channel: str,
        runtimes: tuple[str, ...] | list[str],
    ) -> AgentAdapterFactories:
        del channel

        def runtime_factory(context: RuntimeCommandContext) -> IRuntime:
            del context
            return self._runtime

        return AgentAdapterFactories(
            channel=_StaticChannel(self._channel),
            runtimes={kind: runtime_factory for kind in runtimes},
        )


class _StaticChannel:
    def __init__(self, channel: IChannel) -> None:
        self._channel = channel

    def build(self, context: ChannelContext) -> IChannel:
        del context
        return self._channel


def _upgrade_node(tmp_path: Path) -> tuple[NodeApplication, TestChannel, TestRuntime]:
    channel = TestChannel()
    runtime = TestRuntime()
    node = NodeApplication(
        configuration=NodeConfiguration(
            version_check=False,
            storage="sqlite",
            audit="test",
            agents=(
                AgentConfiguration(
                    id=AGENT_ID,
                    name="Test Agent",
                    channel=ChannelConfiguration(kind="test"),
                    runtimes=(RuntimeConfiguration(kind="test"),),
                ),
            ),
        ),
        shared_factories=AdapterRegistry().load_shared(storage="sqlite", audit="test"),
        registry=_StaticRegistry(channel, runtime),
        endpoint_path=tmp_path / "bcn.sock",
        timeout_budget=TimeoutBudget(
            startup_seconds=5,
            provider_call_seconds=5,
            command_seconds=5,
            shutdown_seconds=5,
        ),
    )
    return node, channel, runtime


def _anchor_message() -> Message:
    return Message(
        direction=MessageDirection.INBOUND,
        seq=1,
        message_id="0198d4e6-29c5-7465-b74b-88db31f0c200",
        session_id="provider-session-a",
        channel_session_id="provider-channel-a",
        channel="test",
        provider_thread_id="provider-thread-a",
        provider_message_id="provider-message-a",
        received_at_ms=1,
        sender=SenderIdentity(id="sender-id", name="sender"),
        message_type="text",
        target="dm:provider-channel-a",
        body="please upgrade",
    )


async def _started_session(runtime: TestRuntime) -> RuntimeSession:
    async with asyncio.timeout(30):
        while not runtime.started_sessions:
            await asyncio.sleep(0.01)
    return runtime.started_sessions[0]


def _isolated_uv(monkeypatch: pytest.MonkeyPatch, root: Path) -> Path:
    """Point uv at directories of our own so the real install stays contained."""

    tool_dir = root / "tools"
    bin_dir = root / "bin"
    tool_dir.mkdir()
    bin_dir.mkdir()
    monkeypatch.setenv("UV_TOOL_DIR", str(tool_dir))
    monkeypatch.setenv("UV_TOOL_BIN_DIR", str(bin_dir))
    return tool_dir


async def _announced_upgrade(current_version: str) -> tuple[VersionWatcher, TimerWheel]:
    wheel = TimerWheel()
    await wheel.start()
    watcher = VersionWatcher(
        timer_wheel=wheel,
        current_version=current_version,
        request_timeout_seconds=30,
    )
    await watcher.start(timeout=30)
    async with asyncio.timeout(60):
        while watcher.available_version() is None:
            await asyncio.sleep(0.05)
    return watcher, wheel


@pytest.mark.asyncio
async def test_upgrade_needs_uv_to_install_anything(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _isolated_uv(monkeypatch, tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    wheel = TimerWheel()
    service = UpgradeService(
        version_watcher=VersionWatcher(
            timer_wheel=wheel,
            current_version="0.0.1",
            request_timeout_seconds=30,
        ),
        installed_version="0.0.1",
        timer_wheel=wheel,
    )

    with pytest.raises(UpgradeError) as failure:
        await service.install("0.1.0")

    # case: a node installed some other way says so instead of half-upgrading
    assert "uv" in str(failure.value)
    assert os.environ["PATH"] == str(tmp_path / "empty")


def _sealed_path(monkeypatch: pytest.MonkeyPatch, root: Path) -> Path:
    """Replace PATH with a directory the installed bcn cannot be reached through.

    ``resolve_bcn_executable`` resolves through PATH, so an upgrade run by a test
    would otherwise restart the node this machine actually runs. Only a recorder
    and a copy of uv are reachable here.
    """

    real_uv = shutil.which("uv")
    assert real_uv is not None, "uv is required for the upgrade acceptance test"
    sealed = root / "path"
    sealed.mkdir()
    shutil.copy2(real_uv, sealed / Path(real_uv).name)
    record = root / "restart-argv"
    recorder = sealed / "bcn"
    recorder.write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$@" > {record}\n',
        encoding="utf-8",
    )
    recorder.chmod(0o755)
    monkeypatch.setenv("PATH", os.pathsep.join((str(sealed), "/usr/bin", "/bin")))
    assert shutil.which("bcn") == str(recorder)
    return record


@pytest.mark.asyncio
async def test_real_upgrade_installs_then_schedules_then_asks_for_a_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        agent_module,
        "resolve_workspace_dir",
        lambda agent_id: tmp_path / "workspaces" / agent_id,
    )
    tool_dir = _isolated_uv(monkeypatch, tmp_path)
    record = _sealed_path(monkeypatch, tmp_path)
    watcher, wheel = await _announced_upgrade("0.0.1")
    upgrade = UpgradeService(
        version_watcher=watcher,
        installed_version="0.0.1",
        timer_wheel=wheel,
    )
    node, channel, runtime = _upgrade_node(tmp_path)
    await node.start()
    try:
        await channel.inject(_anchor_message())
        session = await _started_session(runtime)
        application = node.agents[AGENT_ID]
        dispatcher = CommandDispatcher(
            application.orchestrator.command_service,
            reminder_service=application.reminder_service,
            timeout_budget=application.timeout_budget,
            session_binding_validator=application._validate_session_binding,
            upgrade_service=upgrade,
        )
        dispatcher.start_accepting()
        environment = application._build_command_environment(
            session.bcn_session_id,
            session.id,
            runtime_index=0,
        )

        async with asyncio.timeout(600):
            response = await dispatcher(
                {
                    "kind": "command",
                    "resource": "node",
                    "command": "upgrade",
                    "agent_id": AGENT_ID,
                    "session_id": session.bcn_session_id,
                    "runtime_session_id": session.id,
                    "session_capability": environment["BCN_COMMAND_CAPABILITY"],
                    "message_id": _anchor_message().message_id,
                }
            )

        assert response["ok"] is True, response
        result = cast(Mapping[str, object], response["result"])
        assert result["upgrade_version"] == watcher.available_version()

        # case: the release is on disk by the time the command answers
        assert (tool_dir / "bazaar-compute-node").is_dir()

        # case: the follow-up is durable, so the restart cannot lose it
        repository = cast(SqliteDatabase, node.storage).scope(AGENT_ID, "Test Agent")
        reminders = await repository.list_reminders(
            session.bcn_session_id,
            frozenset({ReminderState.SCHEDULED}),
        )
        assert [reminder.reminder_id for reminder in reminders] == [
            result["reminder_id"]
        ]
        assert str(watcher.available_version()) in reminders[0].title

        # case: only then does the node ask the installed CLI to restart it
        async with asyncio.timeout(30):
            while not record.exists():
                await asyncio.sleep(0.05)
        assert record.read_text(encoding="utf-8").split() == [
            "system-service",
            "restart",
        ]
    finally:
        await node.stop()
        await watcher.stop(timeout=5)
        await wheel.close()


@pytest.mark.asyncio
async def test_real_upgrade_failure_reaches_the_agent_without_a_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        agent_module,
        "resolve_workspace_dir",
        lambda agent_id: tmp_path / "workspaces" / agent_id,
    )
    tool_dir = _isolated_uv(monkeypatch, tmp_path)
    record = _sealed_path(monkeypatch, tmp_path)
    monkeypatch.setenv("UV_INDEX_URL", "http://127.0.0.1:1/simple")
    watcher, wheel = await _announced_upgrade("0.0.1")
    upgrade = UpgradeService(
        version_watcher=watcher,
        installed_version="0.0.1",
        timer_wheel=wheel,
    )
    node, channel, runtime = _upgrade_node(tmp_path)
    await node.start()
    try:
        await channel.inject(_anchor_message())
        session = await _started_session(runtime)
        application = node.agents[AGENT_ID]
        dispatcher = CommandDispatcher(
            application.orchestrator.command_service,
            reminder_service=application.reminder_service,
            timeout_budget=application.timeout_budget,
            session_binding_validator=application._validate_session_binding,
            upgrade_service=upgrade,
        )
        dispatcher.start_accepting()
        environment = application._build_command_environment(
            session.bcn_session_id,
            session.id,
            runtime_index=0,
        )
        request = {
            "kind": "command",
            "resource": "node",
            "command": "upgrade",
            "agent_id": AGENT_ID,
            "session_id": session.bcn_session_id,
            "runtime_session_id": session.id,
            "session_capability": environment["BCN_COMMAND_CAPABILITY"],
            "message_id": _anchor_message().message_id,
        }
        response = await dispatcher(request)

        # case: the Agent is told why, in the answer to the command it ran
        assert response["ok"] is False, response
        assert response["code"] == "UPGRADE_INSTALL_FAILED"
        assert "Connection refused" in cast(str, response["error"])

        # case: nothing was installed, promised or restarted
        repository = cast(SqliteDatabase, node.storage).scope(AGENT_ID, "Test Agent")
        assert not (tool_dir / "bazaar-compute-node").exists()
        assert not record.exists()
        assert (
            await repository.list_reminders(
                session.bcn_session_id,
                frozenset({ReminderState.SCHEDULED}),
            )
            == ()
        )

        # case: the user can ask for it again
        assert (await dispatcher(request))["code"] == "UPGRADE_INSTALL_FAILED"
    finally:
        await node.stop()
        await watcher.stop(timeout=5)
        await wheel.close()
