from __future__ import annotations

import asyncio
import shutil
from collections.abc import Sequence
from pathlib import Path
from time import time_ns
from uuid import uuid7

import pytest
from bcn_test_support import TestChannel
from test_orchestration import run_natural_conversation_contract

from bazaar_compute_node.contrib.codex_app_server import (
    CodexAppServerClient,
    CodexAppServerRuntime,
    CodexTurnEventStream,
    JsonlProcessSpec,
    JsonlProcessState,
    JsonlProcessSupervisor,
    JsonlRemoteError,
    build_initialize_params,
    build_thread_resume_params,
    build_thread_start_params,
    build_turn_interrupt_params,
    build_turn_start_params,
    parse_error_notification,
    parse_thread_response,
    parse_turn_notification,
    parse_turn_response,
)
from bazaar_compute_node.contrib.codex_app_server.plugin import create_runtime
from bazaar_compute_node.contrib.sqlite import SqliteDatabase
from bazaar_compute_node.core.approval import IApprovalHandler
from bazaar_compute_node.core.client import CLIENT_INFO
from bazaar_compute_node.core.instruction import DeveloperInstructionContext
from bazaar_compute_node.core.models import (
    ApprovalRequest,
    ApprovalResult,
    RuntimeProcessState,
    RuntimeSession,
    RuntimeTurn,
    RuntimeTurnState,
)
from bazaar_compute_node.core.paths import resolve_workspace_dir
from bazaar_compute_node.core.runtime import RuntimeCommandContext

TEST_MODEL = "gpt-5.6-luna"
TEST_EFFORT = "max"


def python_process(script: str, *, cwd: Path | None = None) -> JsonlProcessSpec:
    import sys

    return JsonlProcessSpec(
        executable=sys.executable,
        arguments=("-u", "-c", script),
        cwd=cwd,
    )


class _NoopApprovalHandler(IApprovalHandler):
    async def request_approval(
        self,
        request: ApprovalRequest,
        *,
        timeout: float,
    ) -> ApprovalResult:
        raise AssertionError(f"unexpected approval request: {request.request_id}")


def test_build_thread_start_params_maps_rendered_instructions() -> None:
    developer_instructions = "Runtime: runtime-from-caller"
    workspace = Path.cwd()
    params = build_thread_start_params(
        developer_instructions,
        model=TEST_MODEL,
        approval_policy="never",
        cwd=workspace,
        ephemeral=True,
    )

    assert params["developerInstructions"] == developer_instructions
    assert params["model"] == TEST_MODEL
    assert params["approvalPolicy"] == "never"
    assert params["cwd"] == str(workspace)
    assert params["ephemeral"] is True


def test_codex_protocol_builders_and_parsers_preserve_runtime_contract() -> None:
    assert build_initialize_params(CLIENT_INFO) == {
        "clientInfo": {
            "name": "bcn",
            "version": "0.1.0",
        }
    }
    assert build_thread_start_params("instructions") == {
        "developerInstructions": "instructions",
    }
    assert build_thread_resume_params("thread-1") == {"threadId": "thread-1"}
    assert build_turn_start_params(
        "thread-1",
        "natural follow-up",
        client_user_message_id="message-1",
    ) == {
        "threadId": "thread-1",
        "clientUserMessageId": "message-1",
        "input": [{"type": "text", "text": "natural follow-up"}],
    }
    assert build_turn_start_params(
        "thread-1",
        "natural follow-up",
        model=TEST_MODEL,
        effort=TEST_EFFORT,
    ) == {
        "threadId": "thread-1",
        "model": TEST_MODEL,
        "effort": TEST_EFFORT,
        "input": [{"type": "text", "text": "natural follow-up"}],
    }
    assert build_turn_interrupt_params("thread-1", "turn-1") == {
        "threadId": "thread-1",
        "turnId": "turn-1",
    }
    assert (
        build_thread_start_params("instructions", model="another-model")["model"]
        == "another-model"
    )

    thread = parse_thread_response(
        {
            "result": {
                "thread": {
                    "id": "thread-1",
                    "path": "/home/user/.codex/sessions/thread-1.jsonl",
                }
            }
        }
    )
    assert thread.thread_id == "thread-1"
    turn = parse_turn_response(
        {"result": {"turn": {"id": "turn-1", "status": "inProgress"}}}
    )
    assert turn.turn_id == "turn-1"
    thread_id, completed = parse_turn_notification(
        {
            "method": "turn/completed",
            "params": {
                "threadId": "thread-1",
                "turn": {"id": "turn-1", "status": "completed"},
            },
        }
    )
    assert thread_id == "thread-1"
    assert completed.status == "completed"
    error = parse_error_notification(
        {
            "method": "error",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "willRetry": True,
                "error": {
                    "message": "temporary provider failure",
                    "codexErrorInfo": {"usageLimit": {}},
                },
            },
        }
    )
    assert error.will_retry is True
    assert error.error_type == "usageLimit"


def test_codex_runtime_factory_uses_optional_runtime_configuration() -> None:
    async def run_command(
        _session_id: str,
        _arguments: Sequence[str],
        _body: str | None,
    ) -> None:
        return None

    configured = create_runtime(
        RuntimeCommandContext(
            run_command=run_command,
            runtime_options={"model": TEST_MODEL, "effort": TEST_EFFORT},
        )
    )
    defaulted = create_runtime(RuntimeCommandContext(run_command=run_command))

    assert isinstance(configured, CodexAppServerRuntime)
    assert configured._model == TEST_MODEL
    assert configured._effort == TEST_EFFORT
    assert isinstance(defaulted, CodexAppServerRuntime)
    assert defaulted._model is None
    assert defaulted._effort is None


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


@pytest.mark.real_home
@pytest.mark.asyncio
async def test_local_codex_app_server_uses_required_model_and_effort() -> None:
    codex = shutil.which("codex")
    if codex is None:
        pytest.fail("codex CLI is required for the App Server integration test")

    database = SqliteDatabase()
    await database.start(timeout=10)
    try:
        identity = await database.initialize()
        workspace = resolve_workspace_dir(identity.workspace_id)
        workspace.mkdir(parents=True, exist_ok=True)
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
            initialize = await client.initialize(
                client_info=CLIENT_INFO,
                timeout=20,
            )
            assert isinstance(initialize.get("result"), dict)
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
                    if isinstance(entry, dict) and entry.get("id") == TEST_MODEL
                ),
                None,
            )
            assert isinstance(luna, dict)
            efforts = luna.get("supportedReasoningEfforts")
            assert isinstance(efforts, list)
            assert any(
                isinstance(entry, dict) and entry.get("reasoningEffort") == TEST_EFFORT
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
                model=TEST_MODEL,
                cwd=workspace,
                timeout=20,
            )
            result = thread_response.get("result")
            assert isinstance(result, dict)
            assert result.get("model") == TEST_MODEL
            thread_info = parse_thread_response(thread_response)
            thread_id = thread_info.thread_id
            assert thread_info.path is not None
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
            turn_response = await client.start_turn(
                thread_id,
                "Reply naturally in one short sentence and do not use tools.",
                client_user_message_id="task4b-message-1",
                model=TEST_MODEL,
                effort=TEST_EFFORT,
                timeout=30,
            )
            turn_result = turn_response.get("result")
            assert isinstance(turn_result, dict)
            provider_turn = parse_turn_response(turn_response)
            assert provider_turn.status == "inProgress"
            stream = CodexTurnEventStream(
                supervisor,
                node_id="node-test",
                runtime_slug="codex",
                bcn_session_id="bcn-test",
                agent_runtime_session_id="session-test",
                turn_id="local-turn-1",
                provider_thread_id=thread_id,
                provider_turn_id=provider_turn.turn_id,
            )
            events = []
            async with asyncio.timeout(120):
                async for event in stream:
                    events.append(event)
            assert events[0].state.value == "started"
            assert events[-1].state.value == "completed"
            assert events[-1].metadata["provider_turn_id"] == provider_turn.turn_id
        finally:
            await supervisor.stop(timeout=10)

        assert supervisor.returncode is not None
        assert not supervisor.is_running
    finally:
        await database.stop(timeout=10)


@pytest.mark.real_home
@pytest.mark.asyncio
async def test_local_codex_runtime_maps_follow_up_resume_and_concurrency() -> None:
    codex = shutil.which("codex")
    if codex is None:
        pytest.fail("codex CLI is required for the App Server integration test")

    database = SqliteDatabase()
    await database.start(timeout=10)

    async def unexpected_command(
        bcn_session_id: str,
        arguments: Sequence[str],
        body: str | None,
    ) -> None:
        raise AssertionError(
            f"the no-tool scenario unexpectedly invoked bcc for {bcn_session_id}: "
            f"{arguments!r} {body!r}"
        )

    async def consume_turn(
        runtime: CodexAppServerRuntime,
        session: RuntimeSession,
        label: str,
    ) -> tuple[str, str, tuple[str, ...]]:
        turn = RuntimeTurn(
            turn_id=f"turn-{label}",
            agent_runtime_session_id=session.agent_runtime_session_id,
            state=RuntimeTurnState.STARTING,
            started_at_ms=time_ns() // 1_000_000,
            client_user_message_id=f"message-{label}",
        )
        stream = await runtime.start_turn(
            session,
            turn,
            "Respond naturally in one short sentence and do not use tools. "
            "This is a behavior-level runtime adapter probe.",
            _NoopApprovalHandler(),
            timeout=30,
        )
        events = []
        async with asyncio.timeout(120):
            async for event in stream:
                events.append(event)
        assert events[-1].state.value == "completed"
        provider_turn_ids = {
            str(event.metadata["provider_turn_id"])
            for event in events
            if event.metadata.get("provider_turn_id") is not None
        }
        assert len(provider_turn_ids) == 1
        assert all(event.bcn_session_id == session.bcn_session_id for event in events)
        provider_thread_id = session.provider_thread_id
        assert provider_thread_id is not None
        return (
            provider_thread_id,
            provider_turn_ids.pop(),
            tuple(event.event_name for event in events),
        )

    try:
        identity = await database.initialize()
        now = time_ns() // 1_000_000
        first_session = RuntimeSession(
            agent_runtime_session_id=f"runtime-first-{uuid7()}",
            bcn_session_id=f"bcn-first-{uuid7()}",
            channel_session_id=f"channel-first-{uuid7()}",
            runtime_slug="codex",
            workspace_id=identity.workspace_id,
            process_state=RuntimeProcessState.STARTING,
            created_at_ms=now,
            updated_at_ms=now,
        )
        second_session = RuntimeSession(
            agent_runtime_session_id=f"runtime-second-{uuid7()}",
            bcn_session_id=f"bcn-second-{uuid7()}",
            channel_session_id=f"channel-second-{uuid7()}",
            runtime_slug="codex",
            workspace_id=identity.workspace_id,
            process_state=RuntimeProcessState.STARTING,
            created_at_ms=now,
            updated_at_ms=now,
        )
        context = RuntimeCommandContext(
            run_command=unexpected_command,
            node_id=identity.node_id,
        )
        first_runtime = CodexAppServerRuntime(
            context,
            executable=codex,
            model=TEST_MODEL,
            effort=TEST_EFFORT,
        )
        second_runtime = CodexAppServerRuntime(
            context,
            executable=codex,
            model=TEST_MODEL,
            effort=TEST_EFFORT,
        )
        await asyncio.gather(
            first_runtime.start(timeout=10),
            second_runtime.start(timeout=10),
        )
        try:
            first_result, second_result = await asyncio.gather(
                first_runtime.start_session(first_session, timeout=30),
                second_runtime.start_session(second_session, timeout=30),
            )
            assert first_result.value is not None
            assert second_result.value is not None
            first_running = first_result.value
            second_running = second_result.value
            first_thread, first_turn, first_events = await consume_turn(
                first_runtime,
                first_running,
                "first-initial",
            )
            follow_up_thread, follow_up_turn, _ = await consume_turn(
                first_runtime,
                first_running,
                "first-follow-up",
            )
            assert follow_up_thread == first_thread
            assert follow_up_turn != first_turn

            concurrent = await asyncio.gather(
                consume_turn(second_runtime, second_running, "second-concurrent"),
                consume_turn(first_runtime, first_running, "first-concurrent"),
            )
            assert concurrent[0][0] != concurrent[1][0]
            assert concurrent[0][1] != concurrent[1][1]
            assert first_events[0] == "codex.turn.started"

            await first_runtime.stop(timeout=20)
            unknown = first_running.transition_process_to(
                RuntimeProcessState.UNKNOWN,
                updated_at_ms=time_ns() // 1_000_000,
            )
            reconciling = unknown.transition_process_to(
                RuntimeProcessState.RECONCILING,
                updated_at_ms=time_ns() // 1_000_000,
            )
            resumed_runtime = CodexAppServerRuntime(
                context,
                executable=codex,
                model=TEST_MODEL,
                effort=TEST_EFFORT,
            )
            await resumed_runtime.start(timeout=10)
            try:
                resumed_result = await resumed_runtime.resume_session(
                    reconciling,
                    timeout=30,
                )
                assert resumed_result.value is not None
                resumed = resumed_result.value
                assert resumed.provider_thread_id == first_thread
                resumed_thread, resumed_turn, _ = await consume_turn(
                    resumed_runtime,
                    resumed,
                    "first-resumed",
                )
                assert resumed_thread == first_thread
                assert resumed_turn not in {first_turn, follow_up_turn}
            finally:
                await resumed_runtime.stop(timeout=20)
        finally:
            await second_runtime.stop(timeout=20)
    finally:
        await database.stop(timeout=10)


@pytest.mark.real_home
@pytest.mark.asyncio
async def test_local_codex_runtime_preserves_natural_conversation_session_behavior() -> (
    None
):
    codex = shutil.which("codex")
    if codex is None:
        pytest.fail("codex CLI is required for the App Server integration test")

    await run_natural_conversation_contract(
        channel=TestChannel,
        runtime=lambda context: CodexAppServerRuntime(
            context,
            executable=codex,
            model=TEST_MODEL,
            effort=TEST_EFFORT,
        ),
    )
