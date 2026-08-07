from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast

import pytest

from bazaar_compute_node.app.daemon import read_runtime_metadata
from bazaar_compute_node.app.transport import LocalCommandClient


async def wait_for_runtime_endpoint(data_dir: Path) -> str:
    metadata_path = data_dir / "runtime.json"
    for _ in range(200):
        metadata = read_runtime_metadata(metadata_path)
        if metadata is not None:
            return metadata.endpoint
        await asyncio.sleep(0.01)
    raise AssertionError("dummy node did not publish runtime metadata")


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
    raise AssertionError("dummy node status did not reach the expected state")


def start_dummy_process(
    tmp_path: Path,
) -> tuple[subprocess.Popen[str], Path, Path]:
    endpoint = tmp_path / "node.sock"
    data_dir = tmp_path / "bcn"
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
            "dummy",
            "--runtime",
            "dummy",
            "--storage",
            "dummy",
            "--data-dir",
            str(data_dir),
            "--endpoint",
            str(endpoint),
        ],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return process, endpoint, data_dir


def stop_dummy_process(data_dir: Path) -> subprocess.CompletedProcess[str]:
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
            "--data-dir",
            str(data_dir),
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def inbound_payload(session_id: str, seq: int = 1) -> dict[str, object]:
    return {
        "bcn_session_id": session_id,
        "channel_session_id": f"channel-{session_id}",
        "channel_slug": "dummy",
        "provider_message_id": f"provider-{session_id}-{seq}",
        "message_id": f"message-{session_id}-{seq}",
        "seq": seq,
        "received_at_ms": seq,
        "provider_time_ms": seq,
        "sender_id": "sender-1",
        "sender_display_name": "Sender",
        "message_type": "text",
        "canonical_target": f"#dummy:{session_id}",
        "body": f"inbound-{seq}",
        "provider_thread_id": f"thread-{session_id}",
    }


@pytest.mark.asyncio
async def test_real_dummy_process_runs_bcc_commands_and_keeps_sessions_isolated(
    tmp_path: Path,
) -> None:
    process, endpoint_path, data_dir = start_dummy_process(tmp_path)
    await asyncio.to_thread(process.wait, 5)
    assert process.returncode == 0, process.stderr
    try:
        endpoint = await wait_for_runtime_endpoint(data_dir)
        assert endpoint.startswith("tcp://" if os.name == "nt" else "unix://")
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
        assert {message["bcn_session_id"] for message in sent_messages} == {
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
                ["message", "read", "--target", "#dummy:bcn-a"],
                ["message", "send", "--target", "#dummy:bcn-a"],
            ],
            "bcn-b": [
                ["message", "check"],
                ["message", "read", "--target", "#dummy:bcn-b"],
                ["message", "send", "--target", "#dummy:bcn-b"],
            ],
        }
        assert all(turn["state"] == "completed" for turn in runtime_turns.values())
        assert all(outbound["state"] == "sent" for outbound in outbound_messages)
    finally:
        stop_process = await asyncio.to_thread(stop_dummy_process, data_dir)
        assert stop_process.returncode == 0, stop_process.stderr
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()

    assert not endpoint_path.exists()
    assert not (data_dir / "runtime.json").exists()


@pytest.mark.asyncio
async def test_daemon_restart_reuses_persisted_adapter_selection(
    tmp_path: Path,
) -> None:
    process, endpoint_path, data_dir = start_dummy_process(tmp_path)
    await asyncio.to_thread(process.wait, 5)
    assert process.returncode == 0, process.stderr
    try:
        endpoint = await wait_for_runtime_endpoint(data_dir)
        first_metadata = read_runtime_metadata(data_dir / "runtime.json")
        assert first_metadata is not None
        assert endpoint == first_metadata.endpoint
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
                "--data-dir",
                str(data_dir),
            ],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        assert restart_process.returncode == 0, restart_process.stderr
        second_metadata = read_runtime_metadata(data_dir / "runtime.json")
        assert second_metadata is not None
        assert second_metadata.channel_slug == "dummy"
        assert second_metadata.runtime_slug == "dummy"
        if os.name == "nt":
            assert second_metadata.endpoint.startswith("tcp://127.0.0.1:")
        else:
            assert second_metadata.endpoint == first_metadata.endpoint
        assert second_metadata.pid != first_metadata.pid
        response = await request_with_retry(
            second_metadata.endpoint,
            {"kind": "control", "operation": "status"},
        )
        assert response.get("ok") is True
    finally:
        stop_process = await asyncio.to_thread(stop_dummy_process, data_dir)
        assert stop_process.returncode == 0, stop_process.stderr
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()

    assert not endpoint_path.exists()
    assert not (data_dir / "runtime.json").exists()
