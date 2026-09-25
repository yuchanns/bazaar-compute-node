from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any
from uuid import uuid7

import pytest
from bcn_test_support import TestRuntime

from bazaar_compute_node.core.models import Message, MessageDirection, SenderIdentity
from bazaar_compute_node.core.paths import resolve_workspace_dir
from bazaar_compute_node.core.reminder import ReminderScheduleRequest
from bazaar_compute_node.core.runtime import RuntimeDescription, RuntimeSkill
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


@pytest.mark.asyncio
async def test_a_server_takes_an_agent_in_changes_it_and_lets_it_go(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """From outside, the agents a node runs are read with the kinds it could
    run, a new one is written and starts at once, changed by writing it
    again, looked into, and let go - the configuration file following each
    time and the credential never coming back."""

    async with serving_app(tmp_path) as (base, app):
        storage = app.state.storage
        controls: Controls = app.state.controls
        enrolment = await enrol(storage, "kana")
        monkeypatch.setenv("BCN_SERVER_TOKEN", enrolment.token)
        node = await node_reporting_to(base, tmp_path)
        computer = enrolment.computer.id
        config_path = tmp_path / "config.toml"

        async def up(agent_id: str) -> None:
            """Until the start of an agent the node answered for is over: it
            runs, or it failed and says why."""

            async with asyncio.timeout(10):
                while not (
                    (agent_id in node.agents and node.agents[agent_id].started)
                    or agent_id in node.agent_startup_results
                ):
                    await asyncio.sleep(0.02)

        async def ask(**request: object) -> dict[str, Any]:
            answer = await controls.ask(computer, request)
            assert answer is not None and answer["ok"] is True, answer
            return answer["result"]

        try:
            # case: what it runs now, and what it could run
            listing = await ask(read="agents")
            assert [agent["id"] for agent in listing["agents"]] == [AGENT_ID]
            assert listing["agents"][0]["status"] == "idle"
            assert "test" in listing["kinds"]["channels"]
            assert "test" in listing["kinds"]["runtimes"]

            # case: a new one, with its credential and a value for its
            # runtime's environment; the answer comes once the file has it,
            # naming where each is kept, and it starts after
            written = await ask(
                write="agent",
                agent={
                    "name": "Newcomer",
                    "channel": [{"kind": "test"}],
                    "runtime": [{"kind": "test"}],
                },
                secrets={"0": {"token": "secret-1"}},
                env={"0": {"API_KEY": "value-1", "api_key": "value-2"}},
                settings={"review.reply": "One moment."},
            )
            newcomer = str(written["id"])
            assert newcomer != AGENT_ID and written["status"] == "init"
            await up(newcomer)
            # what it does was kept with it, not asked of it while it started
            said = await ask(read="setting", agent_id=newcomer, key="review.reply")
            assert said["value"] == "One moment."
            assert "secret-1" not in str(written) and "value-1" not in str(written)
            prefix = f"BCN_{newcomer.replace('-', '_').upper()}"
            kept = f"{prefix}_RUNTIME0_TEST_ENV_API_KEY"
            # two names apart by case are two variables, kept apart
            assert written["runtime"][0]["env"] == {
                "API_KEY": kept,
                "api_key": f"{prefix}_RUNTIME0_TEST_ENV_api_key",
            }
            assert os.environ[f"{prefix}_RUNTIME0_TEST_ENV_api_key"] == "value-2"
            assert os.environ[kept] == "value-1"
            assert written["channel"] == [
                {"kind": "test", "token_env": f"{prefix}_CHANNEL0_TEST_TOKEN"}
            ]
            assert 'name = "Newcomer"' in config_path.read_text()
            # a second one made at once, with two cards of one kind, keeps
            # every credential apart
            twin = await ask(
                write="agent",
                agent={
                    "name": "Twin",
                    "channel": [{"kind": "test"}, {"kind": "test"}],
                    "runtime": [{"kind": "test"}],
                },
                secrets={"0": {"token": "secret-2"}, "1": {"token": "secret-3"}},
                settings={"review.reply": "Back soon."},
            )
            await up(str(twin["id"]))
            # what it is set to do is read from where it is kept, running or
            # not - this one does not start
            assert str(twin["id"]) not in node.agents
            said = await ask(
                read="setting", agent_id=str(twin["id"]), key="review.reply"
            )
            assert said["value"] == "Back soon."
            names = [channel["token_env"] for channel in twin["channel"]]
            assert len({*names, f"{prefix}_CHANNEL0_TEST_TOKEN"}) == 3
            assert [os.environ[name] for name in names] == ["secret-2", "secret-3"]
            assert os.environ[f"{prefix}_CHANNEL0_TEST_TOKEN"] == "secret-1"
            await ask(remove="agent", agent_id=str(twin["id"]))

            # case: written again under the same id, it is changed, and the
            # listing says its credential is set without saying what it is
            renamed = await ask(write="agent", agent={**written, "name": "Renamed"})
            assert renamed["id"] == newcomer and renamed["name"] == "Renamed"
            await up(newcomer)
            assert node.agents[newcomer].name == "Renamed"
            listing = await ask(read="agents")
            newest = next(a for a in listing["agents"] if a["id"] == newcomer)
            assert newest["secrets"] == [{"token": True}]

            # case: a configuration the node will not have is refused, and
            # the agent goes on as it was
            refused = await controls.ask(
                computer, {"write": "agent", "agent": {"id": newcomer, "name": ""}}
            )
            assert refused is not None and refused["ok"] is False
            assert refused["code"] == "REFUSED"
            assert node.agents[newcomer].name == "Renamed"
            # one naming an agent the node does not have is not taken in anew
            gone = await controls.ask(
                computer,
                {
                    "write": "agent",
                    "agent": {**renamed, "id": "019a0000-0000-7000-8000-000000000000"},
                },
            )
            assert gone is not None and gone["code"] == "TARGET_NOT_FOUND"
            assert len(node.configuration.agents) == 2
            # and a value the environment file could not hold safely, before
            # anything is written
            refused = await controls.ask(
                computer,
                {
                    "write": "agent",
                    "agent": renamed,
                    "env": {"0": {"QUOTED": "it's"}},
                },
            )
            assert refused is not None and refused["ok"] is False
            assert refused["code"] == "REFUSED"
            assert not [name for name in os.environ if name.endswith("_ENV_QUOTED")]
            # and so is an environment name it could not keep
            refused = await controls.ask(
                computer,
                {"write": "agent", "agent": renamed, "env": {"0": {"bad-name": "x"}}},
            )
            assert refused is not None and refused["ok"] is False
            assert refused["code"] == "REFUSED"

            # case: what it has to work with, and what it could run
            workspace = await ask(read="workspace", agent_id=newcomer)
            # what is in it, and not where it is on the computer
            assert "path" not in workspace and workspace["at"] == ""
            # a directory at a time, and never one outside it
            (resolve_workspace_dir(newcomer) / "notes" / "deep").mkdir(parents=True)
            (resolve_workspace_dir(newcomer) / "notes" / "a.md").write_text("hi")
            # a link is not listed, whether it leads out or nowhere
            (resolve_workspace_dir(newcomer) / "notes" / "out").symlink_to("/")
            (resolve_workspace_dir(newcomer) / "notes" / "broken").symlink_to("gone")
            notes = await ask(read="workspace", agent_id=newcomer, path="notes")
            assert notes["at"] == "notes" and notes["more"] is False
            assert [(e["name"], e["kind"]) for e in notes["entries"]] == [
                ("a.md", "file"),
                ("deep", "directory"),
            ]
            outside = await controls.ask(
                computer,
                {"read": "workspace", "agent_id": newcomer, "path": "../.."},
            )
            assert outside is not None and outside["ok"] is False
            assert outside["code"] == "REFUSED"
            # nor is one named for an agent the node does not run
            stranger = await controls.ask(
                computer, {"read": "workspace", "agent_id": "nobody"}
            )
            assert stranger is not None and stranger["ok"] is False
            assert stranger["code"] == "TARGET_NOT_FOUND"
            runtimes = await ask(read="runtimes")
            said = {runtime["kind"]: runtime for runtime in runtimes["runtimes"]}
            assert said["test"]["available"] is True
            assert said["test"]["version"] == "1.2.3"
            models = await ask(read="models", kind="test")
            assert models["error"] is None
            assert models["models"][0]["id"] == "test-model"

            # case: the skills of an agent that is up, and of one that is not
            assert await ask(read="skills", agent_id=newcomer) == {"skills": []}
            runtime = node.agents[newcomer].runtimes[0]
            assert isinstance(runtime, TestRuntime)
            runtime.description = RuntimeDescription(
                skills=(
                    RuntimeSkill(
                        name="brainstorming", description="", source="workspace"
                    ),
                )
            )
            found = await ask(read="skills", agent_id=newcomer)
            assert [skill["name"] for skill in found["skills"]] == ["brainstorming"]
            assert await ask(read="skills", agent_id="nobody") == {"skills": []}

            # case: let go - off the node and out of the file
            gone = await ask(remove="agent", agent_id=newcomer)
            assert gone["id"] == newcomer and newcomer not in node.agents
            assert "Renamed" not in config_path.read_text()
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
