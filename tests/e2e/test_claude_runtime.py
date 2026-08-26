from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from time import time_ns
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
    ProcessState,
    ProcessSupervisor,
    build_arguments,
)
from bazaar_compute_node.contrib.claude.runtime import Runtime
from bazaar_compute_node.core.approval import IApprovalHandler
from bazaar_compute_node.core.channel import IChannel
from bazaar_compute_node.core.lifecycle import TimeoutBudget
from bazaar_compute_node.core.models import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalResult,
    Message,
    MessageDirection,
    RuntimeEvent,
    RuntimeEventState,
    RuntimeSession,
    RuntimeTurn,
    RuntimeTurnState,
    SenderIdentity,
    SessionRuntimeState,
    StreamEvent,
    StreamEventKind,
)
from bazaar_compute_node.core.outcomes import ProviderCallStatus
from bazaar_compute_node.core.paths import resolve_workspace_dir
from bazaar_compute_node.core.runtime import (
    IRuntime,
    RuntimeCommandContext,
    RuntimeSandboxMode,
    RuntimeSessionUnavailable,
)
from bazaar_compute_node.core.storage import IStorage

pytestmark = pytest.mark.e2e


class _NoopApprovalHandler(IApprovalHandler):
    async def request_approval(
        self, request: ApprovalRequest, *, timeout: float
    ) -> ApprovalResult:
        del timeout
        raise AssertionError(f"unexpected approval request: {request.request_id}")


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
                        model="kimi",
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
                model="kimi",
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
                    }
                    and event.correlation.turn_id == turn_id
                ),
                None,
            )
            await asyncio.sleep(0.05)
    assert terminal_event.event_name == "claudecode.turn.completed", terminal_event


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


def _session(agent_id: str, session_id: str) -> RuntimeSession:
    now = time_ns() // 1_000_000
    return RuntimeSession(
        id=session_id,
        bcn_session_id=f"bcn-{session_id}",
        channel_session_id=f"channel-{session_id}",
        runtime="claudecode",
        workspace_id=agent_id,
        created_at_ms=now,
        updated_at_ms=now,
    )


def _turn(session: RuntimeSession) -> RuntimeTurn:
    return RuntimeTurn(
        turn_id=str(uuid4()),
        session_id=session.id,
        state=RuntimeTurnState.RUNNING,
        started_at_ms=time_ns() // 1_000_000,
        client_user_message_id=str(uuid4()),
    )


def _context(agent_id: str) -> RuntimeCommandContext:
    environment = _claude_environment()

    def environment_for_session(session: RuntimeSession) -> Mapping[str, str]:
        del session
        return environment

    return RuntimeCommandContext(
        run_command=_unused_command,
        environment_for_session=environment_for_session,
        agent_id=agent_id,
        agent_name="Claude E2E",
        bot_name=lambda: "Claude E2E",
        runtime_options={"model": "kimi"},
        sandbox_mode=RuntimeSandboxMode.DANGER_FULL_ACCESS,
        startup_timeout_seconds=30,
    )


@pytest.mark.asyncio
async def test_real_claude_runtime_starts_reconciles_and_reaps_process() -> None:
    if shutil.which("claude") is None:
        pytest.fail("claude CLI is required for the runtime integration test")
    agent_id = f"claude-e2e-{uuid4()}"
    runtime = Runtime(_context(agent_id), model="kimi")
    session = _session(agent_id, str(uuid4()))
    await runtime.start(timeout=30)

    started = await runtime.start_session(session, timeout=30)

    assert started.status is ProviderCallStatus.CONFIRMED
    assert started.value is not None
    assert started.value.provider_thread_id is not None
    fresh_connection = runtime._connections[session.id]
    fresh_pid = fresh_connection.supervisor.pid
    assert fresh_pid is not None
    inbox, send_error = await fresh_connection.client.open_turn(
        "Reply exactly OK.", timeout=30
    )
    assert send_error is None
    async with asyncio.timeout(60):
        while (await inbox.receive())["type"] != "result":
            pass
    await fresh_connection.client.close_turn(inbox)
    await runtime.stop_session(started.value, timeout=10)

    reconciled = await runtime.reconcile_session(started.value, None, None, timeout=30)

    assert reconciled.status is ProviderCallStatus.CONFIRMED
    assert reconciled.value is not None
    assert reconciled.value.state is SessionRuntimeState.IDLE
    connection = runtime._connections[session.id]
    pid = connection.supervisor.pid
    assert pid is not None
    await runtime.stop(timeout=10)
    assert connection.supervisor.state is ProcessState.STOPPED
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


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
                model="kimi",
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
async def test_real_claude_turn_stream_and_running_steer() -> None:
    if shutil.which("claude") is None:
        pytest.fail("claude CLI is required for the turn integration test")
    agent_id = f"claude-e2e-{uuid4()}"
    runtime = Runtime(_context(agent_id), model="kimi")
    session = _session(agent_id, str(uuid4()))
    await runtime.start(timeout=30)
    started = await runtime.start_session(session, timeout=30)
    assert started.value is not None
    session = started.value
    turn = _turn(session)
    stream = await runtime.start_turn(
        session,
        turn,
        (
            "Write a numbered list of thirty common animals, with one short "
            "sentence about each animal."
        ),
        _NoopApprovalHandler(),
        timeout=30,
    )
    items = []
    steered = False
    async with asyncio.timeout(120):
        async for item in stream:
            items.append(item)
            if (
                not steered
                and isinstance(item, StreamEvent)
                and item.kind is StreamEventKind.AGENT_MESSAGE_DELTA
            ):
                steered = await runtime.steer_turn(
                    session,
                    turn,
                    "Stop the list now and finish with a short acknowledgement.",
                    timeout=30,
                )

    terminals = [
        item
        for item in items
        if isinstance(item, RuntimeEvent)
        and item.state
        in {
            RuntimeEventState.COMPLETED,
            RuntimeEventState.FAILED,
            RuntimeEventState.CANCELLED,
            RuntimeEventState.UNKNOWN,
        }
    ]
    assert steered
    assert any(
        isinstance(item, StreamEvent)
        and item.kind is StreamEventKind.AGENT_MESSAGE_DELTA
        for item in items
    )
    assert len(terminals) == 1
    assert terminals[0].state is RuntimeEventState.COMPLETED
    assert "usage" in terminals[0].metadata
    assert not await runtime.has_background_job(session, timeout=1)
    await runtime.stop(timeout=10)


@pytest.mark.asyncio
async def test_real_claude_interrupt_drains_aborted_result() -> None:
    if shutil.which("claude") is None:
        pytest.fail("claude CLI is required for the interrupt integration test")
    agent_id = f"claude-e2e-{uuid4()}"
    runtime = Runtime(_context(agent_id), model="kimi")
    session = _session(agent_id, str(uuid4()))
    await runtime.start(timeout=30)
    started = await runtime.start_session(session, timeout=30)
    assert started.value is not None
    session = started.value
    turn = _turn(session)
    stream = await runtime.start_turn(
        session,
        turn,
        "Write a long, detailed tutorial with many sections and examples.",
        _NoopApprovalHandler(),
        timeout=30,
    )
    interrupt_task = None
    terminal = None
    async with asyncio.timeout(90):
        async for item in stream:
            if (
                interrupt_task is None
                and isinstance(item, StreamEvent)
                and item.kind is StreamEventKind.AGENT_MESSAGE_DELTA
            ):
                interrupt_task = asyncio.create_task(
                    runtime.interrupt_turn(session, turn, timeout=30)
                )
            if isinstance(item, RuntimeEvent) and item.state in {
                RuntimeEventState.COMPLETED,
                RuntimeEventState.FAILED,
                RuntimeEventState.CANCELLED,
                RuntimeEventState.UNKNOWN,
            }:
                terminal = item
    assert interrupt_task is not None
    interrupted = await interrupt_task
    assert interrupted.status is ProviderCallStatus.CONFIRMED
    assert interrupted.value is not None
    assert interrupted.value.state is RuntimeTurnState.CANCELLED
    assert terminal is not None
    assert terminal.state is RuntimeEventState.CANCELLED
    await runtime.stop(timeout=10)


@pytest.mark.asyncio
async def test_real_claude_active_child_exit_is_terminal_unknown() -> None:
    if shutil.which("claude") is None:
        pytest.fail("claude CLI is required for the exit integration test")
    agent_id = f"claude-e2e-{uuid4()}"
    runtime = Runtime(_context(agent_id), model="kimi")
    session = _session(agent_id, str(uuid4()))
    await runtime.start(timeout=30)
    started = await runtime.start_session(session, timeout=30)
    assert started.value is not None
    session = started.value
    turn = _turn(session)
    stream = await runtime.start_turn(
        session,
        turn,
        "Write a long response with many detailed sections.",
        _NoopApprovalHandler(),
        timeout=30,
    )
    connection = runtime._connections[session.id]
    pid = connection.supervisor.pid
    assert pid is not None
    os.kill(pid, signal.SIGKILL)
    items = []
    async with asyncio.timeout(30):
        async for item in stream:
            items.append(item)

    terminals = [
        item
        for item in items
        if isinstance(item, RuntimeEvent)
        and item.state
        in {
            RuntimeEventState.COMPLETED,
            RuntimeEventState.FAILED,
            RuntimeEventState.CANCELLED,
            RuntimeEventState.UNKNOWN,
        }
    ]
    assert len(terminals) == 1
    assert terminals[0].state is RuntimeEventState.UNKNOWN
    assert session.id not in runtime._connections
    await runtime.stop(timeout=10)


@pytest.mark.asyncio
async def test_real_claude_idle_child_exit_rejects_next_turn() -> None:
    if shutil.which("claude") is None:
        pytest.fail("claude CLI is required for the idle-exit integration test")
    agent_id = f"claude-e2e-{uuid4()}"
    runtime = Runtime(_context(agent_id), model="kimi")
    session = _session(agent_id, str(uuid4()))
    await runtime.start(timeout=30)
    started = await runtime.start_session(session, timeout=30)
    assert started.value is not None
    session = started.value
    connection = runtime._connections[session.id]
    pid = connection.supervisor.pid
    assert pid is not None
    os.kill(pid, signal.SIGKILL)
    await connection.supervisor.wait(timeout=10)

    with pytest.raises(RuntimeSessionUnavailable):
        await runtime.start_turn(
            session,
            _turn(session),
            "Reply briefly.",
            _NoopApprovalHandler(),
            timeout=30,
        )
    await runtime.stop(timeout=10)


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
            "Ask a background teammate to run `sleep 20`. Confirm as soon as the "
            "teammate starts, without waiting for the command to finish."
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
                                origin = message.get("origin")
                                if (
                                    message.get("type") == "user"
                                    and isinstance(origin, Mapping)
                                    and origin.get("kind") not in {None, "human"}
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
                    inbox is not None and inbox.adopted_injected_turn
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
                        "injected_turn_active": connection.client.injected_turn_active,
                        "inbox_adopted": (
                            inbox.adopted_injected_turn if inbox is not None else None
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
        model="kimi",
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
