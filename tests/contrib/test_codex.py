from __future__ import annotations

import asyncio
import os
import shutil
import warnings
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path
from time import time_ns
from uuid import NAMESPACE_URL, uuid5, uuid7

import pytest
from bcn_test_support import RecordingAudit, StaticChannelBuilder, TestChannel
from test_orchestration import (
    _wait_for_audit_event,
    _wait_for_inbound_messages,
    make_message,
    run_natural_conversation_contract,
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
from bazaar_compute_node.contrib.codex import (
    Client,
    JsonlProcessSpec,
    JsonlProcessState,
    JsonlProcessSupervisor,
    JsonlProtocolError,
    JsonlRemoteError,
    Runtime,
    TurnEventStream,
    build_fs_watch_params,
    build_initialize_params,
    build_thread_resume_params,
    build_thread_start_params,
    build_turn_interrupt_params,
    build_turn_start_params,
    build_turn_steer_params,
    parse_background_terminals_response,
    parse_error_notification,
    parse_fs_changed_notification,
    parse_fs_watch_response,
    parse_initialize_response,
    parse_skills_changed_notification,
    parse_thread_response,
    parse_turn_notification,
    parse_turn_response,
    parse_turn_steer_response,
)
from bazaar_compute_node.contrib.codex import runtime as runtime_module
from bazaar_compute_node.contrib.codex.plugin import create_runtime
from bazaar_compute_node.contrib.sqlite import SqliteDatabase
from bazaar_compute_node.core.approval import IApprovalHandler
from bazaar_compute_node.core.channel import IChannel
from bazaar_compute_node.core.client import CLIENT_INFO
from bazaar_compute_node.core.instruction import DeveloperInstructionContext
from bazaar_compute_node.core.lifecycle import TimeoutBudget
from bazaar_compute_node.core.models import (
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
    RuntimeExpire,
    RuntimeSandboxMode,
    RuntimeSessionUnavailable,
)

TEST_MODEL = "gpt-5.6-luna"
TEST_EFFORT = "max"


class _StaticRegistry(AdapterRegistry):
    def __init__(
        self,
        *,
        channel: IChannel,
        runtime: Callable[[RuntimeCommandContext], Runtime],
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
        del timeout
        raise AssertionError(f"unexpected approval request: {request.request_id}")


def test_codex_turn_stream_normalizes_transient_updates() -> None:
    stream = TurnEventStream(
        JsonlProcessSupervisor(JsonlProcessSpec(executable="unused")),
        session_id="bcn-1",
        runtime_session_id="runtime-1",
        turn_id="turn-1",
        provider_thread_id="thread-1",
        provider_turn_id="provider-turn-1",
    )

    reasoning = stream._map_message(
        {
            "method": "item/reasoning/summaryTextDelta",
            "params": {
                "threadId": "thread-1",
                "turnId": "provider-turn-1",
                "itemId": "reasoning-1",
                "delta": "",
                "summaryIndex": 2,
            },
        }
    )
    assert isinstance(reasoning, StreamEvent)
    assert reasoning == StreamEvent(
        kind=StreamEventKind.REASONING_SUMMARY_DELTA,
        created_at_ms=reasoning.created_at_ms,
        session_id="bcn-1",
        stream_id="reasoning-1",
        content="",
    )

    summary_boundary = stream._map_message(
        {
            "method": "item/reasoning/summaryPartAdded",
            "params": {
                "threadId": "thread-1",
                "turnId": "provider-turn-1",
                "itemId": "reasoning-1",
            },
        }
    )
    assert summary_boundary is None

    lifecycle = stream._map_message(
        {
            "method": "item/completed",
            "params": {
                "threadId": "thread-1",
                "turnId": "provider-turn-1",
                "item": {"id": "reasoning-1", "type": "reasoning"},
            },
        }
    )
    assert lifecycle is None

    future_progress = stream._map_message(
        {
            "method": "item/future/progress",
            "params": {
                "threadId": "thread-1",
                "turnId": "provider-turn-1",
                "itemId": "future-1",
                "delta": "working",
            },
        }
    )
    assert isinstance(future_progress, StreamEvent)
    assert future_progress.kind is StreamEventKind.ITEM_PROGRESS
    assert future_progress.content == "working"


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
    watched_path = Path.cwd() / "AGENTS.md"
    assert build_initialize_params(CLIENT_INFO) == {
        "clientInfo": {
            "name": "bcn",
            "version": CLIENT_INFO.version,
        },
        "capabilities": {"experimentalApi": True},
    }
    assert build_fs_watch_params(watched_path, "agents-workspace") == {
        "watchId": "agents-workspace",
        "path": str(watched_path),
    }
    assert build_thread_start_params("instructions") == {
        "developerInstructions": "instructions",
    }
    assert build_thread_resume_params("thread-1") == {
        "threadId": "thread-1",
        "excludeTurns": True,
    }
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
        cwd=Path("/workspace"),
        sandbox_policy={
            "type": "workspaceWrite",
            "writableRoots": ["/workspace"],
            "networkAccess": True,
        },
    ) == {
        "threadId": "thread-1",
        "model": TEST_MODEL,
        "effort": TEST_EFFORT,
        "cwd": str(Path("/workspace")),
        "sandboxPolicy": {
            "type": "workspaceWrite",
            "writableRoots": ["/workspace"],
            "networkAccess": True,
        },
        "input": [{"type": "text", "text": "natural follow-up"}],
    }
    assert build_turn_interrupt_params("thread-1", "turn-1") == {
        "threadId": "thread-1",
        "turnId": "turn-1",
    }
    assert build_turn_steer_params(
        "thread-1",
        "turn-1",
        "[inbox notice session=bcn-1]\nInbox update: 1 unread message(s).",
    ) == {
        "threadId": "thread-1",
        "expectedTurnId": "turn-1",
        "input": [
            {
                "type": "text",
                "text": (
                    "[inbox notice session=bcn-1]\nInbox update: 1 unread message(s)."
                ),
            }
        ],
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
    active_thread = parse_thread_response(
        {
            "result": {
                "thread": {
                    "id": "thread-1",
                    "status": {"type": "active"},
                    "turns": [
                        {"id": "turn-1", "status": "inProgress"},
                    ],
                }
            }
        }
    )
    assert active_thread.status == "active"
    assert active_thread.turns[0].turn_id == "turn-1"
    assert active_thread.turns[0].status == "inProgress"
    turn = parse_turn_response(
        {"result": {"turn": {"id": "turn-1", "status": "inProgress"}}}
    )
    assert turn.turn_id == "turn-1"
    assert parse_turn_steer_response({"result": {"turnId": "turn-1"}}) == "turn-1"
    assert parse_background_terminals_response({"result": {"data": []}}) is False
    assert (
        parse_background_terminals_response(
            {"result": {"data": [{"processId": "job-1"}]}}
        )
        is True
    )
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
    codex_home = Path.cwd() / ".codex"
    assert (
        parse_initialize_response({"result": {"codexHome": str(codex_home)}})
        == codex_home
    )
    assert (
        parse_fs_watch_response({"result": {"path": str(watched_path)}}) == watched_path
    )
    parse_skills_changed_notification({"method": "skills/changed", "params": {}})
    fs_change = parse_fs_changed_notification(
        {
            "method": "fs/changed",
            "params": {
                "watchId": "agents-workspace",
                "changedPaths": [str(watched_path)],
            },
        }
    )
    assert fs_change.watch_id == "agents-workspace"
    assert fs_change.changed_paths == (watched_path,)


def test_codex_runtime_factory_uses_optional_runtime_configuration() -> None:
    async def run_command(*_: object) -> None:
        return None

    def environment(_: RuntimeSession) -> dict[str, str]:
        return {}

    configured = create_runtime(
        RuntimeCommandContext(
            run_command=run_command,
            environment_for_session=environment,
            agent_name="Test Agent",
            bot_name=lambda: "provider_bot",
            agent_id="agent-test",
            runtime_options={"model": TEST_MODEL, "effort": TEST_EFFORT},
            sandbox_mode=RuntimeSandboxMode.DANGER_FULL_ACCESS,
            network_access=False,
        )
    )
    defaulted = create_runtime(
        RuntimeCommandContext(
            run_command=run_command,
            environment_for_session=environment,
            agent_name="Test Agent",
            bot_name=lambda: "provider_bot",
            agent_id="agent-test",
        )
    )

    assert isinstance(configured, Runtime)
    assert configured._model == TEST_MODEL
    assert configured._effort == TEST_EFFORT
    assert configured._context.sandbox_mode is RuntimeSandboxMode.DANGER_FULL_ACCESS
    assert configured._context.network_access is False
    assert configured.environment_variable_names() == (
        "CODEX_HOME",
        "CODEX_SQLITE_HOME",
        "CODEX_CA_CERTIFICATE",
        "SSL_CERT_FILE",
    )
    assert isinstance(defaulted, Runtime)
    assert defaulted._model is None
    assert defaulted._effort is None
    assert defaulted._context.sandbox_mode is RuntimeSandboxMode.WORKSPACE_WRITE
    assert defaulted._context.network_access is True


@pytest.mark.asyncio
async def test_codex_runtime_reports_missing_connection_before_turn_start() -> None:
    async def run_command(*_: object) -> None:
        return None

    runtime = Runtime(
        RuntimeCommandContext(
            run_command=run_command,
            environment_for_session=lambda _: {},
            agent_name="Test Agent",
            bot_name=lambda: "provider_bot",
            agent_id="agent-test",
        )
    )
    now_ms = time_ns() // 1_000_000
    session = RuntimeSession(
        id="runtime-missing-connection",
        bcn_session_id="bcn-missing-connection",
        channel_session_id="channel-missing-connection",
        runtime="codex",
        workspace_id="workspace-missing-connection",
        provider_thread_id="thread-missing-connection",
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
    )
    turn = RuntimeTurn(
        turn_id="turn-missing-connection",
        session_id=session.id,
        state=RuntimeTurnState.STARTING,
        started_at_ms=now_ms,
        client_user_message_id="message-missing-connection",
    )

    await runtime.start(timeout=1)
    try:
        with pytest.raises(RuntimeSessionUnavailable):
            await runtime.start_turn(
                session,
                turn,
                "Please summarize the latest project update.",
                _NoopApprovalHandler(),
                timeout=1,
            )
    finally:
        await runtime.stop(timeout=1)


@pytest.mark.asyncio
async def test_codex_runtime_declines_steer_without_active_binding() -> None:
    async def run_command(*_: object) -> None:
        return None

    runtime = Runtime(
        RuntimeCommandContext(
            run_command=run_command,
            environment_for_session=lambda _: {},
            agent_name="Test Agent",
            bot_name=lambda: "provider_bot",
            agent_id="agent-test",
        )
    )
    now_ms = time_ns() // 1_000_000
    session = RuntimeSession(
        id="runtime-without-active-turn",
        bcn_session_id="bcn-without-active-turn",
        channel_session_id="channel-without-active-turn",
        runtime="codex",
        workspace_id="workspace-without-active-turn",
        provider_thread_id="thread-without-active-turn",
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
    )
    turn = RuntimeTurn(
        turn_id="turn-without-active-turn",
        session_id=session.id,
        state=RuntimeTurnState.RUNNING,
        started_at_ms=now_ms,
        provider_turn_id="provider-turn-without-active-turn",
        client_user_message_id="message-without-active-turn",
    )

    await runtime.start(timeout=1)
    try:
        assert not await runtime.steer_turn(
            session,
            turn,
            "[inbox notice session=bcn-without-active-turn]\n"
            "Inbox update: 1 unread message(s).",
            timeout=1,
        )
    finally:
        await runtime.stop(timeout=1)


@pytest.mark.asyncio
async def test_codex_runtime_stops_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def stop(_: JsonlProcessSupervisor, *, timeout: float) -> None:
        assert timeout == 60
        stop_started.set()
        await release_stop.wait()

    async def run_command(*_: object) -> None:
        return None

    stop_started = asyncio.Event()
    release_stop = asyncio.Event()
    monkeypatch.setattr(JsonlProcessSupervisor, "stop", stop)
    runtime = Runtime(
        RuntimeCommandContext(
            run_command=run_command,
            environment_for_session=lambda _: {},
            agent_name="Test Agent",
            bot_name=lambda: "provider_bot",
            agent_id="agent-test",
        )
    )
    now_ms = time_ns() // 1_000_000
    session = RuntimeSession(
        id="runtime-queued-stop",
        bcn_session_id="bcn-queued-stop",
        channel_session_id="channel-queued-stop",
        runtime="codex",
        workspace_id="workspace-queued-stop",
        provider_thread_id="thread-queued-stop",
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
    )
    supervisor = JsonlProcessSupervisor(JsonlProcessSpec(executable="unused"))
    runtime._connections[session.id] = runtime_module._Connection(
        supervisor=supervisor,
        client=Client(supervisor),
        workspace=Path.cwd(),
        provider_thread_id=session.provider_thread_id or "",
    )

    stop_task = asyncio.create_task(runtime.stop_session(session, timeout=60))
    try:
        await asyncio.wait_for(stop_started.wait(), timeout=1)
        assert not stop_task.done()
        assert session.id not in runtime._connections

        release_stop.set()
        result = await asyncio.wait_for(stop_task, timeout=1)
        assert result.status is ProviderCallStatus.CONFIRMED
        assert result.value == session
        assert result.receipt == {}

        repeated = await runtime.stop_session(session, timeout=60)
        assert repeated.status is ProviderCallStatus.CONFIRMED
        assert repeated.value == session
    finally:
        release_stop.set()
        if not stop_task.done():
            await asyncio.wait_for(stop_task, timeout=1)


@pytest.mark.asyncio
async def test_windows_codex_runtime_assumes_background_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run_command(*_: object) -> None:
        return None

    monkeypatch.setattr(runtime_module.os, "name", "nt")
    runtime = Runtime(
        RuntimeCommandContext(
            run_command=run_command,
            environment_for_session=lambda _: {},
            agent_name="Test Agent",
            bot_name=lambda: "provider_bot",
            agent_id="agent-test",
        )
    )
    now_ms = time_ns() // 1_000_000
    session = RuntimeSession(
        id="runtime-windows-background-job",
        bcn_session_id="bcn-windows-background-job",
        channel_session_id="channel-windows-background-job",
        runtime="codex",
        workspace_id="workspace-windows-background-job",
        provider_thread_id=None,
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
    )

    assert await runtime.has_background_job(session, timeout=3)


@pytest.mark.asyncio
async def test_codex_runtime_reports_background_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime_module.os, "name", "posix")

    async def list_background_terminals(
        _: Client,
        thread_id: str,
        *,
        timeout: float,
    ) -> dict[str, object]:
        assert thread_id == "thread-background-job"
        assert timeout == 3
        return {"result": {"data": [{"processId": "job-1"}]}}

    async def run_command(*_: object) -> None:
        return None

    monkeypatch.setattr(Client, "list_background_terminals", list_background_terminals)
    runtime = Runtime(
        RuntimeCommandContext(
            run_command=run_command,
            environment_for_session=lambda _: {},
            agent_name="Test Agent",
            bot_name=lambda: "provider_bot",
            agent_id="agent-test",
        )
    )
    now_ms = time_ns() // 1_000_000
    session = RuntimeSession(
        id="runtime-background-job",
        bcn_session_id="bcn-background-job",
        channel_session_id="channel-background-job",
        runtime="codex",
        workspace_id="workspace-background-job",
        provider_thread_id="thread-background-job",
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
    )
    supervisor = JsonlProcessSupervisor(JsonlProcessSpec(executable="unused"))
    runtime._connections[session.id] = runtime_module._Connection(
        supervisor=supervisor,
        client=Client(supervisor),
        workspace=Path.cwd(),
        provider_thread_id=session.provider_thread_id or "",
    )

    assert await runtime.has_background_job(session, timeout=3)


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
async def test_jsonl_supervisor_routes_only_consumed_notifications() -> None:
    routed: list[dict[str, object]] = []

    def route_notification(message: dict[str, object]) -> bool:
        routed.append(message)
        return message.get("method") == "context/changed"

    supervisor = JsonlProcessSupervisor(
        JsonlProcessSpec(executable="unused"),
        notification_router=route_notification,
    )
    consumed = {"method": "context/changed", "params": {}}
    retained = {"method": "turn/completed", "params": {"turnId": "turn-1"}}
    provider_request = {
        "id": "request-1",
        "method": "item/commandExecution/requestApproval",
        "params": {},
    }

    supervisor._route_message(consumed)
    supervisor._route_message(retained)
    supervisor._route_message(provider_request)

    assert routed == [consumed, retained]
    assert await supervisor.receive(timeout=0.1) == retained
    assert await supervisor.receive(timeout=0.1) == provider_request


@pytest.mark.asyncio
async def test_jsonl_supervisor_keeps_responses_out_of_notification_router() -> None:
    def reject_notification(_: dict[str, object]) -> bool:
        raise AssertionError("response reached the notification router")

    supervisor = JsonlProcessSupervisor(
        JsonlProcessSpec(executable="unused"),
        notification_router=reject_notification,
    )
    response = {"id": 7, "result": {}}

    supervisor._route_message(response)

    assert await supervisor.receive(timeout=0.1) == response


@pytest.mark.asyncio
async def test_jsonl_supervisor_fails_pending_requests_when_router_raises() -> None:
    def reject_notification(_: dict[str, object]) -> bool:
        raise ValueError("invalid notification")

    supervisor = JsonlProcessSupervisor(
        JsonlProcessSpec(executable="unused"),
        notification_router=reject_notification,
    )
    pending = asyncio.get_running_loop().create_future()
    supervisor._pending[1] = pending

    supervisor._route_message({"method": "context/changed", "params": {}})

    assert isinstance(supervisor.fatal_error, JsonlProtocolError)
    with pytest.raises(JsonlProtocolError, match="notification router failed"):
        await pending


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_local_codex_uses_required_model_and_effort() -> None:
    codex = shutil.which("codex")
    if codex is None:
        pytest.fail("codex CLI is required for the App Server integration test")

    agent_id = str(uuid7())
    workspace = resolve_workspace_dir(agent_id)
    workspace.mkdir(parents=True, exist_ok=True)
    supervisor = JsonlProcessSupervisor(
        JsonlProcessSpec(
            executable=codex,
            arguments=("app-server", "--stdio"),
            cwd=workspace,
        )
    )
    client = Client(supervisor)
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
                agent_name="Test Agent",
                bot_name="provider_bot",
                agent_id="agent-test",
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
            "Run `sleep 5`, then reply with one short sentence.",
            client_user_message_id="task4b-message-1",
            model=TEST_MODEL,
            effort=TEST_EFFORT,
            timeout=30,
        )
        turn_result = turn_response.get("result")
        assert isinstance(turn_result, dict)
        provider_turn = parse_turn_response(turn_response)
        assert provider_turn.status == "inProgress"
        command_started = False
        async with asyncio.timeout(30):
            while not command_started:
                incoming = await supervisor.receive(timeout=10)
                method = incoming.get("method")
                params = incoming.get("params")
                if not isinstance(params, dict):
                    continue
                if params.get("turnId") != provider_turn.turn_id:
                    continue
                command_started = method in {
                    "item/commandExecution/started",
                    "item/started",
                } and (
                    method == "item/commandExecution/started"
                    or (
                        isinstance(params.get("item"), dict)
                        and params["item"].get("type") == "commandExecution"
                    )
                )
        assert command_started
        steer_response = await client.steer_turn(
            thread_id,
            provider_turn.turn_id,
            "A teammate just confirmed that the inbox update is expected. "
            "Please acknowledge that in the same brief reply.",
            timeout=30,
        )
        assert parse_turn_steer_response(steer_response) == provider_turn.turn_id
        stream = TurnEventStream(
            supervisor,
            session_id="bcn-test",
            runtime_session_id="session-test",
            turn_id="local-turn-1",
            provider_thread_id=thread_id,
            provider_turn_id=provider_turn.turn_id,
            approval_handler=_NoopApprovalHandler(),
        )
        events = []
        async with asyncio.timeout(120):
            async for event in stream:
                events.append(event)
        durable_events = [event for event in events if isinstance(event, RuntimeEvent)]
        assert durable_events[0].state.value == "started"
        assert durable_events[-1].state.value == "completed"
        assert durable_events[-1].metadata["provider_turn_id"] == provider_turn.turn_id
    finally:
        await supervisor.stop(timeout=10)

    assert supervisor.returncode is not None
    assert not supervisor.is_running


@pytest.mark.e2e
@pytest.mark.skipif(
    os.name == "nt",
    reason="Windows Codex cannot observe background tasks",
)
@pytest.mark.asyncio
async def test_local_codex_core_teardown_reaps_background_terminal(
    system_temp_dir: Path,
) -> None:
    codex = shutil.which("codex")
    if codex is None:
        pytest.fail("codex CLI is required for the App Server integration test")

    agent_id = str(uuid7())
    agent_name = "Codex Teardown Test Agent"
    storage = SqliteDatabase()
    channel = TestChannel()
    audit = RecordingAudit()
    node = NodeApplication(
        configuration=NodeConfiguration(
            storage="sqlite",
            audit="test",
            agents=(
                AgentConfiguration(
                    id=agent_id,
                    name=agent_name,
                    channel=ChannelConfiguration(kind="test"),
                    runtime=RuntimeConfiguration(
                        kind="codex",
                        model=TEST_MODEL,
                        effort=TEST_EFFORT,
                    ),
                ),
            ),
        ),
        shared_factories=SharedAdapterFactories(
            storage=lambda: storage,
            audit=lambda: audit,
        ),
        registry=_StaticRegistry(
            channel=channel,
            runtime=lambda context: Runtime(
                context,
                executable=codex,
                model=TEST_MODEL,
                effort=TEST_EFFORT,
            ),
        ),
        endpoint_path=system_temp_dir / "codex-teardown.sock",
        timeout_budget=TimeoutBudget(
            startup_seconds=30,
            provider_call_seconds=30,
            command_seconds=30,
            shutdown_seconds=30,
        ),
    )
    session_id = f"codex-teardown-{uuid7()}"
    scoped_session_id = str(
        uuid5(
            NAMESPACE_URL,
            f"bcn:{agent_id}:bcn-session:{session_id}",
        )
    )
    first = make_message(
        session_id=session_id,
        body=(
            "I need to verify that a long-running local task can continue after you "
            "respond. Start `sleep 60` as a background task, then tell me it is "
            "running without waiting for completion."
        ),
    )
    try:
        await node.start()
        await channel.inject(first)
        storage_scope = storage.scope(agent_id, agent_name)
        persisted = await _wait_for_inbound_messages(
            storage_scope,
            scoped_session_id,
            1,
        )
        turn_id = f"turn-{persisted[0].message_id}"
        try:
            async with asyncio.timeout(120):
                while not any(
                    isinstance(event, RuntimeEvent)
                    and event.event_name == "runtime.turn.completed"
                    and event.state is RuntimeEventState.COMPLETED
                    and event.turn_id == turn_id
                    for event in channel.events
                ):
                    await asyncio.sleep(0.05)
        except TimeoutError:
            warnings.warn(
                "Skipping Codex teardown assertions because the provider did not "
                "complete the turn while the long-running task was active.",
                RuntimeWarning,
                stacklevel=2,
            )
            pytest.skip(
                "Codex provider did not complete a turn with a live background task"
            )

        agent = node.agents[agent_id]
        assert isinstance(agent.runtime, Runtime)
        runtime = agent.runtime
        runtime_session = agent.orchestrator.runtime_session(scoped_session_id)
        assert runtime_session is not None
        if not await runtime.has_background_job(runtime_session, timeout=30):
            warnings.warn(
                "Skipping Codex teardown assertions because turn completion was not "
                "accompanied by a live background task.",
                RuntimeWarning,
                stacklevel=2,
            )
            pytest.skip(
                "Codex provider completed without exposing a live background task"
            )

        connection = runtime._connections.get(runtime_session.id)
        assert connection is not None
        assert connection.supervisor.pid is not None
        assert connection.supervisor.is_running

        await agent.orchestrator._stop_runtime_session(runtime_session, timeout=30)
        assert agent.orchestrator.runtime_session(scoped_session_id) is None
        async with asyncio.timeout(30):
            while agent.orchestrator._runtime_teardown_tasks:
                await asyncio.sleep(0.1)
        assert not connection.supervisor.is_running
        assert connection.supervisor.returncode is not None

        second = make_message(
            session_id=session_id,
            seq=2,
            body="Reply with exactly session replacement confirmed. Do not use tools.",
        )
        await channel.inject(second)
        persisted = await _wait_for_inbound_messages(
            storage_scope,
            scoped_session_id,
            2,
        )
        await _wait_for_audit_event(
            audit,
            session_id=scoped_session_id,
            event_suffix="turn.completed",
            turn_id=f"turn-{persisted[1].message_id}",
        )
        replacement = agent.orchestrator.runtime_session(scoped_session_id)
        assert replacement is not None
        assert replacement.id != runtime_session.id
    finally:
        await node.stop()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_local_codex_runtime_writes_current_workspace_with_default_sandbox(
    system_temp_dir: Path,
) -> None:
    codex = shutil.which("codex")
    if codex is None:
        pytest.fail("codex CLI is required for the App Server integration test")

    agent_id = str(uuid7())
    agent_name = "Codex Test Agent"
    storage = SqliteDatabase()
    workspace = resolve_workspace_dir(agent_id)
    filename = "sandbox-acceptance.md"
    target = workspace / filename
    if target.exists():
        target.unlink()
    channel = TestChannel()
    audit = RecordingAudit()
    node = NodeApplication(
        configuration=NodeConfiguration(
            storage="sqlite",
            audit="test",
            agents=(
                AgentConfiguration(
                    id=agent_id,
                    name=agent_name,
                    channel=ChannelConfiguration(kind="test"),
                    runtime=RuntimeConfiguration(
                        kind="codex",
                        model=TEST_MODEL,
                        effort=TEST_EFFORT,
                    ),
                ),
            ),
        ),
        shared_factories=SharedAdapterFactories(
            storage=lambda: storage,
            audit=lambda: audit,
        ),
        registry=_StaticRegistry(
            channel=channel,
            runtime=lambda context: Runtime(
                context,
                executable=codex,
                model=TEST_MODEL,
                effort=TEST_EFFORT,
            ),
        ),
        endpoint_path=system_temp_dir / "sandbox-acceptance.sock",
    )
    session_id = f"sandbox-acceptance-{uuid7()}"
    scoped_session_id = str(
        uuid5(
            NAMESPACE_URL,
            f"bcn:{agent_id}:bcn-session:{session_id}",
        )
    )
    message = make_message(
        session_id=session_id,
        body=(
            f"We are preparing this project workspace for a new contributor. Please add "
            f"a {filename} file containing exactly this sentence: Workspace access is "
            "ready. Then confirm briefly that the project note is in place."
        ),
    )
    try:
        await node.start()
        await channel.inject(message)
        persisted = await _wait_for_inbound_messages(
            storage.scope(agent_id, agent_name),
            scoped_session_id,
            1,
        )
        await _wait_for_audit_event(
            audit,
            session_id=scoped_session_id,
            event_suffix="turn.completed",
            turn_id=f"turn-{persisted[0].message_id}",
        )
        assert (
            target.read_text(encoding="utf-8").strip() == "Workspace access is ready."
        )
        assert channel.sent_messages
    finally:
        await node.stop()
        if target.exists():
            target.unlink()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_local_codex_runtime_maps_context_changes_to_expiry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codex = shutil.which("codex")
    if codex is None:
        pytest.fail("codex CLI is required for the App Server integration test")

    home = tmp_path / "home"
    codex_home = home / ".codex"
    skill_file = codex_home / "skills" / "context-probe" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text(
        "---\nname: context-probe\ndescription: Initial context probe.\n---\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    async def unexpected_command(
        session_id: str,
        arguments: Sequence[str],
        body: str | None,
    ) -> None:
        raise AssertionError(
            f"context watch unexpectedly invoked bcc for {session_id}: "
            f"{arguments!r} {body!r}"
        )

    now_ms = time_ns() // 1_000_000
    session = RuntimeSession(
        id=f"runtime-context-{uuid7()}",
        bcn_session_id=f"bcn-context-{uuid7()}",
        channel_session_id=f"channel-context-{uuid7()}",
        runtime="codex",
        workspace_id=f"workspace-context-{uuid7()}",
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
    )
    runtime = Runtime(
        RuntimeCommandContext(
            run_command=unexpected_command,
            environment_for_session=lambda _: dict(os.environ),
            agent_name="Test Agent",
            bot_name=lambda: "provider_bot",
            agent_id="agent-test",
        ),
        executable=codex,
    )

    async def assert_change_expires(change: Callable[[], object]) -> None:
        while not runtime._expire_events.empty():
            runtime._expire_events.get_nowait()
        change()
        async with asyncio.timeout(20):
            assert await runtime.receive_expire() == RuntimeExpire(session.id)
        await asyncio.sleep(0.3)

    await runtime.start(timeout=10)
    try:
        started = await runtime.start_session(session, timeout=20)
        assert started.value is not None
        connection = runtime._connections[session.id]
        workspace_agents = connection.workspace / "AGENTS.md"
        codex_home_agents = codex_home / "AGENTS.md"

        await assert_change_expires(
            lambda: workspace_agents.write_text(
                "First workspace instruction.\n", encoding="utf-8"
            )
        )
        await assert_change_expires(
            lambda: workspace_agents.write_text(
                "Updated workspace instruction.\n", encoding="utf-8"
            )
        )

        replacement = codex_home / "AGENTS.md.next"
        replacement.write_text("Atomic home instruction.\n", encoding="utf-8")
        await assert_change_expires(lambda: replacement.replace(codex_home_agents))
        await assert_change_expires(
            lambda: skill_file.write_text(
                "---\nname: context-probe\ndescription: Updated context probe.\n---\n",
                encoding="utf-8",
            )
        )
    finally:
        await runtime.stop(timeout=20)


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_local_codex_runtime_maps_follow_up_resume_and_concurrency() -> None:
    codex = shutil.which("codex")
    if codex is None:
        pytest.fail("codex CLI is required for the App Server integration test")

    async def unexpected_command(
        session_id: str,
        arguments: Sequence[str],
        body: str | None,
    ) -> None:
        raise AssertionError(
            f"the no-tool scenario unexpectedly invoked bcc for {session_id}: "
            f"{arguments!r} {body!r}"
        )

    async def consume_turn(
        runtime: Runtime,
        session: RuntimeSession,
        label: str,
    ) -> tuple[str, str, tuple[str, ...]]:
        turn = RuntimeTurn(
            turn_id=f"turn-{label}",
            session_id=session.id,
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
        durable_events = [event for event in events if isinstance(event, RuntimeEvent)]
        assert durable_events[-1].state.value == "completed"
        provider_turn_ids = {
            str(event.metadata["provider_turn_id"])
            for event in durable_events
            if event.metadata.get("provider_turn_id") is not None
        }
        assert len(provider_turn_ids) == 1
        provider_thread_id = session.provider_thread_id
        assert provider_thread_id is not None
        return (
            provider_thread_id,
            provider_turn_ids.pop(),
            tuple(event.event_name for event in durable_events),
        )

    agent_id = str(uuid7())
    try:
        now = time_ns() // 1_000_000
        first_session = RuntimeSession(
            id=f"runtime-first-{uuid7()}",
            bcn_session_id=f"bcn-first-{uuid7()}",
            channel_session_id=f"channel-first-{uuid7()}",
            runtime="codex",
            workspace_id=agent_id,
            created_at_ms=now,
            updated_at_ms=now,
        )
        second_session = RuntimeSession(
            id=f"runtime-second-{uuid7()}",
            bcn_session_id=f"bcn-second-{uuid7()}",
            channel_session_id=f"channel-second-{uuid7()}",
            runtime="codex",
            workspace_id=agent_id,
            created_at_ms=now,
            updated_at_ms=now,
        )
        context = RuntimeCommandContext(
            run_command=unexpected_command,
            environment_for_session=lambda _: dict(os.environ),
            agent_name="Test Agent",
            bot_name=lambda: "provider_bot",
            agent_id=agent_id,
        )
        first_runtime = Runtime(
            context,
            executable=codex,
            model=TEST_MODEL,
            effort=TEST_EFFORT,
        )
        second_runtime = Runtime(
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

            active_turn = RuntimeTurn(
                turn_id="turn-active-reconcile",
                session_id=first_running.id,
                state=RuntimeTurnState.STARTING,
                started_at_ms=time_ns() // 1_000_000,
                client_user_message_id="message-active-reconcile",
            )
            active_stream = await first_runtime.start_turn(
                first_running,
                active_turn,
                "Please wait at least ten seconds before replying, then give one "
                "short sentence about explicit state machines.",
                _NoopApprovalHandler(),
                timeout=30,
            )
            started_event = await anext(active_stream)
            assert isinstance(started_event, RuntimeEvent)
            active_provider_turn_id = started_event.metadata.get("provider_turn_id")
            assert isinstance(active_provider_turn_id, str)
            await active_stream.aclose()
            active_turn = replace(
                active_turn,
                state=RuntimeTurnState.RUNNING,
                provider_turn_id=active_provider_turn_id,
            )

            active_result = await first_runtime.reconcile_session(
                first_running,
                active_turn,
                _NoopApprovalHandler(),
                timeout=30,
            )
            assert active_result.value is not None
            assert active_result.value.state is SessionRuntimeState.WORKING
            recovered_stream = active_result.value.stream
            assert recovered_stream is not None
            recovered_events = []
            async with asyncio.timeout(180):
                async for event in recovered_stream:
                    recovered_events.append(event)
            recovered_runtime_events = [
                event for event in recovered_events if isinstance(event, RuntimeEvent)
            ]
            assert recovered_runtime_events[-1].state.value == "completed"

            await first_runtime.stop(timeout=20)
            resumed_runtime = Runtime(
                context,
                executable=codex,
                model=TEST_MODEL,
                effort=TEST_EFFORT,
            )
            await resumed_runtime.start(timeout=10)
            try:
                resumed_result = await resumed_runtime.reconcile_session(
                    first_running,
                    None,
                    None,
                    timeout=30,
                )
                assert resumed_result.value is not None
                assert resumed_result.value.state is SessionRuntimeState.IDLE
                resumed = resumed_result.value.session
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
        pass


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_local_codex_runtime_preserves_natural_conversation_session_behavior(
    system_temp_dir: Path,
) -> None:
    codex = shutil.which("codex")
    if codex is None:
        pytest.fail("codex CLI is required for the App Server integration test")

    await run_natural_conversation_contract(
        channel=TestChannel,
        runtime=lambda context: Runtime(
            context,
            executable=codex,
            model=TEST_MODEL,
            effort=TEST_EFFORT,
        ),
        endpoint_root=system_temp_dir,
    )
