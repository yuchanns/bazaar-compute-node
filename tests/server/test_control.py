from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from bazaar_compute_server.control import Controls

from ._node import AGENT_ID, node_reporting_to
from ._serving import enrol, serving_app


@pytest.mark.asyncio
async def test_a_request_goes_down_and_its_answer_comes_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with serving_app(tmp_path) as (base, app):
        storage = app.state.storage
        controls: Controls = app.state.controls
        enrolment = await enrol(storage, "kana")
        monkeypatch.setenv("BCN_SERVER_TOKEN", enrolment.token)
        node = await node_reporting_to(base, tmp_path)
        try:
            assert node.control is not None
            request: dict[str, Any] = {"read": "contacts", "agent_id": AGENT_ID}
            # case: asked once, run once, answered once
            answer, again = await asyncio.gather(
                controls.ask(enrolment.computer.id, request),
                controls.ask(enrolment.computer.id, request),
            )
            assert answer is not None and answer["ok"] is True, answer
            assert answer["result"]["targets"] == []
            assert answer == again
            assert node.control.health["served"] == 1
            assert node.control.health["last_error"] is None

            # case: a second, different request is its own
            other = await controls.ask(enrolment.computer.id, {**request, "offset": 50})
            assert other is not None and other["result"]["offset"] == 50
            assert node.control.health["served"] == 2

            # case: what the node cannot read is answered with why
            refused = await controls.ask(
                enrolment.computer.id, {"read": "contacts", "agent_id": "nobody"}
            )
            assert refused is not None and refused["code"] == "AGENT_NOT_AVAILABLE"
            malformed = await controls.ask(enrolment.computer.id, {"read": "mind"})
            assert malformed is not None and malformed["code"] == "INVALID_READ"
        finally:
            await node.stop()


@pytest.mark.asyncio
async def test_a_computer_that_does_not_answer_is_said_so_and_asked_no_more(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with serving_app(tmp_path) as (base, app):
        storage = app.state.storage
        controls: Controls = app.state.controls
        enrolment = await enrol(storage, "kana")
        monkeypatch.setenv("BCN_SERVER_TOKEN", enrolment.token)
        request = {"read": "contacts", "agent_id": AGENT_ID}

        # case: nobody is polling: the asker gets nothing after its wait, and
        # the request is not kept for a node that comes later
        assert await controls.ask(enrolment.computer.id, request, timeout=0.2) is None
        node = await node_reporting_to(base, tmp_path)
        try:
            assert node.control is not None
            async with asyncio.timeout(10):
                while node.control.health["last_polled_at_ms"] is None:
                    await asyncio.sleep(0.05)
            assert node.control.health["served"] == 0

            # case: a node that comes up while someone still waits gets the
            # request and answers it; an asker that gave up earlier on the
            # same request took nothing from the one still waiting
            await node.stop()
            waiting = asyncio.create_task(
                controls.ask(enrolment.computer.id, request, timeout=8)
            )
            assert (
                await controls.ask(enrolment.computer.id, request, timeout=0.2) is None
            )
            node = await node_reporting_to(base, tmp_path)
            answer = await waiting
            assert answer is not None and answer["ok"] is True
            assert node.control is not None
            assert node.control.health["served"] == 1
        finally:
            await node.stop()


@pytest.mark.asyncio
async def test_a_server_that_is_not_there_is_asked_again_quietly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BCN_SERVER_TOKEN", "kana:secret")
    node = await node_reporting_to("http://127.0.0.1:1", tmp_path)
    try:
        assert node.control is not None
        async with asyncio.timeout(10):
            while node.control.health["last_error"] is None:
                await asyncio.sleep(0.05)
        assert "ClientConnectorError" in str(node.control.health["last_error"])
        assert node.control.health["served"] == 0
        # case: reporting is not held up by the failing fetches
        assert node.audit.health["queued"] == 0
    finally:
        await node.stop()
