from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast

import pytest

from bazaar_compute_node.app.transport import (
    LocalCommandClient,
    local_endpoint_for_path,
)
from bazaar_compute_node.core.paths import resolve_data_dir


async def wait_for_runtime_endpoint(endpoint_path: Path) -> str:
    endpoint = local_endpoint_for_path(endpoint_path)
    for _ in range(200):
        response: Mapping[str, object] | None = None
        try:
            response = await LocalCommandClient.request(
                endpoint,
                {"kind": "control", "operation": "health"},
                timeout=1,
            )
        except Exception:  # noqa: BLE001
            response = None
        if response is not None and response.get("ok") is True:
            return endpoint
        await asyncio.sleep(0.01)
    raise AssertionError("test node did not publish its local endpoint")


async def request_with_retry(
    endpoint: str,
    payload: Mapping[str, object],
) -> Mapping[str, object]:
    last_error: OSError | TimeoutError | None = None
    for _ in range(200):
        try:
            return await LocalCommandClient.request(endpoint, payload, timeout=1)
        except (TimeoutError, ConnectionError, FileNotFoundError, OSError) as error:
            last_error = error
            await asyncio.sleep(0.01)
    raise AssertionError(
        f"local command request did not become available: {last_error}"
    )


async def wait_for_status(
    endpoint: str,
    predicate: Callable[[Mapping[str, object]], bool],
) -> Mapping[str, object]:
    for _ in range(300):
        response = await request_with_retry(
            endpoint,
            {"kind": "control", "operation": "status"},
        )
        if response.get("ok") is not True:
            raise AssertionError(f"status request failed: {response}")
        result = response.get("result")
        if not isinstance(result, Mapping):
            raise TypeError(f"status response has no result: {response}")
        if predicate(result):
            return result
        await asyncio.sleep(0.01)
    raise AssertionError("test node status did not reach the expected state")


def start_test_process(
    tmp_path: Path,
) -> tuple[subprocess.Popen[str], Path, Path]:
    endpoint = tmp_path / "node.sock"
    endpoint_text = endpoint.as_posix()
    data_dir = resolve_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "config.toml").write_text(
        f'[node]\nchannel = "test"\nruntime = "test"\n'
        f'storage = "test"\nendpoint = "{endpoint_text}"\n',
        encoding="utf-8",
    )
    environment = os.environ.copy()
    source_root = str(Path(__file__).parents[2] / "src")
    environment["PYTHONPATH"] = os.pathsep.join(
        value for value in (source_root, environment.get("PYTHONPATH")) if value
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "bazaar_compute_node.cli",
            "start",
            "--channel",
            "test",
            "--runtime",
            "test",
            "--storage",
            "test",
            "--endpoint",
            str(endpoint),
        ],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return process, endpoint, data_dir


def stop_test_process() -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    source_root = str(Path(__file__).parents[2] / "src")
    environment["PYTHONPATH"] = os.pathsep.join(
        value for value in (source_root, environment.get("PYTHONPATH")) if value
    )
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "bazaar_compute_node.cli",
            "stop",
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def inbound_payload(session_id: str, seq: int = 1) -> dict[str, object]:
    return {
        "session_id": session_id,
        "channel_session_id": f"channel-{session_id}",
        "channel": "test",
        "provider_message_id": f"provider-{session_id}-{seq}",
        "message_id": f"message-{session_id}-{seq}",
        "seq": seq,
        "received_at_ms": seq,
        "provider_time_ms": seq,
        "sender": "Sender",
        "message_type": "text",
        "canonical_target": f"#test:{session_id}",
        "body": f"inbound-{seq}",
        "provider_thread_id": f"thread-{session_id}",
    }


@pytest.mark.asyncio
async def test_real_process_runs_bcc_commands_and_keeps_sessions_isolated(
    tmp_path: Path,
) -> None:
    process, endpoint_path, data_dir = start_test_process(tmp_path)
    await asyncio.to_thread(process.wait, 5)
    assert process.returncode == 0, process.stderr
    try:
        endpoint = await wait_for_runtime_endpoint(endpoint_path)
        assert endpoint.startswith("pipe://" if os.name == "nt" else "unix://")
        for session_id in ("bcn-a", "bcn-b"):
            response = await request_with_retry(
                endpoint,
                {
                    "kind": "control",
                    "operation": "inject",
                    "message": inbound_payload(session_id),
                },
            )
            assert response.get("ok") is True

        status = await wait_for_status(
            endpoint,
            lambda value: (
                len(cast(list[object], value.get("sent_messages", []))) == 2
                and all(
                    turn.get("state") == "completed"
                    for turn in cast(
                        dict[str, dict[str, object]],
                        value.get("runtime_turns", {}),
                    ).values()
                )
            ),
        )
        inbound_messages = cast(dict[str, int], status["inbound_messages"])
        sent_messages = cast(list[dict[str, object]], status["sent_messages"])
        bcc_commands = cast(list[dict[str, object]], status["bcc_commands"])
        runtime_turns = cast(dict[str, dict[str, object]], status["runtime_turns"])
        outbound_messages = cast(list[dict[str, object]], status["outbound_messages"])
        assert inbound_messages == {"bcn-a": 1, "bcn-b": 1}
        assert {message["session_id"] for message in sent_messages} == {
            "bcn-a",
            "bcn-b",
        }
        commands_by_session: dict[str, list[list[object]]] = {}
        for entry in bcc_commands:
            session_id = cast(str, entry["session_id"])
            command = cast(list[object], entry["command"])
            commands_by_session.setdefault(session_id, []).append(command)
        assert commands_by_session == {
            "bcn-a": [
                ["message", "check"],
                ["message", "read", "--target", "#test:bcn-a"],
                ["message", "send", "--target", "#test:bcn-a"],
            ],
            "bcn-b": [
                ["message", "check"],
                ["message", "read", "--target", "#test:bcn-b"],
                ["message", "send", "--target", "#test:bcn-b"],
            ],
        }
        assert all(turn["state"] == "completed" for turn in runtime_turns.values())
        assert all(outbound["state"] == "sent" for outbound in outbound_messages)
    finally:
        stop_process = await asyncio.to_thread(stop_test_process)
        assert stop_process.returncode == 0, stop_process.stderr
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()

    assert not endpoint_path.exists()
    assert not (data_dir / "runtime.lock").exists()
    if os.name == "nt":
        assert not (data_dir / "bin" / "bcc.cmd").exists()
        assert not (data_dir / "bin" / "bcc.ps1").exists()
    else:
        assert not (data_dir / "bin" / "bcc").exists()


@pytest.mark.asyncio
async def test_daemon_restart_uses_persisted_configuration(
    tmp_path: Path,
) -> None:
    process, endpoint_path, data_dir = start_test_process(tmp_path)
    await asyncio.to_thread(process.wait, 5)
    assert process.returncode == 0, process.stderr
    try:
        endpoint = await wait_for_runtime_endpoint(endpoint_path)
        environment = os.environ.copy()
        source_root = str(Path(__file__).parents[2] / "src")
        environment["PYTHONPATH"] = os.pathsep.join(
            value for value in (source_root, environment.get("PYTHONPATH")) if value
        )
        restart_process = await asyncio.to_thread(
            subprocess.run,
            [
                sys.executable,
                "-m",
                "bazaar_compute_node.cli",
                "restart",
            ],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        assert restart_process.returncode == 0, restart_process.stderr
        response_endpoint = await wait_for_runtime_endpoint(endpoint_path)
        assert response_endpoint == endpoint
        response = await request_with_retry(
            response_endpoint,
            {"kind": "control", "operation": "status"},
        )
        assert response.get("ok") is True
    finally:
        stop_process = await asyncio.to_thread(stop_test_process)
        assert stop_process.returncode == 0, stop_process.stderr
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()

    assert not endpoint_path.exists()
    assert not (data_dir / "runtime.lock").exists()
