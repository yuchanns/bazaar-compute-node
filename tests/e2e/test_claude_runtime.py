from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import cast
from uuid import NAMESPACE_URL, uuid4, uuid5, uuid7

import pytest
from bcn_test_support import (
    MemoryStorage,
    RecordingAudit,
    StaticChannelBuilder,
    TestChannel,
)

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
    SharedAdapterFactories,
)
from bazaar_compute_node.contrib.claude.client import Client
from bazaar_compute_node.contrib.claude.events import TurnEventStream
from bazaar_compute_node.contrib.claude.process import (
    ProcessSpec,
    ProcessSupervisor,
    build_arguments,
)
from bazaar_compute_node.contrib.claude.runtime import Runtime
from bazaar_compute_node.core.channel import IChannel
from bazaar_compute_node.core.lifecycle import TimeoutBudget
from bazaar_compute_node.core.models import (
    ApprovalDecision,
    Message,
    MessageDirection,
    RuntimeEvent,
    RuntimeEventState,
    RuntimeSession,
    SenderIdentity,
    StreamEventKind,
)
from bazaar_compute_node.core.paths import resolve_workspace_dir
from bazaar_compute_node.core.runtime import (
    IRuntime,
    RuntimeCommandContext,
    RuntimeSandboxMode,
)
from bazaar_compute_node.core.storage import IStorage

pytestmark = pytest.mark.e2e


class _StaticRegistry(AdapterRegistry):
    def __init__(
        self,
        *,
        channel: IChannel,
        runtime: Callable[[RuntimeCommandContext], IRuntime],
    ) -> None:
        self._channel = channel
        self._runtime = runtime

    def load_agent(
        self,
        *,
        channel: str,
        runtime: str,
        storage: str,
    ) -> AgentAdapterFactories:
        del channel, runtime, storage
        return AgentAdapterFactories(
            channel=StaticChannelBuilder(self._channel),
            runtime=self._runtime,
        )


def _node(
    endpoint: Path,
    *,
    provider_call_seconds: float = 30,
    idle_timeout_seconds: float = 0,
) -> tuple[NodeApplication, TestChannel, RecordingAudit, str]:
    if shutil.which("claude") is None:
        pytest.fail("claude CLI is required for the runtime integration test")
    agent_id = str(uuid7())
    agent_name = "Claude E2E"
    channel = TestChannel()
    storage = MemoryStorage()
    audit = RecordingAudit()
    node = NodeApplication(
        configuration=NodeConfiguration(
            storage="memory",
            audit="test",
            agents=(
                AgentConfiguration(
                    id=agent_id,
                    name=agent_name,
                    channel=ChannelConfiguration(kind="test"),
                    runtime=RuntimeConfiguration(
                        kind="claudecode",
                        model="claude-opus-5",
                        sandbox_mode=RuntimeSandboxMode.DANGER_FULL_ACCESS,
                        idle_timeout_seconds=idle_timeout_seconds,
                    ),
                ),
            ),
        ),
        shared_factories=SharedAdapterFactories(
            storage=lambda: cast(IStorage, storage),
            audit=lambda: audit,
        ),
        registry=_StaticRegistry(
            channel=channel,
            runtime=lambda context: Runtime(
                context,
                model="claude-opus-5",
            ),
        ),
        endpoint_path=endpoint,
        timeout_budget=TimeoutBudget(
            startup_seconds=30,
            provider_call_seconds=provider_call_seconds,
            command_seconds=30,
            shutdown_seconds=30,
        ),
    )
    return node, channel, audit, agent_id


def _message(
    session_id: str,
    *,
    seq: int = 1,
    body: str,
) -> Message:
    channel_session_id = f"channel-{session_id}"
    return Message(
        direction=MessageDirection.INBOUND,
        seq=seq,
        message_id=f"message-{session_id}-{seq}",
        session_id=session_id,
        channel_session_id=channel_session_id,
        channel="test",
        provider_thread_id=f"thread-{session_id}",
        provider_message_id=f"provider-{session_id}-{seq}",
        received_at_ms=seq,
        sender=SenderIdentity(id="sender-id", name="Sender"),
        message_type="text",
        target=f"dm:{channel_session_id}",
        body=body,
        metadata={"sender_kind": "human"},
    )


async def _wait_for_turn_completion(
    audit: RecordingAudit,
    *,
    session_id: str,
    turn_id: str,
    timeout: float = 180,
    expected_event_name: str = "claudecode.turn.completed",
) -> None:
    terminal_event = None
    async with asyncio.timeout(timeout):
        while terminal_event is None:
            terminal_event = next(
                (
                    event
                    for event in audit.events
                    if event.correlation.bcn_session_id == session_id
                    and event.event_name
                    in {
                        "claudecode.turn.completed",
                        "claudecode.turn.failed",
                        "claudecode.turn.cancelled",
                        "claudecode.turn.unknown",
                        "claudecode.turn.transport.unknown",
                        "claudecode.turn.protocol.unknown",
                        "claudecode.turn.start.unknown",
                        "claudecode.turn.conversation_reset",
                    }
                    and event.correlation.turn_id == turn_id
                ),
                None,
            )
            await asyncio.sleep(0.05)
    assert terminal_event.event_name == expected_event_name, terminal_event


async def _wait_for_audit_event(
    audit: RecordingAudit,
    *,
    session_id: str,
    event_name: str,
    timeout: float = 60,
) -> None:
    async with asyncio.timeout(timeout):
        while not any(
            event.event_name == event_name
            and event.correlation.bcn_session_id == session_id
            for event in audit.events
        ):
            await asyncio.sleep(0.05)


def _empty_environment(session: RuntimeSession) -> Mapping[str, str]:
    del session
    return {}


def _claude_environment() -> Mapping[str, str]:
    names = Runtime(
        RuntimeCommandContext(
            run_command=_unused_command,
            environment_for_session=_empty_environment,
            agent_id="claude-e2e-environment",
            agent_name="Claude E2E",
            bot_name=lambda: None,
        )
    ).environment_variable_names()
    return {
        name: os.environ[name]
        for name in names
        if name in os.environ and name != "ANTHROPIC_API_KEY"
    }


async def _unused_command(
    command: str, arguments: Sequence[str], cwd: str | None
) -> None:
    del command, arguments, cwd


@pytest.mark.asyncio
async def test_real_claude_initialization_and_session_system_message() -> None:
    executable = shutil.which("claude")
    if executable is None:
        pytest.fail("claude CLI is required for the protocol integration test")
    workspace = resolve_workspace_dir(f"claude-e2e-{uuid4()}")
    workspace.mkdir(parents=True, mode=0o700)
    session_id = str(uuid4())
    supervisor = ProcessSupervisor(
        ProcessSpec(
            executable=executable,
            arguments=build_arguments(
                system_prompt="Reply concisely.",
                settings=json.dumps({"sandbox": {"enabled": False}}),
                session_id=session_id,
                model="claude-opus-5",
            ),
            cwd=workspace,
            environment=_claude_environment(),
        )
    )
    client = Client(supervisor)
    await supervisor.start(timeout=30)

    initialized = await client.initialize(timeout=30)
    inbox, send_error = await client.open_turn("Reply exactly OK.", timeout=30)
    assert send_error is None
    messages = []
    async with asyncio.timeout(60):
        while True:
            message = await inbox.receive()
            messages.append(message)
            if message["type"] == "result":
                break

    assert initialized.get("subtype") == "success"
    assert any(
        message.get("type") == "system" and message.get("subtype") == "init"
        for message in messages
    )
    assert messages[-1].get("session_id") == session_id
    await client.close_turn(inbox)
    await supervisor.stop(timeout=10)
    await client.close()
    assert supervisor.returncode == 0


@pytest.mark.asyncio
async def test_real_claude_turn_stream_and_running_steer(
    system_temp_dir: Path,
) -> None:
    node, channel, audit, agent_id = _node(system_temp_dir / "claude-steer.sock")
    session_id = f"claude-steer-{uuid4()}"
    scoped_session_id = str(
        uuid5(NAMESPACE_URL, f"bcn:{agent_id}:bcn-session:{session_id}")
    )
    first = _message(
        session_id,
        body=(
            "Write a numbered list of thirty common animals, with one short "
            "sentence about each animal."
        ),
    )
    second = _message(
        session_id,
        seq=2,
        body="Stop the list now and finish with a short acknowledgement.",
    )
    try:
        await node.start()
        await channel.inject(first)
        async with asyncio.timeout(120):
            while not any(
                event.kind is StreamEventKind.TOOL_PROGRESS
                and event.content is not None
                and "thirty common animals" in event.content
                for event in channel.stream_events
            ):
                await asyncio.sleep(0.05)
        await channel.inject(second)
        async with asyncio.timeout(120):
            while not any(
                event.event_name == "runtime.request.turn.steer.accepted"
                and event.correlation.turn_id == f"turn-{first.message_id}"
                for event in audit.events
            ):
                await asyncio.sleep(0.05)
        await _wait_for_turn_completion(
            audit,
            session_id=scoped_session_id,
            turn_id=f"turn-{first.message_id}",
        )
    finally:
        await node.stop()


@pytest.mark.asyncio
async def test_real_claude_active_child_exit_is_terminal_unknown(
    system_temp_dir: Path,
) -> None:
    node, channel, audit, agent_id = _node(system_temp_dir / "claude-active-exit.sock")
    session_id = f"claude-active-exit-{uuid4()}"
    scoped_session_id = str(
        uuid5(NAMESPACE_URL, f"bcn:{agent_id}:bcn-session:{session_id}")
    )
    message = _message(
        session_id,
        body="Write a long response with many detailed sections.",
    )
    recovery = _message(
        session_id,
        seq=2,
        body="After recovering the conversation, summarize the topic in one paragraph.",
    )
    try:
        await node.start()
        await channel.inject(message)
        async with asyncio.timeout(120):
            while not any(
                event.kind is StreamEventKind.AGENT_MESSAGE_DELTA
                for event in channel.stream_events
            ):
                await asyncio.sleep(0.05)
        agent = node.agents[agent_id]
        assert isinstance(agent.runtime, Runtime)
        runtime_session = agent.orchestrator.runtime_session(scoped_session_id)
        assert runtime_session is not None
        connection = agent.runtime._connections[runtime_session.id]
        pid = connection.supervisor.pid
        assert pid is not None
        provider_thread_id = runtime_session.provider_thread_id
        assert provider_thread_id is not None
        os.kill(pid, signal.SIGKILL)
        await _wait_for_turn_completion(
            audit,
            session_id=scoped_session_id,
            turn_id=f"turn-{message.message_id}",
            expected_event_name="claudecode.turn.transport.unknown",
        )
        assert runtime_session.id not in agent.runtime._connections

        await channel.inject(recovery)
        await _wait_for_turn_completion(
            audit,
            session_id=scoped_session_id,
            turn_id=f"turn-{recovery.message_id}",
        )
        recovered_session = agent.orchestrator.runtime_session(scoped_session_id)
        assert recovered_session is not None
        assert recovered_session.provider_thread_id == provider_thread_id
        recovered = agent.runtime._connections[recovered_session.id]
        assert recovered.supervisor.pid != pid
    finally:
        await node.stop()


@pytest.mark.asyncio
async def test_real_claude_idle_child_exit_rebuilds_for_next_turn(
    system_temp_dir: Path,
) -> None:
    node, channel, audit, agent_id = _node(system_temp_dir / "claude-idle-exit.sock")
    session_id = f"claude-idle-exit-{uuid4()}"
    scoped_session_id = str(
        uuid5(NAMESPACE_URL, f"bcn:{agent_id}:bcn-session:{session_id}")
    )
    first = _message(
        session_id, body="Summarize why leaves are green in one paragraph."
    )
    second = _message(
        session_id,
        seq=2,
        body="Now summarize why the sky appears blue in one paragraph.",
    )
    try:
        await node.start()
        await channel.inject(first)
        await _wait_for_turn_completion(
            audit,
            session_id=scoped_session_id,
            turn_id=f"turn-{first.message_id}",
        )
        agent = node.agents[agent_id]
        assert isinstance(agent.runtime, Runtime)
        runtime_session = agent.orchestrator.runtime_session(scoped_session_id)
        assert runtime_session is not None
        connection = agent.runtime._connections[runtime_session.id]
        pid = connection.supervisor.pid
        assert pid is not None
        os.kill(pid, signal.SIGKILL)
        await connection.supervisor.wait(timeout=10)

        await channel.inject(second)
        await _wait_for_turn_completion(
            audit,
            session_id=scoped_session_id,
            turn_id=f"turn-{second.message_id}",
        )
        rebuilt_session = agent.orchestrator.runtime_session(scoped_session_id)
        assert rebuilt_session is not None
        rebuilt = agent.runtime._connections[rebuilt_session.id]
        assert rebuilt.supervisor.pid != pid
    finally:
        await node.stop()


@pytest.mark.asyncio
async def test_real_claude_approval_lifecycle_uses_test_channel(
    system_temp_dir: Path,
) -> None:
    node, channel, audit, agent_id = _node(system_temp_dir / "claude-approval.sock")
    session_id = f"claude-approval-{uuid4()}"
    scoped_session_id = str(
        uuid5(NAMESPACE_URL, f"bcn:{agent_id}:bcn-session:{session_id}")
    )
    workspace = resolve_workspace_dir(agent_id)
    approved_note = workspace / f"approved-{uuid4()}.md"
    rejected_note = workspace / f"rejected-{uuid4()}.md"
    timed_out_note = workspace / f"timed-out-{uuid4()}.md"
    try:
        await node.start()
        approved = _message(
            session_id,
            body=(
                f"Add a project note named {approved_note.name} explaining that the "
                "release checklist has been reviewed, then confirm the update."
            ),
        )
        await channel.inject(approved)
        await _wait_for_turn_completion(
            audit,
            session_id=scoped_session_id,
            turn_id=f"turn-{approved.message_id}",
        )
        assert channel.approval_requests
        assert channel.approval_results[-1].decision is ApprovalDecision.APPROVED
        assert "release checklist" in approved_note.read_text(encoding="utf-8").lower()

        channel.set_approval_decision(
            ApprovalDecision.REJECTED,
            reason="The requested project change was not approved.",
        )
        rejected = _message(
            session_id,
            seq=2,
            body=(
                f"Add a project note named {rejected_note.name} stating that the "
                "deployment window is confirmed, then report the outcome."
            ),
        )
        await channel.inject(rejected)
        await _wait_for_turn_completion(
            audit,
            session_id=scoped_session_id,
            turn_id=f"turn-{rejected.message_id}",
        )
        assert channel.approval_results[-1].decision is ApprovalDecision.REJECTED

        approval_count = len(channel.approval_requests)
        channel.block_approvals()
        timed_out = _message(
            session_id,
            seq=3,
            body=(
                f"Add a project note named {timed_out_note.name} summarizing the "
                "pending release risks, then report the outcome."
            ),
        )
        await channel.inject(timed_out)
        async with asyncio.timeout(90):
            while not channel.cancelled_approval_requests:
                await asyncio.sleep(0.05)
        channel.release_approvals()
        await _wait_for_turn_completion(
            audit,
            session_id=scoped_session_id,
            turn_id=f"turn-{timed_out.message_id}",
        )
        assert len(channel.approval_requests) > approval_count
    finally:
        channel.release_approvals()
        await node.stop()
        for note in (approved_note, rejected_note, timed_out_note):
            if note.exists():
                note.unlink()


@pytest.mark.asyncio
async def test_real_claude_background_idle_event_restarts_runtime_timer(
    system_temp_dir: Path,
) -> None:
    node, channel, audit, agent_id = _node(
        system_temp_dir / "claude-background-idle.sock",
        idle_timeout_seconds=0.25,
    )
    session_id = f"claude-background-idle-{uuid4()}"
    scoped_session_id = str(
        uuid5(NAMESPACE_URL, f"bcn:{agent_id}:bcn-session:{session_id}")
    )
    message = _message(
        session_id,
        body=(
            "Ask a background teammate to independently verify one hundred arithmetic "
            "identities, spending at least twenty seconds on the review. Confirm as "
            "soon as the review starts, without waiting for its findings."
        ),
    )
    try:
        await node.start()
        await channel.inject(message)
        await _wait_for_turn_completion(
            audit,
            session_id=scoped_session_id,
            turn_id=f"turn-{message.message_id}",
            timeout=600,
        )
        agent = node.agents[agent_id]
        assert isinstance(agent.runtime, Runtime)
        runtime = agent.runtime
        runtime_session = agent.orchestrator.runtime_session(scoped_session_id)
        assert runtime_session is not None
        assert await runtime.has_background_job(runtime_session, timeout=30)
        connection = runtime._connections[runtime_session.id]
        pid = connection.supervisor.pid
        assert pid is not None
        assert connection.supervisor.is_running

        async with asyncio.timeout(600):
            while agent.orchestrator.runtime_session(scoped_session_id) is not None:
                await asyncio.sleep(0.05)
        await _wait_for_audit_event(
            audit,
            session_id=scoped_session_id,
            event_name="runtime.process.stop.completed",
        )
        assert not connection.supervisor.is_running
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    finally:
        await node.stop()


@pytest.mark.asyncio
async def test_real_claude_background_lifecycle_keeps_process_running(
    system_temp_dir: Path,
) -> None:
    node, channel, audit, agent_id = _node(system_temp_dir / "claude-background.sock")
    session_id = f"claude-background-{uuid4()}"
    scoped_session_id = str(
        uuid5(NAMESPACE_URL, f"bcn:{agent_id}:bcn-session:{session_id}")
    )
    first = _message(
        session_id,
        body=(
            "Delegate a detailed independent review of the first fifty prime "
            "numbers to a teammate and let that work continue asynchronously. "
            "Reply as soon as the delegation is underway without waiting for its "
            "findings."
        ),
    )
    second = _message(
        session_id,
        seq=2,
        body=(
            "While that review is being incorporated, summarize the practical "
            "difference between prime and composite numbers in two sentences."
        ),
    )
    try:
        await node.start()
        await channel.inject(first)
        first_turn_id = f"turn-{first.message_id}"
        agent = node.agents[agent_id]
        assert isinstance(agent.runtime, Runtime)
        runtime = agent.runtime
        observed_background = False
        connection = None
        second_injected = asyncio.Event()
        second_tasks: list[asyncio.Task[None]] = []
        async with asyncio.timeout(180):
            while True:
                session = agent.orchestrator.runtime_session(scoped_session_id)
                if session is not None:
                    observed_background = observed_background or (
                        await runtime.has_background_job(session, timeout=1)
                    )
                    if connection is None:
                        connection = runtime._connections.get(session.id)
                        if connection is not None:
                            original_observer = connection.client._message_observer

                            def observe(
                                message: dict[str, object],
                                previous: Callable[[dict[str, object]], None]
                                | None = original_observer,
                            ) -> None:
                                if previous is not None:
                                    previous(message)
                                if (
                                    message.get("type") == "system"
                                    and message.get("subtype") == "task_notification"
                                    and not second_injected.is_set()
                                ):
                                    second_injected.set()
                                    second_tasks.append(
                                        asyncio.create_task(channel.inject(second))
                                    )

                            connection.client.set_message_observer(observe)
                if any(
                    isinstance(event, RuntimeEvent)
                    and event.turn_id == first_turn_id
                    and event.state is RuntimeEventState.COMPLETED
                    for event in channel.events
                ):
                    break
                await asyncio.sleep(0.01)

        assert observed_background
        session = agent.orchestrator.runtime_session(scoped_session_id)
        assert session is not None
        assert connection is not None
        original_pid = connection.supervisor.pid
        assert original_pid is not None

        async with asyncio.timeout(180):
            await second_injected.wait()
        assert second_tasks
        await second_tasks[0]
        second_turn_id = f"turn-{second.message_id}"
        observed_adoption = False
        async with asyncio.timeout(180):
            while True:
                inbox = connection.client._turn_inbox
                observed_adoption = observed_adoption or (
                    inbox is not None and inbox.adopted_provider_wake
                )
                if any(
                    event.correlation.bcn_session_id == scoped_session_id
                    and event.event_name.endswith("turn.completed")
                    and event.correlation.turn_id == second_turn_id
                    for event in audit.events
                ):
                    break
                await asyncio.sleep(0.01)
        if not observed_adoption:
            inbox = connection.client._turn_inbox
            pytest.fail(
                json.dumps(
                    {
                        "observed_adoption": observed_adoption,
                        "provider_wake_active": connection.client.provider_wake_active,
                        "inbox_adopted": (
                            inbox.adopted_provider_wake if inbox is not None else None
                        ),
                        "inbox_size": (
                            inbox._messages.qsize() if inbox is not None else None
                        ),
                        "active_turn_id": connection.active_turn_id,
                        "pending_human_results": connection.pending_human_results,
                        "background": await runtime.has_background_job(
                            session, timeout=1
                        ),
                        "approval_actions": [
                            request.action for request in channel.approval_requests
                        ],
                        "turn_events": [
                            event.event_name
                            for event in audit.events
                            if event.correlation.turn_id == second_turn_id
                        ],
                    },
                    sort_keys=True,
                )
            )
        async with asyncio.timeout(180):
            while await runtime.has_background_job(session, timeout=1):
                await asyncio.sleep(0.05)

        assert runtime._connections[session.id].supervisor.pid == original_pid
        assert channel.approval_requests
    finally:
        await node.stop()


@pytest.mark.asyncio
async def test_real_claude_provider_limit_result_remains_authoritative() -> None:
    executable = shutil.which("claude")
    if executable is None:
        pytest.fail("claude CLI is required for the provider-limit integration test")
    environment = _claude_environment()
    version_text = await asyncio.to_thread(
        subprocess.check_output,
        (executable, "--version"),
        cwd=Path.cwd(),
        env=environment,
        text=True,
    )
    version_parts = version_text.split()[0].split(".")
    claude_version = tuple(int(part) for part in version_parts)
    assert len(claude_version) == 3
    workspace = resolve_workspace_dir(f"claude-e2e-{uuid4()}")
    workspace.mkdir(parents=True, mode=0o700)
    session_id = str(uuid4())
    base_arguments = build_arguments(
        system_prompt="Follow the user's tool instruction.",
        settings=json.dumps({"sandbox": {"enabled": False}}),
        session_id=session_id,
        model="claude-opus-5",
    )
    supervisor = ProcessSupervisor(
        ProcessSpec(
            executable=executable,
            arguments=(*base_arguments[:-2], "--max-turns", "1", *base_arguments[-2:]),
            cwd=workspace,
            environment=environment,
        )
    )
    client = Client(supervisor)
    await supervisor.start(timeout=30)
    await client.initialize(timeout=30)

    async def claim_result(message: dict[str, object]) -> bool:
        del message
        return True

    async def close_turn() -> None:
        return None

    async def ignore_unusable(error: BaseException) -> None:
        del error

    inbox, send_error = await client.open_turn(
        (
            "Ask another agent to calculate 23 multiplied by 29, then use its "
            "result in your answer."
        ),
        timeout=30,
    )
    assert send_error is None
    stream = TurnEventStream(
        inbox,
        session_id="bcn-provider-limit",
        turn_id=str(uuid4()),
        provider_thread_id=session_id,
        claude_version=(
            claude_version[0],
            claude_version[1],
            claude_version[2],
        ),
        claim_result=claim_result,
        on_closed=close_turn,
        on_unusable=ignore_unusable,
    )
    terminal = None
    async with asyncio.timeout(90):
        async for item in stream:
            if isinstance(item, RuntimeEvent) and item.state in {
                RuntimeEventState.COMPLETED,
                RuntimeEventState.FAILED,
                RuntimeEventState.CANCELLED,
                RuntimeEventState.UNKNOWN,
            }:
                terminal = item

    assert terminal is not None
    assert terminal.state is RuntimeEventState.FAILED
    assert terminal.error_kind == "provider_failed"
    await client.close_turn(inbox)
    await supervisor.stop(timeout=10)
    await client.close()
