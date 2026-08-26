from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from time import time_ns
from uuid import uuid4

import pytest

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
from bazaar_compute_node.core.models import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalResult,
    RuntimeEvent,
    RuntimeEventState,
    RuntimeSession,
    RuntimeTurn,
    RuntimeTurnState,
    SessionRuntimeState,
    StreamEvent,
    StreamEventKind,
)
from bazaar_compute_node.core.outcomes import ProviderCallStatus
from bazaar_compute_node.core.paths import resolve_workspace_dir
from bazaar_compute_node.core.runtime import (
    RuntimeCommandContext,
    RuntimeSandboxMode,
    RuntimeSessionUnavailable,
)

pytestmark = pytest.mark.e2e


class _NoopApprovalHandler(IApprovalHandler):
    async def request_approval(
        self, request: ApprovalRequest, *, timeout: float
    ) -> ApprovalResult:
        del timeout
        raise AssertionError(f"unexpected approval request: {request.request_id}")


class _RecordingApprovalHandler(IApprovalHandler):
    def __init__(self, decision: ApprovalDecision, reason: str | None = None) -> None:
        self.decision = decision
        self.reason = reason
        self.requests: list[ApprovalRequest] = []
        self.requested = asyncio.Event()

    async def request_approval(
        self, request: ApprovalRequest, *, timeout: float
    ) -> ApprovalResult:
        del timeout
        self.requests.append(request)
        self.requested.set()
        return ApprovalResult(
            request_id=request.request_id,
            decision=self.decision,
            decided_at_ms=time_ns() // 1_000_000,
            reason=self.reason,
        )


class _BlockingApprovalHandler(IApprovalHandler):
    def __init__(self) -> None:
        self.requested = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def request_approval(
        self, request: ApprovalRequest, *, timeout: float
    ) -> ApprovalResult:
        del request, timeout
        self.requested.set()
        try:
            await asyncio.Future[None]()
            raise AssertionError("blocking approval completed without cancellation")
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


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
    child_environment = Path(f"/proc/{fresh_pid}/environ").read_bytes().split(b"\0")
    assert not any(item.startswith(b"USER=") for item in child_environment)
    await fresh_connection.client.send_user_message("Reply exactly OK.", timeout=30)
    async with asyncio.timeout(60):
        while (await fresh_connection.client.receive())["type"] != "result":
            pass
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
async def test_real_claude_workspace_sandbox_fails_closed_when_unavailable() -> None:
    if shutil.which("claude") is None:
        pytest.fail("claude CLI is required for the sandbox integration test")
    agent_id = f"claude-e2e-{uuid4()}"
    context = _context(agent_id)
    context = RuntimeCommandContext(
        run_command=context.run_command,
        environment_for_session=context.environment_for_session,
        agent_id=context.agent_id,
        agent_name=context.agent_name,
        bot_name=context.bot_name,
        runtime_options=context.runtime_options,
        network_access=False,
        startup_timeout_seconds=context.startup_timeout_seconds,
    )
    runtime = Runtime(context, model="kimi")
    session = _session(agent_id, str(uuid4()))
    await runtime.start(timeout=30)

    started = await runtime.start_session(session, timeout=30)

    assert started.status is not ProviderCallStatus.CONFIRMED
    assert session.id not in runtime._connections
    await runtime.stop(timeout=10)


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
    await client.send_user_message("Reply exactly OK.", timeout=30)
    messages = []
    async with asyncio.timeout(60):
        while True:
            message = await client.receive()
            messages.append(message)
            if message["type"] == "result":
                break

    assert initialized.get("subtype") == "success"
    assert any(
        message.get("type") == "system" and message.get("subtype") == "init"
        for message in messages
    )
    assert messages[-1].get("session_id") == session_id
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
async def test_real_claude_approved_bash_continues_turn() -> None:
    if shutil.which("claude") is None:
        pytest.fail("claude CLI is required for the approval integration test")
    agent_id = f"claude-e2e-{uuid4()}"
    runtime = Runtime(_context(agent_id), model="kimi")
    session = _session(agent_id, str(uuid4()))
    approval_handler = _RecordingApprovalHandler(ApprovalDecision.APPROVED)
    filename = f"approved-{uuid4()}.txt"
    await runtime.start(timeout=30)
    started = await runtime.start_session(session, timeout=30)
    assert started.value is not None
    session = started.value
    stream = await runtime.start_turn(
        session,
        _turn(session),
        (
            f"Use the Bash tool, and no other tool, to run printf approved > "
            f"{filename}. Then report that the operation finished."
        ),
        approval_handler,
        timeout=30,
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

    assert approval_handler.requests
    assert approval_handler.requests[0].action == "Bash"
    assert approval_handler.requests[0].turn_id is not None
    assert terminal is not None
    assert terminal.state is RuntimeEventState.COMPLETED
    workspace = resolve_workspace_dir(agent_id)
    assert (workspace / filename).read_text() == "approved"
    await runtime.stop(timeout=10)


@pytest.mark.asyncio
async def test_real_claude_rejected_bash_reports_permission_denial() -> None:
    if shutil.which("claude") is None:
        pytest.fail("claude CLI is required for the approval integration test")
    agent_id = f"claude-e2e-{uuid4()}"
    runtime = Runtime(_context(agent_id), model="kimi")
    session = _session(agent_id, str(uuid4()))
    approval_handler = _RecordingApprovalHandler(
        ApprovalDecision.REJECTED, "The workspace owner rejected this operation."
    )
    await runtime.start(timeout=30)
    started = await runtime.start_session(session, timeout=30)
    assert started.value is not None
    session = started.value
    stream = await runtime.start_turn(
        session,
        _turn(session),
        (
            "Use the Bash tool, and no other tool, to create a file named "
            "rejected-operation.txt. If permission is denied, explain that result."
        ),
        approval_handler,
        timeout=30,
    )
    terminal = None
    tool_results: list[str] = []
    async with asyncio.timeout(90):
        async for item in stream:
            if (
                isinstance(item, StreamEvent)
                and item.kind is StreamEventKind.TOOL_PROGRESS
                and item.content is not None
            ):
                tool_results.append(item.content)
            if isinstance(item, RuntimeEvent) and item.state in {
                RuntimeEventState.COMPLETED,
                RuntimeEventState.FAILED,
                RuntimeEventState.CANCELLED,
                RuntimeEventState.UNKNOWN,
            }:
                terminal = item

    assert approval_handler.requests
    assert terminal is not None
    assert terminal.state is RuntimeEventState.COMPLETED
    permission_denials = terminal.metadata.get("permission_denials")
    assert isinstance(permission_denials, list)
    assert permission_denials
    assert any("workspace owner" in result.lower() for result in tool_results)
    await runtime.stop(timeout=10)


@pytest.mark.asyncio
async def test_real_claude_interrupt_cancels_pending_approval() -> None:
    if shutil.which("claude") is None:
        pytest.fail("claude CLI is required for the approval integration test")
    agent_id = f"claude-e2e-{uuid4()}"
    runtime = Runtime(_context(agent_id), model="kimi")
    session = _session(agent_id, str(uuid4()))
    approval_handler = _BlockingApprovalHandler()
    await runtime.start(timeout=30)
    started = await runtime.start_session(session, timeout=30)
    assert started.value is not None
    session = started.value
    turn = _turn(session)
    stream = await runtime.start_turn(
        session,
        turn,
        (
            "Use the Bash tool, and no other tool, to create a file named "
            "interrupt-approval.txt."
        ),
        approval_handler,
        timeout=30,
    )

    async def consume() -> RuntimeEvent | None:
        terminal = None
        async for item in stream:
            if isinstance(item, RuntimeEvent) and item.state in {
                RuntimeEventState.COMPLETED,
                RuntimeEventState.FAILED,
                RuntimeEventState.CANCELLED,
                RuntimeEventState.UNKNOWN,
            }:
                terminal = item
        return terminal

    consumer = asyncio.create_task(consume())
    async with asyncio.timeout(60):
        await approval_handler.requested.wait()
    interrupted = await runtime.interrupt_turn(session, turn, timeout=30)
    async with asyncio.timeout(30):
        terminal = await consumer
        await approval_handler.cancelled.wait()

    assert interrupted.status is ProviderCallStatus.CONFIRMED
    assert interrupted.value is not None
    assert interrupted.value.state is RuntimeTurnState.CANCELLED
    assert terminal is not None
    assert terminal.state is RuntimeEventState.CANCELLED
    await runtime.stop(timeout=10)


@pytest.mark.asyncio
async def test_real_claude_approval_timeout_returns_visible_denial() -> None:
    if shutil.which("claude") is None:
        pytest.fail("claude CLI is required for the approval integration test")
    agent_id = f"claude-e2e-{uuid4()}"
    runtime = Runtime(_context(agent_id), model="kimi")
    session = _session(agent_id, str(uuid4()))
    approval_handler = _BlockingApprovalHandler()
    await runtime.start(timeout=30)
    started = await runtime.start_session(session, timeout=30)
    assert started.value is not None
    session = started.value
    stream = await runtime.start_turn(
        session,
        _turn(session),
        (
            "Use the Bash tool, and no other tool, to create a file named "
            "timed-out-approval.txt. If permission is denied, explain that result."
        ),
        approval_handler,
        timeout=2,
    )
    terminal = None
    tool_results: list[str] = []
    async with asyncio.timeout(90):
        async for item in stream:
            if (
                isinstance(item, StreamEvent)
                and item.kind is StreamEventKind.TOOL_PROGRESS
                and item.content is not None
            ):
                tool_results.append(item.content)
            if isinstance(item, RuntimeEvent) and item.state in {
                RuntimeEventState.COMPLETED,
                RuntimeEventState.FAILED,
                RuntimeEventState.CANCELLED,
                RuntimeEventState.UNKNOWN,
            }:
                terminal = item

    assert approval_handler.requested.is_set()
    assert approval_handler.cancelled.is_set()
    assert terminal is not None
    assert terminal.state is RuntimeEventState.COMPLETED
    assert any("timed out" in result.lower() for result in tool_results)
    await runtime.stop(timeout=10)


@pytest.mark.asyncio
async def test_real_claude_session_shutdown_cancels_pending_approval() -> None:
    if shutil.which("claude") is None:
        pytest.fail("claude CLI is required for the approval integration test")
    agent_id = f"claude-e2e-{uuid4()}"
    runtime = Runtime(_context(agent_id), model="kimi")
    session = _session(agent_id, str(uuid4()))
    approval_handler = _BlockingApprovalHandler()
    await runtime.start(timeout=30)
    started = await runtime.start_session(session, timeout=30)
    assert started.value is not None
    session = started.value
    stream = await runtime.start_turn(
        session,
        _turn(session),
        (
            "Use the Bash tool, and no other tool, to create a file named "
            "shutdown-approval.txt."
        ),
        approval_handler,
        timeout=30,
    )

    async def consume() -> RuntimeEvent | None:
        terminal = None
        async for item in stream:
            if isinstance(item, RuntimeEvent) and item.state in {
                RuntimeEventState.COMPLETED,
                RuntimeEventState.FAILED,
                RuntimeEventState.CANCELLED,
                RuntimeEventState.UNKNOWN,
            }:
                terminal = item
        return terminal

    consumer = asyncio.create_task(consume())
    async with asyncio.timeout(60):
        await approval_handler.requested.wait()
    stopped = await runtime.stop_session(session, timeout=10)
    async with asyncio.timeout(30):
        terminal = await consumer
        await approval_handler.cancelled.wait()

    assert stopped.status is ProviderCallStatus.CONFIRMED
    assert terminal is not None
    assert terminal.state is RuntimeEventState.UNKNOWN
    await runtime.stop(timeout=10)


@pytest.mark.asyncio
async def test_real_claude_resumed_session_approves_next_turn() -> None:
    if shutil.which("claude") is None:
        pytest.fail("claude CLI is required for the approval integration test")
    agent_id = f"claude-e2e-{uuid4()}"
    runtime = Runtime(_context(agent_id), model="kimi")
    session = _session(agent_id, str(uuid4()))
    await runtime.start(timeout=30)
    started = await runtime.start_session(session, timeout=30)
    assert started.value is not None
    session = started.value
    first_stream = await runtime.start_turn(
        session,
        _turn(session),
        "Reply briefly without using tools.",
        _NoopApprovalHandler(),
        timeout=30,
    )
    async with asyncio.timeout(60):
        async for _ in first_stream:
            pass
    await runtime.stop_session(session, timeout=10)
    reconciled = await runtime.reconcile_session(session, None, None, timeout=30)
    assert reconciled.status is ProviderCallStatus.CONFIRMED

    approval_handler = _RecordingApprovalHandler(ApprovalDecision.APPROVED)
    filename = f"resumed-approved-{uuid4()}.txt"
    second_stream = await runtime.start_turn(
        session,
        _turn(session),
        (
            f"Use the Bash tool, and no other tool, to run printf resumed > "
            f"{filename}. Then report that the operation finished."
        ),
        approval_handler,
        timeout=30,
    )
    terminal = None
    async with asyncio.timeout(90):
        async for item in second_stream:
            if isinstance(item, RuntimeEvent) and item.state in {
                RuntimeEventState.COMPLETED,
                RuntimeEventState.FAILED,
                RuntimeEventState.CANCELLED,
                RuntimeEventState.UNKNOWN,
            }:
                terminal = item

    assert approval_handler.requests
    assert terminal is not None
    assert terminal.state is RuntimeEventState.COMPLETED
    assert (resolve_workspace_dir(agent_id) / filename).read_text() == "resumed"
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
async def test_real_claude_delegated_background_task_lifecycle() -> None:
    if shutil.which("claude") is None:
        pytest.fail("claude CLI is required for the background-task integration test")
    agent_id = f"claude-e2e-{uuid4()}"
    runtime = Runtime(_context(agent_id), model="kimi")
    session = _session(agent_id, str(uuid4()))
    await runtime.start(timeout=30)
    started = await runtime.start_session(session, timeout=30)
    assert started.value is not None
    session = started.value
    stream = await runtime.start_turn(
        session,
        _turn(session),
        (
            "Use the Task tool to start one local agent in the background to "
            "calculate 17 multiplied by 19. Wait for it to finish, then summarize."
        ),
        _NoopApprovalHandler(),
        timeout=30,
    )
    observed_background = False
    terminal = None
    async with asyncio.timeout(120):
        async for item in stream:
            observed_background = (
                observed_background
                or await runtime.has_background_job(session, timeout=1)
            )
            if isinstance(item, RuntimeEvent) and item.state in {
                RuntimeEventState.COMPLETED,
                RuntimeEventState.FAILED,
                RuntimeEventState.CANCELLED,
                RuntimeEventState.UNKNOWN,
            }:
                terminal = item

    assert observed_background
    assert terminal is not None
    assert terminal.state is RuntimeEventState.COMPLETED
    async with asyncio.timeout(30):
        while await runtime.has_background_job(session, timeout=1):
            await asyncio.sleep(0.05)
    assert not await runtime.has_background_job(session, timeout=1)
    await runtime.stop(timeout=10)


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

    stream = TurnEventStream(
        client,
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
    )
    await client.send_user_message(
        (
            "Use the Task tool exactly once to ask a local agent to calculate "
            "23 multiplied by 29, then use its result in your answer."
        ),
        timeout=30,
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
    await supervisor.stop(timeout=10)
    await client.close()
