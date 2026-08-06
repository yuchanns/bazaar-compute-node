from __future__ import annotations

import sys

import pytest

from bazaar_compute_node.app.transport import LocalCommandClient


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
