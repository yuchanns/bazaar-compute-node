from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from ._node import node_reporting_to
from ._serving import enrol, serving_app, signed_in
from .test_pages import _get, _short


@pytest.mark.asyncio
async def test_an_agent_is_taken_in_from_the_computer_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tab under the computer's box opens the form at once; what the
    computer has is asked for where the form first wants it. Filled in, the
    computer takes the agent in and the box shows it. A configuration the computer turns down keeps the form up,
    as it was filled in, saying so."""

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

            async with signed_in(base, storage) as session:
                headers = {"Accept-Language": "en"}
                computer = await _short(storage, enrolment.computer.id)

                # case: the form opens with its stacks' blank pages to be
                # filled in from the computer it names
                status, form = await _get(
                    session, f"{base}/computers/{computer}/agents/new", **headers
                )
                assert status == 200 and '<div class="veil" id="agent-new"' in form
                # the button that sends it is held while the computer answers
                assert 'hx-disable="find [data-done]"' in form
                assert f'data-computer="/computers/{computer}"' in form
                assert 'data-blank="channel"' in form and 'data-blank="runtime"' in form

                # case: a blank page offers the kinds of its family the
                # computer has, each with the card it starts; a runtime with
                # its version
                status, channels = await _get(
                    session, f"{base}/computers/{computer}/kinds/channel", **headers
                )
                assert status == 200 and 'data-add="channel-test"' in channels
                assert '<template id="card-channel-test">' in channels
                status, runtimes = await _get(
                    session, f"{base}/computers/{computer}/kinds/runtime", **headers
                )
                assert status == 200 and 'data-add="runtime-test"' in runtimes
                assert '<span class="ver">1.2.3</span>' in runtimes
                assert '<template id="card-runtime-test">' in runtimes
                status, _ = await _get(
                    session, f"{base}/computers/{computer}/kinds/elsewhere", **headers
                )
                assert status == 404

                # case: a runtime's models, as the options of its field, each
                # with the efforts it takes
                status, options = await _get(
                    session, f"{base}/computers/{computer}/models/test", **headers
                )
                assert status == 200
                assert '<option value="test-model" data-efforts="low high">' in options
                # one the computer does not have is said so, for the field
                # to offer asking again
                status, missing = await _get(
                    session, f"{base}/computers/{computer}/models/elsewhere", **headers
                )
                assert status == 502 and "TARGET_NOT_FOUND" in missing

                # case: filled in and sent, the computer runs the agent, and
                # the form goes, telling the box to look again
                filled = {
                    "name": "Newcomer",
                    "mode": "session",
                    "idle_timeout": "0",
                    "channel-0-kind": "test",
                    "runtime-1-kind": "test",
                    "runtime-1-model": "test-model",
                    "runtime-1-effort": "high",
                    "runtime-1-sandbox_mode": "workspace-write",
                    "runtime-1-network_access": "on",
                    "runtime-1-env-0-name": "CODEX_HOME",
                    "runtime-1-env-0-value": "/srv/codex",
                    "runtime-1-env-1-name": "",
                    "runtime-1-env-1-value": "",
                    "reply": "Hold on, someone will be with you.",
                }
                async with session.post(
                    f"{base}/computers/{computer}/agents", data=filled, headers=headers
                ) as response:
                    assert response.status == 200
                    assert response.headers["HX-Trigger"] == "agents-changed"
                    assert (
                        await response.text()
                    ).strip() == '<div id="agent-new" hidden></div>'
                newcomer = next(
                    agent
                    for agent in node.configuration.agents
                    if agent.name == "Newcomer"
                )
                assert node.agents[newcomer.id].started
                runtime = newcomer.runtimes[0]
                assert (runtime.model, runtime.effort) == ("test-model", "high")
                assert runtime.network_access is True
                # the value is kept by the node, under a name it gives
                kept = f"BCN_{newcomer.id.replace('-', '_').upper()}_RUNTIME0_TEST_ENV_CODEX_HOME"
                assert runtime.env == {"CODEX_HOME": kept}
                assert os.environ[kept] == "/srv/codex"
                # what it tells those waiting was kept on the agent too
                kept = await app.state.controls.ask(
                    enrolment.computer.id,
                    {
                        "read": "setting",
                        "agent_id": newcomer.id,
                        "key": "review.reply",
                    },
                )
                assert kept is not None
                assert kept["result"]["value"] == "Hold on, someone will be with you."

                async with asyncio.timeout(10):
                    while True:
                        _, pane = await _get(
                            session, f"{base}/computers/{computer}/detail", **headers
                        )
                        if "Newcomer" in pane.split('<div class="roster">')[1]:
                            break
                        await asyncio.sleep(0.1)

                # case: one the computer will not have keeps the form up, as
                # filled in, and nothing is added
                async with session.post(
                    f"{base}/computers/{computer}/agents",
                    data={**filled, "name": "Broken", "mode": "nonsense"},
                    headers=headers,
                ) as response:
                    refused = await response.text()
                assert '<div class="veil" id="agent-new"' in refused
                assert "The configuration was rejected (REFUSED)." in refused
                assert 'value="Broken"' in refused
                assert all(
                    agent.name != "Broken" for agent in node.configuration.agents
                )

                # case: one with no runtime is not sent at all, and the form
                # says what it lacks
                async with session.post(
                    f"{base}/computers/{computer}/agents",
                    data={
                        key: value
                        for key, value in {**filled, "name": "Idle"}.items()
                        if not key.startswith("runtime-")
                    },
                    headers=headers,
                ) as response:
                    lacking = await response.text()
                assert "Add at least one runtime." in lacking
                assert all(agent.name != "Idle" for agent in node.configuration.agents)
        finally:
            await node.stop()
