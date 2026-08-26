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


@pytest.mark.asyncio
async def test_claude_client_adopts_an_injected_provider_turn() -> None:
    supervisor = ProcessSupervisor(
        ProcessSpec(Path("claude").as_posix(), (), Path.cwd(), {})
    )
    client = Client(supervisor)
    await client._route_business_message(
        {
            "type": "user",
            "origin": {"kind": "task-notification"},
            "message": {"role": "user", "content": "task completed"},
        },
        1,
    )
    client._message_sequence = 1

    inbox, send_error = await client.open_turn("new channel input", timeout=0.1)

    assert send_error is not None
    assert inbox.adopted_injected_turn
    await client._route_business_message(
        {"type": "assistant", "message": {"content": []}}, 2
    )
    await client._route_business_message(
        {"type": "result", "origin": {"kind": "task-notification"}}, 3
    )
    assert (await inbox.receive())["type"] == "assistant"
    assert (await inbox.receive())["type"] == "result"
    assert not client.injected_turn_active
    await client.close_turn(inbox)
    await client.close()


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
