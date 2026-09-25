from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from bazaar_compute_node.core.paths import resolve_workspace_dir
from bazaar_compute_server.activity import PAGE_EVENTS
from bazaar_compute_server.protocol import Event

from ._node import AGENT_ID, node_reporting_to
from ._serving import enrol, serving_app, signed_in
from .test_pages import _event, _get, _short


@pytest.mark.asyncio
async def test_an_agent_is_seen_changed_and_let_go_from_its_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gear on an agent's row opens its page: five tabs, the first its
    configuration. Renamed and saved there, the computer runs it by the new
    name and the lists follow; let go from there, the module comes back with
    nothing open and the agent off its computer."""

    async with serving_app(tmp_path) as (base, app):
        storage = app.state.storage
        enrolment = await enrol(storage, "kana")
        monkeypatch.setenv("BCN_SERVER_TOKEN", enrolment.token)
        node = await node_reporting_to(base, tmp_path)
        try:
            async with asyncio.timeout(10):
                while not (await storage.computer_health([enrolment.computer]))[
                    0
                ].health:
                    await asyncio.sleep(0.05)
            # more of the agent's events than a page of the activity tab holds
            await storage.record_events(
                enrolment.computer.id,
                "profile-run",
                [
                    Event.model_validate(
                        _event(seq, "tool_call.started", AGENT_ID, name=f"step-{seq}")
                    )
                    for seq in range(1, PAGE_EVENTS + 6)
                ]
                + [
                    Event.model_validate(
                        _event(
                            PAGE_EVENTS + 6,
                            "usage.updated",
                            AGENT_ID,
                            total={
                                "input_tokens": 1000,
                                "output_tokens": 200,
                                "cached_input_tokens": 34,
                            },
                            cost_usd=0.5,
                        )
                    )
                ],
            )

            async with signed_in(base, storage) as session:
                headers = {"Accept-Language": "en"}
                key = await _short(storage, enrolment.computer.id, AGENT_ID)
                page_url = f"{base}/agents/{key}/profile"

                # case: the page opens on its configuration, as the computer
                # holds it, with the lists on its left
                status, page = await _get(session, page_url, **headers)
                assert status == 200 and 'id="profile-page"' in page
                assert 'id="agent-list"' in page
                assert 'name="name" value="Kana"' in page
                assert 'name="channel-0-was" value="0"' in page
                # a runtime naming no model holds the default alone until its
                # field is opened, and a save untouched keeps it so
                model = page.split('name="runtime-1-model"')[1].split("</select>")[0]
                assert '<option value="">Default</option>' in model
                assert f'data-computer="/computers/{key.split("/")[0]}"' in page
                for tab in ("config", "skills", "workspace", "status", "activity"):
                    assert f'hx-get="/agents/{key}/profile/{tab}"' in page

                # case: every tab opens on its own
                status, skills = await _get(session, f"{page_url}/skills", **headers)
                assert status == 200 and "No skills found yet." in skills
                folder = resolve_workspace_dir(AGENT_ID) / "notes"
                folder.mkdir(parents=True)
                (folder / "plan.md").write_text("hi")
                status, files = await _get(session, f"{page_url}/workspace", **headers)
                # what is in it, not where it is on the computer
                assert status == 200 and str(folder.parent) not in files
                # a folder opens onto its own contents, read when it opens
                assert f'hx-get="/agents/{key}/profile/workspace?path=notes"' in files
                assert "plan.md" not in files
                status, inside = await _get(
                    session, f"{page_url}/workspace?path=notes", **headers
                )
                assert status == 200 and "plan.md" in inside
                status, record = await _get(session, f"{page_url}/status", **headers)
                # read for a person: whether it runs, and how its channel is
                assert status == 200 and "<label>Agent</label>" in record
                assert '<span class="dot running"></span>Running' in record
                assert "<label>Connection</label>" in record
                # and what it used today, as the card by its face says
                assert "Used today" in record and "$0.50" in record
                status, events = await _get(session, f"{page_url}/activity", **headers)
                # the tab is the log itself, not its lines alone
                assert status == 200 and 'class="log"' in events and "step-" in events
                assert events.count('class="ln"') == PAGE_EVENTS
                # today's lines are under no date
                assert 'class="day"' not in events
                # the end asks for the page before it
                older = re.search(r'hx-get="([^"]*before=\d+[^"]*)"', events)
                assert older is not None
                status, rest = await _get(
                    session, base + older[1].replace("&amp;", "&"), **headers
                )
                assert status == 200 and 0 < rest.count('class="ln"') < PAGE_EVENTS
                # a refresh from a cursor reads on from it in order: a burst
                # longer than a page between two refreshes is not passed over
                shown = sorted(
                    int(found)
                    for found in re.findall(
                        r'class="ln"[^>]*data-id="(\d+)"', events + rest
                    )
                )
                status, fresh = await _get(
                    session, f"{page_url}/events?after={shown[0]}", **headers
                )
                assert status == 200
                assert [
                    int(found)
                    for found in re.findall(
                        r'class="ln[^"]*"[^>]*data-id="(\d+)"', fresh
                    )
                ] == shown[1 : PAGE_EVENTS + 1][::-1]
                # lines coming in take the place of saying there are none
                assert '<div id="events-empty" hx-swap-oob="delete">' in fresh
                status, _ = await _get(session, f"{page_url}/elsewhere", **headers)
                assert status == 404

                # case: renamed and saved, the computer runs it by that name,
                # keeping what the page does not show, and the lists follow
                kept = next(
                    agent for agent in node.configuration.agents if agent.id == AGENT_ID
                )
                async with session.post(
                    page_url,
                    data={
                        "name": "Renamed",
                        "mode": "session",
                        "idle_timeout": "0",
                        "reply": "One moment.",
                        "channel-0-kind": "test",
                        "channel-0-was": "0",
                        "runtime-1-kind": "test",
                        "runtime-1-was": "0",
                        "runtime-1-model": "test-model",
                        "runtime-1-sandbox_mode": "workspace-write",
                        "runtime-1-network_access": "on",
                        "runtime-1-env-0-name": "API_KEY",
                        "runtime-1-env-0-value": "value-1",
                    },
                    headers=headers,
                ) as response:
                    saved = await response.text()
                    assert response.headers["HX-Trigger"] == "agents-changed"
                assert "Saved." in saved and 'value="Renamed"' in saved
                # refused, the form comes back as filled in: a card added in
                # front of a kept one does not take over what the kept one was
                async with session.post(
                    page_url,
                    data={
                        "name": "Renamed",
                        "mode": "nonsense",
                        "idle_timeout": "0",
                        "reply": "",
                        "channel-3-kind": "test",
                        "channel-5-kind": "test",
                        "channel-5-was": "0",
                        "runtime-6-kind": "test",
                        "runtime-6-was": "0",
                        "runtime-6-sandbox_mode": "workspace-write",
                    },
                    headers=headers,
                ) as response:
                    refused = await response.text()
                assert "rejected" in refused
                assert 'name="channel-0-was"' not in refused
                assert 'name="channel-1-was" value="0"' in refused
                # the page's heading takes the new name at once
                assert 'id="profile-head" hx-swap-oob="outerHTML"' in saved
                assert saved.split('id="profile-head"')[1].count("Renamed") >= 1
                renamed = next(
                    agent for agent in node.configuration.agents if agent.id == AGENT_ID
                )
                assert renamed.name == "Renamed"
                assert renamed.channels == kept.channels
                assert renamed.runtimes[0].model == "test-model"
                # the value typed for the environment is kept by the node,
                # and the page shows it set without showing it
                assert renamed.runtimes[0].env == {
                    "API_KEY": f"BCN_{AGENT_ID.replace('-', '_').upper()}_RUNTIME0_TEST_ENV_API_KEY"
                }
                assert "value-1" not in saved
                assert 'name="runtime-1-env-0-was" value="API_KEY"' in saved
                reply = await app.state.controls.ask(
                    enrolment.computer.id,
                    {"read": "setting", "agent_id": AGENT_ID, "key": "review.reply"},
                )
                assert reply is not None and reply["result"]["value"] == "One moment."
                async with asyncio.timeout(10):
                    while True:
                        _, listing = await _get(
                            session, f"{base}/agents/list", **headers
                        )
                        if "Renamed" in listing:
                            break
                        await asyncio.sleep(0.1)

                # case: let go, the module comes back with nothing open, and
                # the computer runs it no more
                status, ask = await _get(session, f"{page_url}/remove", **headers)
                assert status == 200 and 'id="agent-remove"' in ask
                async with session.delete(
                    f"{base}/agents/{key}", headers={**headers, "HX-Request": "true"}
                ) as response:
                    assert response.status == 200
                    assert response.headers["HX-Push-Url"] == "/agents"
                assert all(agent.id != AGENT_ID for agent in node.configuration.agents)
        finally:
            await node.stop()
