from __future__ import annotations

import asyncio
import os
import signal
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from bazaar_compute_node.contrib.claude.client import (
    Client,
    ProviderCycleState,
    ProviderCycleStateMachine,
    TurnInbox,
)
from bazaar_compute_node.contrib.claude.events import TurnEventStream
from bazaar_compute_node.contrib.claude.plugin import create_runtime
from bazaar_compute_node.contrib.claude.process import (
    MAX_JSONL_BYTES,
    ProcessSpec,
    ProcessSupervisor,
    build_arguments,
    decode_stdout_line,
)
from bazaar_compute_node.contrib.claude.protocol import (
    ClaudeProcessExited,
    ClaudeProtocolError,
)
from bazaar_compute_node.contrib.claude.runtime import _Connection, _observe_background
from bazaar_compute_node.core.models import (
    RuntimeEvent,
    RuntimeEventState,
    RuntimeSession,
)
from bazaar_compute_node.core.runtime import RuntimeCommandContext, RuntimeSandboxMode


def test_claude_command_uses_sdk_style_streaming_contract() -> None:
    arguments = build_arguments(
        system_prompt="BCN instructions",
        settings='{"sandbox":{"enabled":true}}',
        session_id="session-1",
        model="model-1",
        effort="high",
    )

    assert arguments[:3] == ("--output-format", "stream-json", "--verbose")
    assert "-p" not in arguments
    assert "--session-id=session-1" in arguments
    assert arguments[-2:] == ("--input-format", "stream-json")
    assert arguments[arguments.index("--disallowedTools") + 1] == "AskUserQuestion"


def test_claude_command_can_bypass_permissions() -> None:
    arguments = build_arguments(
        system_prompt="BCN instructions",
        settings='{"sandbox":{"enabled":false}}',
        permission_mode="bypassPermissions",
        session_id="session-1",
    )

    assert arguments[arguments.index("--permission-mode") + 1] == "bypassPermissions"


def test_claude_stdout_framing_has_one_mib_boundary() -> None:
    prefix = b'{"type":"system","subtype":"init","padding":"'
    suffix = b'"}'
    line = prefix + b"x" * (MAX_JSONL_BYTES - 1 - len(prefix) - len(suffix)) + suffix

    assert decode_stdout_line(line) is not None
    with pytest.raises(ClaudeProtocolError, match="exceeds 1 MiB"):
        decode_stdout_line(line + b"x")


@pytest.mark.asyncio
async def test_claude_process_exit_uses_result_error_when_stderr_is_empty(
    tmp_path: Path,
) -> None:
    script = (
        "import sys;"
        'print(\'{"type":"result","subtype":"error_during_execution",'
        '"duration_ms":1,"duration_api_ms":1,"is_error":true,'
        '"num_turns":0,"session_id":"session-1",'
        '"errors":["sandbox dependency missing"]}\',flush=True);'
        "sys.exit(1)"
    )
    supervisor = ProcessSupervisor(
        ProcessSpec(sys.executable, ("-c", script), tmp_path, os.environ)
    )

    await supervisor.start(timeout=5)
    result = await supervisor.receive()

    assert result["type"] == "result"
    with pytest.raises(ClaudeProcessExited, match="sandbox dependency missing"):
        await supervisor.receive()


@pytest.mark.asyncio
async def test_claude_process_exit_drops_error_from_earlier_result(
    tmp_path: Path,
) -> None:
    script = (
        "import sys;"
        'print(\'{"type":"result","subtype":"error_during_execution",'
        '"duration_ms":1,"duration_api_ms":1,"is_error":true,'
        '"num_turns":0,"session_id":"session-1",'
        '"errors":["stale failure"]}\',flush=True);'
        'print(\'{"type":"result","subtype":"success",'
        '"duration_ms":1,"duration_api_ms":1,"is_error":false,'
        '"num_turns":1,"session_id":"session-1","result":"done"}\',flush=True);'
        "sys.exit(1)"
    )
    supervisor = ProcessSupervisor(
        ProcessSpec(sys.executable, ("-c", script), tmp_path, os.environ)
    )

    await supervisor.start(timeout=5)
    assert (await supervisor.receive())["type"] == "result"
    assert (await supervisor.receive())["type"] == "result"

    with pytest.raises(ClaudeProcessExited) as error:
        await supervisor.receive()
    assert "stale failure" not in str(error.value)
    assert error.value.result_error_tail == ()


@pytest.mark.asyncio
async def test_claude_control_failure_cleans_pending_request() -> None:
    supervisor = ProcessSupervisor(
        ProcessSpec(Path("claude").as_posix(), (), Path.cwd(), {})
    )
    client = Client(supervisor)

    with pytest.raises(Exception):  # noqa: B017
        await client.initialize(timeout=0.1)

    assert client.pending_control_count == 0


@pytest.mark.parametrize(
    ("events", "expected_states", "adopted"),
    [
        (
            ("foreground_opened", "foreground_closed"),
            (ProviderCycleState.FOREGROUND, ProviderCycleState.IDLE),
            False,
        ),
        (
            ("provider_wake_started", "provider_wake_finished"),
            (ProviderCycleState.PROVIDER_WAKE, ProviderCycleState.IDLE),
            False,
        ),
        (
            (
                "provider_wake_started",
                "foreground_opened",
                "provider_wake_finished",
                "foreground_closed",
            ),
            (
                ProviderCycleState.PROVIDER_WAKE,
                ProviderCycleState.PROVIDER_WAKE_ADOPTED,
                ProviderCycleState.FOREGROUND,
                ProviderCycleState.IDLE,
            ),
            True,
        ),
        (
            (
                "foreground_opened",
                "provider_wake_started",
                "foreground_closed",
                "provider_wake_finished",
            ),
            (
                ProviderCycleState.FOREGROUND,
                ProviderCycleState.PROVIDER_WAKE_ADOPTED,
                ProviderCycleState.PROVIDER_WAKE,
                ProviderCycleState.IDLE,
            ),
            False,
        ),
    ],
)
def test_claude_provider_cycle_state_machine(
    events: tuple[str, ...],
    expected_states: tuple[ProviderCycleState, ...],
    adopted: bool,
) -> None:
    machine = ProviderCycleStateMachine()
    foreground_adopted = False

    for event, expected_state in zip(events, expected_states, strict=True):
        result = getattr(machine, event)()
        if event == "foreground_opened":
            foreground_adopted = result
        assert machine.state is expected_state

    assert foreground_adopted is adopted


def test_claude_provider_cycle_rejects_second_foreground() -> None:
    machine = ProviderCycleStateMachine()
    machine.foreground_opened()

    with pytest.raises(RuntimeError, match=r"FOREGROUND \+ FOREGROUND_OPENED"):
        machine.foreground_opened()


@pytest.mark.asyncio
async def test_claude_task_notification_adopts_foreground_turn() -> None:
    supervisor = ProcessSupervisor(
        ProcessSpec(Path("claude").as_posix(), (), Path.cwd(), {})
    )
    client = Client(supervisor)
    inbox = TurnInbox(1)
    client._turn_inbox = inbox
    client._provider_cycle.foreground_opened()

    await client._route_business_message(
        {
            "type": "system",
            "subtype": "task_notification",
            "session_id": "session-1",
            "task_id": "task-1",
        },
        1,
    )

    assert client.provider_wake_active
    assert client.provider_cycle_state is ProviderCycleState.PROVIDER_WAKE_ADOPTED
    assert inbox.adopted_provider_wake

    await client._route_business_message(
        {
            "type": "result",
            "subtype": "success",
            "session_id": "session-1",
            "origin": {"kind": "task-notification"},
        },
        2,
    )

    assert not client.provider_wake_active
    assert client.provider_cycle_state is ProviderCycleState.FOREGROUND


@pytest.mark.asyncio
async def test_claude_adopted_task_notification_result_completes_turn() -> None:
    inbox = TurnInbox(1)
    inbox.adopted_provider_wake = True
    closed = False

    async def claim_result(message: dict[str, object]) -> bool:
        del message
        return True

    async def close_turn() -> None:
        nonlocal closed
        closed = True

    async def reject_unusable(error: BaseException) -> None:
        raise AssertionError from error

    stream = TurnEventStream(
        inbox,
        session_id="bcn-session-1",
        turn_id="turn-1",
        provider_thread_id="provider-session-1",
        claude_version=(2, 1, 247),
        claim_result=claim_result,
        on_closed=close_turn,
        on_unusable=reject_unusable,
    )
    started = await anext(stream)
    inbox._messages.put_nowait(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "duration_ms": 1,
            "duration_api_ms": 1,
            "num_turns": 1,
            "session_id": "provider-session-1",
            "origin": {"kind": "task-notification"},
        }
    )
    completed = await anext(stream)

    assert isinstance(started, RuntimeEvent)
    assert started.state is RuntimeEventState.STARTED
    assert isinstance(completed, RuntimeEvent)
    assert completed.state is RuntimeEventState.COMPLETED
    assert closed


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX process signals")
@pytest.mark.asyncio
async def test_claude_parent_exit_is_not_blocked_by_inherited_pipes(
    tmp_path: Path,
) -> None:
    child_pid_path = tmp_path / "child.pid"
    script = (
        "import pathlib,subprocess,sys,time;"
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']);"
        f"pathlib.Path({str(child_pid_path)!r}).write_text(str(child.pid));"
        "time.sleep(30)"
    )
    supervisor = ProcessSupervisor(
        ProcessSpec(
            sys.executable,
            ("-c", script),
            tmp_path,
            os.environ,
        )
    )
    await supervisor.start(timeout=10)
    async with asyncio.timeout(10):
        while not child_pid_path.exists():
            await asyncio.sleep(0.01)
    child_pid = int(child_pid_path.read_text())
    parent_pid = supervisor.pid
    assert parent_pid is not None
    try:
        os.kill(parent_pid, signal.SIGKILL)
        await supervisor.wait(timeout=5)
    finally:
        try:
            os.kill(child_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_claude_runtime_factory_preserves_runtime_options() -> None:
    async def run_command(
        command: str, arguments: Sequence[str], cwd: str | None
    ) -> None:
        del command, arguments, cwd

    def environment_for_session(session: RuntimeSession) -> Mapping[str, str]:
        del session
        return {}

    context = RuntimeCommandContext(
        run_command=run_command,
        environment_for_session=environment_for_session,
        agent_id="agent-1",
        agent_name="Agent One",
        bot_name=lambda: "Bot One",
        runtime_options={"model": "model-1", "effort": "high"},
        sandbox_mode=RuntimeSandboxMode.WORKSPACE_WRITE,
        network_access=False,
    )

    runtime = create_runtime(context)

    assert runtime.name == "claudecode"
    assert runtime.environment_variable_names() == (
        "CLAUDE_CONFIG_DIR",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "CLAUDE_CODE_USE_FOUNDRY",
        "CLAUDE_CODE_USE_MANTLE",
        "SSL_CERT_FILE",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
    )


def test_claude_background_tasks_emit_only_the_idle_edge() -> None:
    supervisor = ProcessSupervisor(
        ProcessSpec(Path("claude").as_posix(), (), Path.cwd(), {})
    )
    connection = _Connection(
        supervisor,
        Client(supervisor),
        Path.cwd(),
        "provider-session-1",
        (2, 1, 239),
    )

    assert not _observe_background(
        connection,
        {
            "type": "system",
            "subtype": "task_started",
            "task_id": "task-1",
            "task_type": "local_agent",
        },
    )
    assert not _observe_background(
        connection,
        {
            "type": "system",
            "subtype": "task_started",
            "task_id": "task-2",
            "task_type": "local_workflow",
        },
    )
    assert not _observe_background(
        connection,
        {
            "type": "system",
            "subtype": "task_notification",
            "task_id": "task-1",
        },
    )
    assert _observe_background(
        connection,
        {
            "type": "system",
            "subtype": "task_updated",
            "task_id": "task-2",
            "patch": {"status": "completed"},
        },
    )
    assert not _observe_background(
        connection,
        {
            "type": "system",
            "subtype": "task_notification",
            "task_id": "task-2",
        },
    )
