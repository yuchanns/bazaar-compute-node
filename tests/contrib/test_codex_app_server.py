from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

from bazaar_compute_node.contrib.codex_app_server import (
    CodexAppServerClient,
    JsonlProcessSpec,
    JsonlProcessState,
    JsonlProcessSupervisor,
    JsonlRemoteError,
    build_thread_start_params,
)
from bazaar_compute_node.core.instruction import DeveloperInstructionContext


def python_process(script: str, *, cwd: Path | None = None) -> JsonlProcessSpec:
    import sys

    return JsonlProcessSpec(
        executable=sys.executable,
        arguments=("-u", "-c", script),
        cwd=cwd,
    )


def test_build_thread_start_params_maps_rendered_instructions() -> None:
    developer_instructions = "Runtime: runtime-from-caller"
    workspace = Path.cwd()
    params = build_thread_start_params(
        developer_instructions,
        model="gpt-5.6-luna",
        approval_policy="never",
        cwd=workspace,
        ephemeral=True,
    )

    assert params["developerInstructions"] == developer_instructions
    assert params["model"] == "gpt-5.6-luna"
    assert params["approvalPolicy"] == "never"
    assert params["cwd"] == str(workspace)
    assert params["ephemeral"] is True


@pytest.mark.asyncio
async def test_jsonl_supervisor_classifies_invalid_json_and_nonzero_exit(
    tmp_path: Path,
) -> None:
    invalid = JsonlProcessSupervisor(
        python_process(
            """
import sys
print("not-json", flush=True)
""",
            cwd=tmp_path,
        )
    )
    await invalid.start(timeout=2)
    await invalid.wait(timeout=2)
    assert invalid.state is JsonlProcessState.FAILED
    assert invalid.fatal_error is not None
    assert invalid.fatal_error.kind == "protocol_error"
    await invalid.stop(timeout=2)

    exited = JsonlProcessSupervisor(
        python_process(
            """
import sys
sys.exit(7)
""",
            cwd=tmp_path,
        )
    )
    await exited.start(timeout=2)
    assert await exited.wait(timeout=2) == 7
    assert exited.state is JsonlProcessState.FAILED
    assert exited.fatal_error is not None
    assert exited.fatal_error.kind == "process_exited"
    await exited.stop(timeout=2)


@pytest.mark.asyncio
async def test_jsonl_supervisor_timeout_cancellation_and_restart(
    tmp_path: Path,
) -> None:
    supervisor = JsonlProcessSupervisor(
        python_process(
            """
import json
import sys

for line in sys.stdin:
    json.loads(line)
""",
            cwd=tmp_path,
        )
    )
    await supervisor.start(timeout=2)
    with pytest.raises(TimeoutError):
        await supervisor.request("never", timeout=0.05)
    cancelled = asyncio.create_task(supervisor.request("cancel", timeout=2))
    await asyncio.sleep(0)
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    await supervisor.stop(timeout=2)

    await supervisor.start(timeout=2)
    await supervisor.stop(timeout=2)
    assert supervisor.state is JsonlProcessState.STOPPED


@pytest.mark.asyncio
async def test_local_codex_app_server_uses_required_model_and_effort() -> None:
    workspace = Path.cwd()
    codex = shutil.which("codex")
    if codex is None:
        pytest.fail("codex CLI is required for the App Server integration test")
    supervisor = JsonlProcessSupervisor(
        JsonlProcessSpec(
            executable=codex,
            arguments=("app-server", "--stdio"),
            cwd=workspace,
        )
    )
    client = CodexAppServerClient(supervisor)
    await supervisor.start(timeout=10)
    try:
        initialize = await supervisor.request(
            "initialize",
            {"clientInfo": {"name": "bcn-task4a-tests", "version": "0.1.0"}},
            timeout=20,
        )
        assert isinstance(initialize.get("result"), dict)
        await supervisor.notify("initialized", timeout=5)
        model_responses = await asyncio.gather(
            supervisor.request("model/list", {}, timeout=20),
            supervisor.request("model/list", {}, timeout=20),
        )
        model_catalogs: list[list[object]] = []
        for model_response in model_responses:
            model_result = model_response.get("result")
            assert isinstance(model_result, dict)
            models = model_result.get("data")
            assert isinstance(models, list)
            model_catalogs.append(models)
        models = model_catalogs[0]
        luna = next(
            (
                entry
                for entry in models
                if isinstance(entry, dict) and entry.get("id") == "gpt-5.6-luna"
            ),
            None,
        )
        assert isinstance(luna, dict)
        efforts = luna.get("supportedReasoningEfforts")
        assert isinstance(efforts, list)
        assert any(
            isinstance(entry, dict) and entry.get("reasoningEffort") == "max"
            for entry in efforts
        )
        with pytest.raises(JsonlRemoteError) as raised:
            await supervisor.request("method/does-not-exist", {}, timeout=20)
        assert raised.value.kind == "remote_error"
        thread_response = await client.start_thread(
            DeveloperInstructionContext(
                node_id="node-test",
                runtime_session_id="session-test",
                runtime="codex",
                workspace=str(workspace),
            ).render(),
            model="gpt-5.6-luna",
            approval_policy="never",
            cwd=workspace,
            timeout=20,
        )
        result = thread_response.get("result")
        assert isinstance(result, dict)
        assert result.get("model") == "gpt-5.6-luna"
        thread = result.get("thread")
        assert isinstance(thread, dict)
        thread_id = thread.get("id")
        assert isinstance(thread_id, str)
        thread_path = thread.get("path")
        assert isinstance(thread_path, str)
        thread_started = False
        async with asyncio.timeout(10):
            while not thread_started:
                incoming = await supervisor.receive(timeout=5)
                if incoming.get("method") != "thread/started":
                    continue
                params = incoming.get("params")
                if not isinstance(params, dict):
                    continue
                started_thread = params.get("thread")
                thread_started = (
                    isinstance(started_thread, dict)
                    and started_thread.get("id") == thread_id
                )
        assert thread_started
        turn_response = await supervisor.request(
            "turn/start",
            {
                "threadId": thread_id,
                "model": "gpt-5.6-luna",
                "effort": "max",
                "input": [
                    {
                        "type": "text",
                        "text": "Reply with exactly OK and do not use tools.",
                    }
                ],
            },
            timeout=30,
        )
        turn_result = turn_response.get("result")
        assert isinstance(turn_result, dict)
        turn = turn_result.get("turn")
        assert isinstance(turn, dict)
        assert turn.get("status") == "inProgress"
        completed_turn: dict[str, object] | None = None
        async with asyncio.timeout(120):
            while completed_turn is None:
                incoming = await supervisor.receive(timeout=10)
                if incoming.get("method") != "turn/completed":
                    continue
                params = incoming.get("params")
                if not isinstance(params, dict):
                    continue
                candidate = params.get("turn")
                if isinstance(candidate, dict):
                    completed_turn = candidate
        assert completed_turn.get("status") == "completed"
        assert completed_turn.get("error") is None
    finally:
        await supervisor.stop(timeout=10)

    assert supervisor.returncode is not None
    assert not supervisor.is_running
