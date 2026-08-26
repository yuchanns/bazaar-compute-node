from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from bazaar_compute_node.contrib.claude.client import Client
from bazaar_compute_node.contrib.claude.plugin import create_runtime
from bazaar_compute_node.contrib.claude.process import (
    MAX_JSONL_BYTES,
    ProcessSpec,
    ProcessSupervisor,
    build_arguments,
    decode_stdout_line,
)
from bazaar_compute_node.contrib.claude.protocol import ClaudeProtocolError
from bazaar_compute_node.contrib.claude.runtime import _Connection, _observe_background
from bazaar_compute_node.core.models import RuntimeSession
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


def test_claude_stdout_framing_has_one_mib_boundary() -> None:
    prefix = b'{"type":"system","subtype":"init","padding":"'
    suffix = b'"}'
    line = prefix + b"x" * (MAX_JSONL_BYTES - 1 - len(prefix) - len(suffix)) + suffix

    assert decode_stdout_line(line) is not None
    with pytest.raises(ClaudeProtocolError, match="exceeds 1 MiB"):
        decode_stdout_line(line + b"x")


@pytest.mark.asyncio
async def test_claude_control_failure_cleans_pending_request() -> None:
    supervisor = ProcessSupervisor(
        ProcessSpec(Path("claude").as_posix(), (), Path.cwd(), {})
    )
    client = Client(supervisor)

    with pytest.raises(Exception):  # noqa: B017
        await client.initialize(timeout=0.1)

    assert client.pending_control_count == 0


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
