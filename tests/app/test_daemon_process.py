from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path

import pytest

from bazaar_compute_node.app.config import resolve_config_path
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


async def wait_for_health(endpoint: str) -> Mapping[str, object]:
    for _ in range(300):
        response = await request_with_retry(
            endpoint,
            {"kind": "control", "operation": "health"},
        )
        if response.get("ok") is not True:
            raise AssertionError(f"health request failed: {response}")
        result = response.get("result")
        if not isinstance(result, Mapping):
            raise TypeError(f"health response has no result: {response}")
        if result.get("ready") is True:
            return result
        await asyncio.sleep(0.01)
    raise AssertionError("test node health did not become available")


def start_test_process(
    tmp_path: Path,
) -> tuple[subprocess.Popen[str], Path, Path, Path]:
    endpoint = tmp_path / "node.sock"
    endpoint_text = endpoint.as_posix()
    data_dir = resolve_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    config_path = data_dir / "test_config.toml"
    config_path.write_text(
        f"""
version = "3"

[node]
storage = "test"
endpoint = "{endpoint_text}"

[[agent]]
id = "0198d4e6-29c5-7465-b74b-88db31f0c118"
name = "test-agent"

[agent.channel]
kind = "test"

[[agent.runtime]]
kind = "test"
""".lstrip(),
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
            "run",
            "--config",
            str(config_path),
            "--database-name",
            "test.sqlite3",
            "--endpoint",
            str(endpoint),
        ],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return process, endpoint, data_dir, config_path


async def stop_test_process(endpoint_path: Path) -> Mapping[str, object]:
    response = await request_with_retry(
        local_endpoint_for_path(endpoint_path),
        {"kind": "control", "operation": "shutdown"},
    )
    if response.get("ok") is not True:
        raise AssertionError(f"test node rejected shutdown: {response}")
    return response


@pytest.mark.asyncio
async def test_real_process_reports_health_and_keeps_agent_configuration(
    tmp_path: Path,
) -> None:
    process, endpoint_path, data_dir, _ = start_test_process(tmp_path)
    try:
        endpoint = await wait_for_runtime_endpoint(endpoint_path)
        assert endpoint.startswith("pipe://" if os.name == "nt" else "unix://")
        health = await wait_for_health(endpoint)
        assert health["started"] is True
        assert health["ready"] is True, health
        assert health["accepting"] is True
        assert health["configured"] == 1
        assert health["started_agents"] == 1
        assert health["failed_agents"] == 0
        agents = health.get("agents")
        assert isinstance(agents, list)
        assert len(agents) == 1
        agent = agents[0]
        assert isinstance(agent, Mapping)
        assert agent["agent_id"] == "0198d4e6-29c5-7465-b74b-88db31f0c118"
        assert agent["name"] == "test-agent"
        assert agent["status"] == "started"
    finally:
        await stop_test_process(endpoint_path)
        await asyncio.to_thread(process.wait, 5)
        assert process.returncode == 0
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
async def test_foreground_process_restarts_with_persisted_configuration(
    tmp_path: Path,
) -> None:
    process, endpoint_path, data_dir, _ = start_test_process(tmp_path)
    try:
        endpoint = await wait_for_runtime_endpoint(endpoint_path)
        await wait_for_health(endpoint)
        await stop_test_process(endpoint_path)
        await asyncio.to_thread(process.wait, 5)
        assert process.returncode == 0

        process, endpoint_path, data_dir, _ = start_test_process(tmp_path)
        response_endpoint = await wait_for_runtime_endpoint(endpoint_path)
        assert response_endpoint == endpoint
        health = await wait_for_health(response_endpoint)
        assert health["ready"] is True
        assert health["configured"] == 1
        assert health["started_agents"] == 1
    finally:
        if process.poll() is None:
            await stop_test_process(endpoint_path)
        await asyncio.to_thread(process.wait, 5)
        assert process.returncode == 0
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()

    assert not endpoint_path.exists()
    assert not (data_dir / "runtime.lock").exists()


def test_a_first_run_writes_the_configuration_it_needs() -> None:
    # a fresh install has no config file; run is what puts one there, so it has
    # to prepare one unconditionally or the node has nothing to start from
    config_path = resolve_config_path()
    assert not config_path.exists()

    environment = os.environ.copy()
    source_root = str(Path(__file__).parents[2] / "src")
    environment["PYTHONPATH"] = os.pathsep.join(
        value for value in (source_root, environment.get("PYTHONPATH")) if value
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "bazaar_compute_node.cli", "run"],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not config_path.exists():
            if process.poll() is not None:
                _, stderr = process.communicate()
                raise AssertionError(f"bcn run exited before it started: {stderr}")
            time.sleep(0.05)

        assert config_path.exists()
        assert process.poll() is None
    finally:
        process.terminate()
        process.wait(timeout=10)
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
