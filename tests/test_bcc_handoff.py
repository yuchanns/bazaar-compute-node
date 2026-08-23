from __future__ import annotations

from collections.abc import Mapping
from io import StringIO

import pytest

from bazaar_compute_node import bcc as bcc_module
from bazaar_compute_node.bcc import build_parser, serialize_handoff_check

SOURCE_MESSAGE_ID = "019d2f00-0000-7000-8000-000000000001"


def handoff_payload(*, read_at_ms: int | None = None) -> dict[str, object]:
    return {
        "handoff_id": "handoff-1",
        "command_id": "command-1",
        "source_session_id": "session-source",
        "target_session_id": "session-target",
        "source_message_id": SOURCE_MESSAGE_ID,
        "body": "Continue the task.",
        "created_at_ms": 1_700_000_000_000,
        "read_at_ms": read_at_ms,
    }


@pytest.mark.asyncio
async def test_handoff_send_maps_parser_stdin_and_request(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    requests: list[Mapping[str, object]] = []

    async def request(
        endpoint: str,
        payload: Mapping[str, object],
    ) -> Mapping[str, object]:
        assert endpoint == "/tmp/bcn.sock"
        requests.append(payload)
        return {
            "ok": True,
            "result": {
                "handoff": handoff_payload(),
                "target": "dm:target",
            },
        }

    monkeypatch.setenv("BCN_ENDPOINT", "/tmp/bcn.sock")
    monkeypatch.setenv("BCN_SESSION_ID", "session-source")
    monkeypatch.setenv("BCN_RUNTIME_SESSION_ID", "runtime-source")
    monkeypatch.setenv("BCN_COMMAND_CAPABILITY", "capability")
    monkeypatch.setattr(bcc_module.LocalCommandClient, "request", request)
    monkeypatch.setattr(bcc_module.sys, "stdin", StringIO("Body from stdin.\n"))

    assert (
        await bcc_module.async_main(
            (
                "handoff",
                "send",
                "--target",
                "dm:target",
                "--message-id",
                SOURCE_MESSAGE_ID,
            )
        )
        == 0
    )

    assert len(requests) == 1
    assert requests[0]["target"] == "dm:target"
    assert requests[0]["body"] == "Body from stdin.\n"
    assert requests[0]["source_message_id"] == SOURCE_MESSAGE_ID
    assert str(requests[0]["command_id"]).startswith("bcc-")
    assert capsys.readouterr().out == "Handoff sent: #handoff-1 target=dm:target\n"


def test_handoff_check_parser_and_serializer() -> None:
    args = build_parser().parse_args(("handoff", "check"))
    assert (args.resource, args.command) == ("handoff", "check")
    assert serialize_handoff_check({"items": [], "has_more": False}) == (
        "No pending handoffs."
    )

    output = serialize_handoff_check(
        {
            "items": [
                {
                    "handoff": handoff_payload(read_at_ms=1_700_000_000_001),
                    "source_target": "group:source",
                }
            ],
            "has_more": True,
        }
    )
    assert "[handoff=handoff-1 source=group:source" in output
    assert f"message={SOURCE_MESSAGE_ID}" in output
    assert output.endswith(
        "More pending handoffs remain. Run `bcc handoff check` again."
    )
