from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from uuid import uuid7

import pytest

from bazaar_compute_node.core.models import Message, MessageDirection, SenderIdentity
from bazaar_compute_node.core.reminder import ReminderScheduleRequest
from bazaar_compute_node.core.utils.clock import now_ms
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
            assert malformed is not None and malformed["code"] == "INVALID_REQUEST"
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


@pytest.mark.asyncio
async def test_a_server_reviews_and_sets_what_an_agent_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """From outside, a conversation pending review is listed and read, let
    in or turned away, its members and reminders read, and what the agent
    says to those still waiting set - all through the same door."""

    async with serving_app(tmp_path) as (base, app):
        storage = app.state.storage
        controls: Controls = app.state.controls
        enrolment = await enrol(storage, "kana")
        monkeypatch.setenv("BCN_SERVER_TOKEN", enrolment.token)
        node = await node_reporting_to(base, tmp_path)
        try:
            agent = node.agents[AGENT_ID]
            # a stranger writes twice; the node has a server, so they wait
            anchor = str(uuid7())
            for seq in (1, 2):
                await agent.orchestrator.handle_inbound(_stranger(seq, anchor))
            computer = enrolment.computer.id

            async def ask(**request: object) -> dict[str, Any]:
                answer = await controls.ask(computer, {"agent_id": AGENT_ID, **request})
                assert answer is not None and answer["ok"] is True, answer
                return answer["result"]

            # case: to the listing they are pending, counted, and not among
            # the approved
            approved = await ask(read="contacts")
            assert approved["targets"] == [] and approved["pending_review"] == 1
            waiting = await ask(read="contacts", review="pending")
            (row,) = waiting["targets"]
            assert row["review"] == "pending" and row["thread_id"] == "stranger"
            # case: the conversation reads, whatever its state, from outside
            history = await ask(
                read="history",
                actor_id=row["actor_id"],
                target=row["canonical_target"],
                limit=1,
            )
            assert [item["body"] for item in history["messages"]] == ["hello 1"]

            # case: let in, they move to the approved list; the server
            # learns of it as an event
            decided = await ask(write="review", thread_id="stranger", review="approved")
            assert decided["review"] == "approved"
            approved = await ask(read="contacts")
            assert [item["thread_id"] for item in approved["targets"]] == ["stranger"]
            assert approved["pending_review"] == 0
            async with asyncio.timeout(10):
                while not [
                    event
                    for event in await storage.recent_activity(
                        computer, AGENT_ID, limit=20, skipping=()
                    )
                    if event.event_name == "channel.session.reviewed"
                ]:
                    await asyncio.sleep(0.05)

            # case: what the agent says to those waiting is set and read back
            unset = await ask(read="setting", key="review.reply")
            assert unset["value"] is None
            await ask(write="setting", key="review.reply", value="Not yet.")
            assert (await ask(read="setting", key="review.reply"))[
                "value"
            ] == "Not yet."

            # case: the reminders an actor can reach come as scheduled
            await agent.orchestrator.command_service.schedule_reminder(
                agent.actors.for_thread("stranger"),
                ReminderScheduleRequest(
                    title="follow up",
                    message_id=anchor,
                    next_fire_at_ms=now_ms() + 3_600_000,
                    repeat_rule=None,
                    timezone="UTC",
                ),
            )
            reminders = await ask(read="reminders", actor_id=row["actor_id"])
            assert [item["title"] for item in reminders["reminders"]] == ["follow up"]
        finally:
            await node.stop()


def _stranger(seq: int, first_message_id: str) -> Message:
    return Message(
        direction=MessageDirection.INBOUND,
        seq=seq,
        message_id=first_message_id if seq == 1 else f"message-stranger-{seq}",
        thread_id="stranger",
        channel_session_id="channel-stranger",
        channel="test",
        provider_thread_id="thread-stranger",
        provider_message_id=f"provider-stranger-{seq}",
        received_at_ms=seq * 1_000,
        sender=SenderIdentity(id="stranger-id", name="Stranger"),
        message_type="text",
        target="dm:channel-stranger",
        body=f"hello {seq}",
        metadata={"sender_kind": "human"},
    )
