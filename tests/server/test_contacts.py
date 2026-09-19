from __future__ import annotations

import asyncio
from pathlib import Path

import aiosqlite
import pytest

from bazaar_compute_node.core.models import Message, MessageDirection, SenderIdentity
from bazaar_compute_server.clock import now_ms
from bazaar_compute_server.fleet import PAGE_SIZE
from bazaar_compute_server.protocol import Event

from ._node import AGENT_ID, node_reporting_to
from ._serving import enrol, serving_app, signed_in
from .test_pages import _get, _health


def _inbound(session_id: str, seq: int) -> Message:
    return Message(
        direction=MessageDirection.INBOUND,
        seq=seq,
        message_id=f"message-{session_id}-{seq}",
        thread_id=session_id,
        channel_session_id=f"channel-{session_id}",
        channel="test",
        provider_thread_id=f"thread-{session_id}",
        provider_message_id=f"provider-{session_id}-{seq}",
        received_at_ms=seq * 1_000,
        sender=SenderIdentity(id="sender-id", name="Sender"),
        message_type="text",
        target=f"dm:channel-{session_id}",
        body=f"inbound-{seq}",
        metadata={"sender_kind": "human"},
    )


@pytest.mark.asyncio
async def test_an_agents_conversations_are_listed_as_its_node_has_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with serving_app(tmp_path) as (base, app):
        storage = app.state.storage
        enrolment = await enrol(storage, "kana")
        monkeypatch.setenv("BCN_SERVER_TOKEN", enrolment.token)
        node = await node_reporting_to(base, tmp_path)
        try:
            computer_id = enrolment.computer.id
            node_storage = node.agents[AGENT_ID].storage
            # a page and two of conversations, the newest activity first
            for index in range(PAGE_SIZE + 2):
                await node_storage.record_inbound(
                    _inbound(f"session-{index:03d}", index + 1),
                    now_ms=(index + 1) * 1_000,
                )
            # the node's first beat has told the server about the agent
            async with asyncio.timeout(10):
                while not (await storage.computer_health([enrolment.computer]))[
                    0
                ].health:
                    await asyncio.sleep(0.05)

            async with signed_in(base, storage) as session:
                headers = {"Accept-Language": "en"}
                # case: the page opens without waiting for the node, and asks
                # for the column once it is there
                status, page = await _get(
                    session, f"{base}/agents/{computer_id}/{AGENT_ID}", **headers
                )
                assert status == 200
                assert (
                    f'hx-get="/agents/{computer_id}/{AGENT_ID}/contacts" hx-trigger="load"'
                    in page
                )

                # case: the column comes back from the node, a page of it,
                # newest activity first
                status, column = await _get(
                    session,
                    f"{base}/agents/{computer_id}/{AGENT_ID}/contacts",
                    **headers,
                )
                assert status == 200, column
                rows = column.split('<div class="li"')[1:]
                assert len(rows) == PAGE_SIZE
                assert f"channel-session-{PAGE_SIZE + 1:03d}" in rows[0]
                assert f"channel-session-{2:03d}" in rows[-1]
                assert "test · DM · " in rows[0]
                assert '<span class="badge">1</span>' in rows[0]
                assert f'data-after="{PAGE_SIZE}"' in column
                since = int(column.split("?since=")[1].split('"')[0])

                # case: the rows past the edge
                status, more = await _get(
                    session,
                    f"{base}/agents/{computer_id}/{AGENT_ID}/contacts?offset={PAGE_SIZE}",
                    **headers,
                )
                assert status == 200
                assert len(more.split('<div class="li"')[1:]) == 2
                assert "channel-session-000" in more
                assert 'hx-trigger="revealed"' not in more

                # case: nothing has arrived since, so the refresh is not asked
                # of the node at all
                assert node.control is not None
                served = node.control.health["served"]
                status, _ = await _get(
                    session,
                    f"{base}/agents/{computer_id}/{AGENT_ID}/contacts?since={since}&until={PAGE_SIZE}",
                    **headers,
                )
                assert status == 204
                assert node.control.health["served"] == served

                # case: a message arrives; the next refresh is answered with
                # the column as far as it was scrolled, past a newer `since`
                await storage.record_events(
                    computer_id,
                    "run-x",
                    [
                        Event.model_validate(
                            {
                                "seq": 1,
                                "event_name": "channel.inbound.persisted",
                                "state": "completed",
                                "created_at_ms": now_ms(),
                                "correlation": {
                                    "node_id": AGENT_ID,
                                    "thread_id": "session-000",
                                },
                                "metadata": {},
                            }
                        )
                    ],
                )
                status, refreshed = await _get(
                    session,
                    f"{base}/agents/{computer_id}/{AGENT_ID}/contacts?since={since}&until={PAGE_SIZE + 2}",
                    **headers,
                )
                assert status == 200
                assert len(refreshed.split('<div class="li"')[1:]) == PAGE_SIZE + 2
                assert int(refreshed.split("?since=")[1].split('"')[0]) > since
        finally:
            await node.stop()


@pytest.mark.asyncio
async def test_an_offline_computer_is_not_asked(tmp_path: Path) -> None:
    async with serving_app(tmp_path) as (base, app):
        storage = app.state.storage
        enrolment = await enrol(storage, "ie")
        stale = _health(
            [
                {
                    "agent_id": "a",
                    "name": "IE",
                    "status": "started",
                    "channels": [],
                    "runtimes": ["codex"],
                }
            ],
            1,
        )
        stale["created_at_ms"] = now_ms() - 10 * 60_000
        await storage.record_events(
            enrolment.computer.id, "run-1", [Event.model_validate(stale)]
        )
        async with aiosqlite.connect(tmp_path / "bcs.sqlite3") as connection:
            await connection.execute(
                "UPDATE events SET received_at_ms = ?", (now_ms() - 10 * 60_000,)
            )
            await connection.commit()
        async with signed_in(base, storage) as session:
            status, column = await _get(
                session,
                f"{base}/agents/{enrolment.computer.id}/a/contacts",
                **{"Accept-Language": "en"},
            )
            assert status == 200
            assert "This computer is offline" in column
