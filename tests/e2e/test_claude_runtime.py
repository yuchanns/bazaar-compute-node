from __future__ import annotations

import asyncio
import json
import os
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path
from time import time_ns
from uuid import uuid4

import pytest

from bazaar_compute_node.contrib.claude.client import Client
from bazaar_compute_node.contrib.claude.process import (
    ProcessSpec,
    ProcessState,
    ProcessSupervisor,
    build_arguments,
)
from bazaar_compute_node.contrib.claude.runtime import Runtime
from bazaar_compute_node.core.models import RuntimeSession, SessionRuntimeState
from bazaar_compute_node.core.outcomes import ProviderCallStatus
from bazaar_compute_node.core.paths import resolve_workspace_dir
from bazaar_compute_node.core.runtime import RuntimeCommandContext, RuntimeSandboxMode

pytestmark = pytest.mark.e2e


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
        runtime_options={"model": "glm-5.3"},
        sandbox_mode=RuntimeSandboxMode.DANGER_FULL_ACCESS,
        startup_timeout_seconds=30,
    )


@pytest.mark.asyncio
async def test_real_claude_runtime_starts_reconciles_and_reaps_process() -> None:
    if shutil.which("claude") is None:
        pytest.fail("claude CLI is required for the runtime integration test")
    agent_id = f"claude-e2e-{uuid4()}"
    runtime = Runtime(_context(agent_id), model="glm-5.3")
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
    runtime = Runtime(context, model="glm-5.3")
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
                model="glm-5.3",
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
