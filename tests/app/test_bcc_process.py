from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest
from bcn_test_support import TestChannel, TestRuntime

import bazaar_compute_node.app.agent as agent_module
import bazaar_compute_node.app.wrapper as wrapper_module
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
from bazaar_compute_node.app.transport import LocalCommandClient
from bazaar_compute_node.contrib.sqlite import SqliteDatabase
from bazaar_compute_node.core.channel import ChannelContext, ChannelIdentity, IChannel
from bazaar_compute_node.core.lifecycle import TimeoutBudget
from bazaar_compute_node.core.models import (
    InboundMessage,
    RuntimeSession,
    SenderIdentity,
)
from bazaar_compute_node.core.orchestration.command import OutboundAttachmentResolver
from bazaar_compute_node.core.runtime import IRuntime, RuntimeCommandContext

AGENT_A_ID = "0198d4e6-29c5-7465-b74b-88db31f0c118"
AGENT_B_ID = "0198d4e6-29c5-7465-b74b-88db31f0c119"
AGENT_NAMES = {AGENT_A_ID: "Agent A", AGENT_B_ID: "Agent B"}


class _RoutingChannelBuilder:
    def __init__(self, channels: Mapping[str, IChannel]) -> None:
        self._channels = channels

    def build(self, context: ChannelContext) -> IChannel:
        return self._channels[context.agent_id]


class _RoutingRegistry(AdapterRegistry):
    def __init__(
        self,
        channels: Mapping[str, IChannel],
        runtimes: Mapping[str, IRuntime],
    ) -> None:
        self._channels = channels
        self._runtimes = runtimes

    def load_agent(
        self,
        *,
        channel: str,
        runtime: str,
        storage: str,
    ) -> AgentAdapterFactories:
        del channel, runtime, storage

        def runtime_factory(context: RuntimeCommandContext) -> IRuntime:
            return self._runtimes[context.agent_id]

        return AgentAdapterFactories(
            channel=_RoutingChannelBuilder(self._channels),
            runtime=runtime_factory,
        )


def _make_node(
    tmp_path: Path,
    *,
    env_include: tuple[str, ...] = (),
) -> tuple[
    NodeApplication,
    dict[str, TestChannel],
    dict[str, TestRuntime],
]:
    channels = {AGENT_A_ID: TestChannel(), AGENT_B_ID: TestChannel()}
    runtimes = {AGENT_A_ID: TestRuntime(), AGENT_B_ID: TestRuntime()}
    configuration = NodeConfiguration(
        storage="sqlite",
        audit="test",
        agents=tuple(
            AgentConfiguration(
                id=agent_id,
                name=AGENT_NAMES[agent_id],
                channel=ChannelConfiguration(kind="test"),
                runtime=RuntimeConfiguration(
                    kind="test",
                    env_include=env_include,
                ),
            )
            for agent_id in (AGENT_A_ID, AGENT_B_ID)
        ),
    )
    node = NodeApplication(
        configuration=configuration,
        shared_factories=AdapterRegistry().load_shared(storage="sqlite", audit="test"),
        registry=_RoutingRegistry(channels, runtimes),
        endpoint_path=tmp_path / "bcn.sock",
        timeout_budget=TimeoutBudget(
            startup_seconds=2,
            provider_call_seconds=2,
            command_seconds=2,
            shutdown_seconds=2,
        ),
    )
    return node, channels, runtimes


def _make_message() -> InboundMessage:
    return InboundMessage(
        seq=1,
        message_id="message-agent-a",
        session_id="provider-session-a",
        channel_session_id="provider-channel-a",
        channel="test",
        provider_thread_id="provider-thread-a",
        provider_message_id="provider-message-a",
        received_at_ms=1,
        sender=SenderIdentity(id="sender-id", name="sender"),
        message_type="text",
        canonical_target="dm:provider-channel-a",
        body="hello",
    )


async def _wait_for_runtime_session(runtime: TestRuntime) -> RuntimeSession:
    for _ in range(100):
        if runtime.started_sessions:
            return runtime.started_sessions[0]
        await asyncio.sleep(0.01)
    raise AssertionError("runtime session was not started")


@pytest.mark.asyncio
async def test_runtime_bot_name_prefers_channel_name_then_id(
    tmp_path: Path,
) -> None:
    node, channels, _ = _make_node(tmp_path)
    channel = channels[AGENT_A_ID]
    channel.identity = ChannelIdentity(id="provider-id", name="Provider Name")

    await node.start()
    try:
        context = node.agents[AGENT_A_ID]._runtime_context
        assert context.agent_name == AGENT_NAMES[AGENT_A_ID]
        assert context.bot_name() == "Provider Name"

        channel.identity = ChannelIdentity(id="provider-id")
        assert context.bot_name() == "provider-id"

        channel.identity = None
        assert context.bot_name() is None
    finally:
        await node.stop()


@pytest.mark.asyncio
async def test_agents_install_isolated_wrappers_and_runtime_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        agent_module,
        "resolve_workspace_dir",
        lambda agent_id: tmp_path / "workspaces" / agent_id,
    )
    node, _, _ = _make_node(tmp_path)
    await node.start()
    wrapper_name = "bcc.cmd" if os.name == "nt" else "bcc"
    try:
        wrapper_directories = {
            agent_id: tmp_path / "workspaces" / agent_id / ".bcn" / "bin"
            for agent_id in AGENT_NAMES
        }
        for agent_id, application in node.agents.items():
            environment = application._build_command_environment(
                f"session-{agent_id}",
                f"runtime-{agent_id}",
            )
            wrapper_directory = wrapper_directories[agent_id]
            wrapper_path = wrapper_directory / wrapper_name
            assert application._wrapper_path == wrapper_path
            assert wrapper_path.is_file()
            assert environment["BCN_AGENT_ID"] == agent_id
            assert environment["PATH"].split(os.pathsep)[0] == str(wrapper_directory)
            assert all(
                str(other_directory) not in environment["PATH"]
                for other_id, other_directory in wrapper_directories.items()
                if other_id != agent_id
            )
    finally:
        await node.stop()

    for agent_id in AGENT_NAMES:
        wrapper_directory = wrapper_directories[agent_id]
        assert not (wrapper_directory / wrapper_name).exists()
        if os.name == "nt":
            assert not (wrapper_directory / "bcc.ps1").exists()


def test_install_bcc_wrapper_renders_windows_variants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wrapper_module.os, "name", "nt")
    monkeypatch.setattr(wrapper_module.sys, "executable", r"C:\Python\python.exe")

    command_path = wrapper_module.install_bcc_wrapper(
        tmp_path,
        agent_id="agent-a",
    )
    try:
        assert command_path.name == "bcc.cmd"
        assert command_path.read_text(encoding="utf-8").splitlines() == [
            "@echo off",
            'set "BCN_AGENT_ID=agent-a"',
            'set "PYTHONUTF8=1"',
            'set "PYTHONIOENCODING=utf-8"',
            '"C:\\Python\\python.exe" -m bazaar_compute_node.bcc %*',
        ]
        assert (tmp_path / "bcc.ps1").read_text(encoding="utf-8").splitlines() == [
            "$env:BCN_AGENT_ID = 'agent-a'",
            "$env:PYTHONUTF8 = '1'",
            "$env:PYTHONIOENCODING = 'utf-8'",
            '& "C:\\Python\\python.exe" -m bazaar_compute_node.bcc @args',
            "exit $LASTEXITCODE",
        ]
    finally:
        wrapper_module.remove_bcc_wrapper(command_path)


def test_install_bcc_wrapper_rejects_unsafe_agent_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unsupported wrapper characters"):
        wrapper_module.install_bcc_wrapper(tmp_path, agent_id="agent;touch")
    assert not tuple(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_runtime_error_redaction_uses_injected_token_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SERVICE_TOKEN", "service-token-value")
    monkeypatch.setenv("ORDINARY_VALUE", "ordinary-value")
    node, _, _ = _make_node(
        tmp_path,
        env_include=("SERVICE_TOKEN", "ORDINARY_VALUE"),
    )
    await node.start()
    try:
        application = node.agents[AGENT_A_ID]
        environment = application._build_command_environment(
            "session-agent-a",
            "runtime-agent-a",
        )
        detail = application._error_feedback_detail(
            "session-agent-a",
            "failure "
            f"{environment['BCN_COMMAND_CAPABILITY']} "
            "service-token-value ordinary-value",
        )

        assert detail == "failure <redacted> <redacted> ordinary-value"
    finally:
        await node.stop()


@pytest.mark.asyncio
async def test_agent_capability_and_outbound_identity_are_scoped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        agent_module,
        "resolve_workspace_dir",
        lambda agent_id: tmp_path / "workspaces" / agent_id,
    )
    node, channels, runtimes = _make_node(tmp_path)
    await node.start()
    try:
        await channels[AGENT_A_ID].inject(_make_message())
        runtime_session = await _wait_for_runtime_session(runtimes[AGENT_A_ID])
        application = node.agents[AGENT_A_ID]
        environment = application._build_command_environment(
            runtime_session.bcn_session_id,
            runtime_session.id,
        )
        request = {
            "kind": "command",
            "resource": "message",
            "command": "check",
            "session_id": runtime_session.bcn_session_id,
            "runtime_session_id": runtime_session.id,
            "session_capability": environment["BCN_COMMAND_CAPABILITY"],
        }

        monkeypatch.setenv("BCN_AGENT_ID", AGENT_A_ID)
        check_response = await LocalCommandClient.request(node.endpoint, request)
        assert check_response["ok"] is True

        storage = cast(SqliteDatabase, node.storage)
        async with storage.scope(
            AGENT_A_ID, AGENT_NAMES[AGENT_A_ID]
        ).transaction() as transaction:
            messages = await transaction.list_inbound_messages(
                runtime_session.bcn_session_id
            )
        assert len(messages) == 1
        target = messages[0].canonical_target

        send_response = await LocalCommandClient.request(
            node.endpoint,
            {
                **request,
                "command": "send",
                "target": target,
                "body": "reply",
                "command_id": "command-agent-a",
                "attachment_paths": [],
            },
        )
        assert send_response["ok"] is True
        result = send_response["result"]
        assert isinstance(result, Mapping)
        outbound = result["outbound"]
        assert isinstance(outbound, Mapping)
        outbound_id = outbound["outbound_message_id"]
        assert isinstance(outbound_id, str)

        async with storage.transaction() as transaction:
            identity = await transaction.fetchone(
                "SELECT agent_id, agent_name FROM outbound_messages "
                "WHERE outbound_message_id = ?",
                (outbound_id,),
            )
        assert identity is not None
        assert identity["agent_id"] == AGENT_A_ID
        assert identity["agent_name"] == AGENT_NAMES[AGENT_A_ID]

        monkeypatch.setenv("BCN_AGENT_ID", AGENT_B_ID)
        forged_response = await LocalCommandClient.request(node.endpoint, request)
        assert forged_response["ok"] is False
        assert forged_response["code"] in {
            "SESSION_NOT_FOUND",
            "SESSION_BINDING_FAILED",
        }
    finally:
        await node.stop()


def test_outbound_attachment_resolver_rejects_another_agent_workspace(
    tmp_path: Path,
) -> None:
    agent_a_workspace = tmp_path / "agent-a"
    agent_b_file = tmp_path / "agent-b" / "secret.txt"
    agent_a_workspace.mkdir()
    agent_b_file.parent.mkdir()
    agent_b_file.write_text("secret", encoding="utf-8")

    resolver = OutboundAttachmentResolver(lambda: agent_a_workspace)
    with pytest.raises(ValueError, match="within the workspace"):
        resolver((str(agent_b_file),))
