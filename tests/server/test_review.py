from __future__ import annotations

import asyncio
import re
from pathlib import Path
from uuid import uuid7

import pytest

from bazaar_compute_node.core.models import Message, MessageDirection, SenderIdentity
from bazaar_compute_node.core.reminder import ReminderScheduleRequest
from bazaar_compute_node.core.utils.clock import now_ms

from ._node import AGENT_ID, node_reporting_to
from ._serving import enrol, serving_app, signed_in
from .test_pages import _get, _short


@pytest.mark.asyncio
async def test_a_stranger_is_looked_at_let_in_and_turned_away_from_the_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stranger shows up on the contacts column's second tab, counted; the
    question about them opens on the right; let in, they move to the first
    tab and their chat opens with its card; turned away from the card, they
    are gone. What the agent says to those waiting is set from the column."""

    async with serving_app(tmp_path) as (base, app):
        storage = app.state.storage
        enrolment = await enrol(storage, "kana")
        monkeypatch.setenv("BCN_SERVER_TOKEN", enrolment.token)
        node = await node_reporting_to(base, tmp_path)
        try:
            agent = node.agents[AGENT_ID]
            anchor = str(uuid7())
            await agent.orchestrator.handle_inbound(_stranger(1, anchor))
            async with asyncio.timeout(10):
                while not (await storage.computer_health([enrolment.computer]))[
                    0
                ].health:
                    await asyncio.sleep(0.05)

            async with signed_in(base, storage) as session:
                headers = {"Accept-Language": "en"}
                key = await _short(storage, enrolment.computer.id, AGENT_ID)
                column = f"{base}/agents/{key}/contacts"

                # case: the first tab lists nobody, the second counts one; the
                # count goes to the tab, which stands outside the list
                status, chats = await _get(session, column, **headers)
                assert status == 200 and "No conversations." in chats
                assert (
                    '<span class="n" id="pending-review" hx-swap-oob="outerHTML">1</span>'
                    in chats
                )
                assert 'class="switch"' not in chats
                status, requests = await _get(
                    session, f"{column}?review=pending", **headers
                )
                assert status == 200 and 'id="contact-' in requests
                href = requests.split('href="')[1].split('"')[0].replace("&amp;", "&")
                assert href.endswith("&review=pending")

                # case: the row opens on the question, first message and all
                status, asked = await _get(
                    session, f"{base}{href}", **headers, **{"HX-Target": "div#chat"}
                )
                assert status == 200
                assert "First contact" in asked and "hello 1" in asked
                assert "Stranger wants to message Kana" in asked
                # opened as a page of its own, the column lists those waiting,
                # its tab the one lit
                status, whole = await _get(session, f"{base}{href}", **headers)
                assert status == 200
                assert "/contacts?review=pending&amp;selected=" in whole
                assert (
                    '<button class="tab on" type="button" title="New contacts"' in whole
                )
                review_url = (
                    asked.split('hx-post="')[1].split('"')[0].replace("&amp;", "&")
                )

                # case: let in, the chat opens; the tabs swap the row over
                async with session.post(
                    f"{base}{review_url}", data={"review": "approved"}, headers=headers
                ) as response:
                    assert response.status == 200
                    opened = await response.text()
                    pushed = response.headers["HX-Push-Url"]
                assert 'id="history"' in opened and "First contact" not in opened
                assert (
                    pushed.startswith(f"/agents/{key}/contacts/")
                    and "review" not in pushed
                )
                status, chats = await _get(session, column, **headers)
                assert 'id="contact-' in chats
                assert (
                    'id="pending-review" hx-swap-oob="outerHTML" hidden></span>'
                    in chats
                )

                # case: the card about the conversation lists its reminders,
                # each said as when it is for and how often
                for title, rule in (
                    ("follow up", None),
                    ("standup", "weekly:mon,fri@09:00"),
                ):
                    await agent.orchestrator.command_service.schedule_reminder(
                        agent.actors.for_thread("stranger"),
                        ReminderScheduleRequest(
                            title=title,
                            message_id=anchor,
                            next_fire_at_ms=now_ms() + 3_600_000,
                            repeat_rule=rule,
                            timezone="UTC",
                        ),
                    )
                profile_url = (
                    opened.split('class="av sm face"')[1]
                    .split('hx-get="')[1]
                    .split('"')[0]
                    .replace("&amp;", "&")
                )
                status, card = await _get(session, f"{base}{profile_url}", **headers)
                assert status == 200 and "Remove conversation" in card
                assert re.search(
                    r"follow up</b><small>(today|tomorrow) \d\d:\d\d<", card
                )
                assert "standup</b><small>weekly on Mon, Fri · " in card

                # case: a removal the node refuses leaves the chat as it was,
                # the question staying up with the answer
                remove_url = (
                    card.split('id="remove-ask"')[1]
                    .split('hx-post="')[1]
                    .split('"')[0]
                    .replace("&amp;", "&")
                )
                nobody = await _short(storage, "nobody")
                remove_url = re.sub(
                    r"/contacts/\d+/", f"/contacts/{nobody}/", remove_url
                )
                async with session.post(
                    f"{base}{remove_url}", data={"review": "denied"}, headers=headers
                ) as response:
                    assert response.status == 200
                    assert response.headers["HX-Retarget"] == "#remove-ask"
                    refused = await response.text()
                assert 'id="remove-ask"' in refused and 'class="err"' in refused

                # case: turned away from the card, the column is empty again
                # and the stranger waits once more when they write
                async with session.post(
                    f"{base}{review_url}", data={"review": "denied"}, headers=headers
                ) as response:
                    assert response.status == 200
                    assert 'id="history"' not in await response.text()
                    assert response.headers["HX-Push-Url"] == f"/agents/{key}"
                await agent.orchestrator.handle_inbound(_stranger(2, anchor))
                status, requests = await _get(
                    session, f"{column}?review=pending", **headers
                )
                assert 'id="contact-' in requests

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
