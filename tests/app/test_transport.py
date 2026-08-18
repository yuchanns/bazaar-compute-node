from __future__ import annotations

import asyncio
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest

from bazaar_compute_node.app.transport import (
    LocalCommandClient,
    LocalCommandServer,
    local_endpoint_for_path,
)


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only transport guard")
async def test_windows_rejects_unix_command_endpoint() -> None:
    with pytest.raises(
        ValueError,
        match="Unix command endpoints are not supported on Windows",
    ):
        await LocalCommandClient.request(
            "unix:///unsupported.sock",
            {"kind": "control", "operation": "status"},
        )


@pytest.mark.asyncio
async def test_local_transport_authenticates_and_restricts_tcp_fallback(
    tmp_path: Path,
) -> None:
    async def handle(_: Mapping[str, object]) -> dict[str, object]:
        return {"ok": True, "result": {"accepted": True}}

    server = LocalCommandServer(handle, endpoint_path=tmp_path / "bcn.sock")
    await server.start()
    try:
        endpoint = server.endpoint
        assert endpoint == local_endpoint_for_path(tmp_path / "bcn.sock")
        if sys.platform == "win32":
            assert endpoint.startswith("pipe://")
        else:
            assert endpoint.startswith("unix://")
        response = await LocalCommandClient.request(
            endpoint,
            {"kind": "control", "operation": "health"},
        )
        assert response["ok"] is True
        if endpoint.startswith("pipe://"):
            responses = await asyncio.gather(
                *(
                    LocalCommandClient.request(
                        endpoint,
                        {"kind": "control", "operation": "health"},
                    )
                    for _ in range(4)
                )
            )
            assert all(item["ok"] is True for item in responses)
        if endpoint.startswith("tcp://"):
            endpoint_without_query, query = endpoint.split("?", maxsplit=1)
            with pytest.raises(ValueError, match="capability token"):
                await LocalCommandClient.request(endpoint_without_query, {})
            invalid_token = f"{endpoint_without_query}?token=invalid"
            invalid_response = await LocalCommandClient.request(invalid_token, {})
            assert invalid_response["code"] == "LOCAL_AUTH_FAILED"
            port = endpoint_without_query.rsplit(":", maxsplit=1)[1]
            with pytest.raises(ValueError, match="loopback"):
                await LocalCommandClient.request(f"tcp://192.0.2.1:{port}?{query}", {})
    finally:
        await server.stop()
