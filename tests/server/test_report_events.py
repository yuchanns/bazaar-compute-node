from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import aiohttp
import aiosqlite
import pytest

from bazaar_compute_server.contrib.sqlite.storage import SqliteStorage
from bazaar_compute_server.protocol import PROTOCOL_HEADER, PROTOCOL_VERSION, Event

from ._serving import serving, signed_in


def _event(
    seq: int, name: str = "runtime.turn.completed", **metadata: Any
) -> dict[str, Any]:
    return {
        "seq": seq,
        "event_name": name,
        "state": "completed",
        "created_at_ms": 1_000 + seq,
        "correlation": {
            "node_id": "agent-1",
            "thread_id": "thread-1",
            "runtime_session_id": "rs-1",
        },
        "metadata": metadata,
    }


def _headers(token: str, protocol: str = str(PROTOCOL_VERSION)) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", PROTOCOL_HEADER: protocol}


async def _post(
    session: aiohttp.ClientSession, base: str, token: str, body: Any, **kw: Any
) -> tuple[int, Any]:
    async with session.post(
        f"{base}/node/reportEvents", json=body, headers=_headers(token, **kw)
    ) as response:
        return response.status, await response.json()


@pytest.mark.asyncio
async def test_an_enrolled_computer_can_report_and_duplicates_are_ignored(
    tmp_path: Path,
) -> None:
    async with (
        serving(tmp_path) as (base, storage),
        signed_in(base, storage) as session,
    ):
        enrolment = await storage.add_computer("kana")
        computer_id, _, secret = enrolment.token.partition(":")
        assert computer_id == enrolment.computer.id
        assert secret not in json.dumps(
            await storage.list_computers(limit=10), default=str
        )

        status, reply = await _post(
            session,
            base,
            enrolment.token,
            {"run_id": "run-1", "events": [_event(1), _event(2, text="hi")]},
        )
        # case: the reply follows the envelope and counts what was new
        assert status == 200
        assert reply == {
            "ok": True,
            "result": {"accepted": 2},
            "protocol": PROTOCOL_VERSION,
        }

        # case: a retried batch is not stored twice, a new event is
        status, reply = await _post(
            session,
            base,
            enrolment.token,
            {"run_id": "run-1", "events": [_event(1), _event(2), _event(3)]},
        )
        assert reply["result"] == {"accepted": 1}
        assert await storage.count_events(enrolment.computer.id) == 3

        # case: a new run starts its sequence over without colliding
        status, reply = await _post(
            session, base, enrolment.token, {"run_id": "run-2", "events": [_event(1)]}
        )
        assert reply["result"] == {"accepted": 1}
        assert await storage.count_events(enrolment.computer.id) == 4


@pytest.mark.asyncio
async def test_only_a_known_token_on_a_known_protocol_gets_in(tmp_path: Path) -> None:
    async with (
        serving(tmp_path) as (base, storage),
        signed_in(base, storage) as session,
    ):
        enrolment = await storage.add_computer("kana")
        other = await storage.add_computer("ie")
        body = {"run_id": "run-1", "events": [_event(1)]}

        # case: no token, a made-up token, the wrong secret for a real computer
        for token in ("", "nope", f"{enrolment.computer.id}:wrong", other.token + "x"):
            status, reply = await _post(session, base, token, body)
            assert status == 401
            assert reply["error_code"] == "unauthorized"
        assert await storage.count_events(enrolment.computer.id) == 0

        # case: a protocol this server does not speak
        status, reply = await _post(session, base, enrolment.token, body, protocol="2")
        assert status == 400
        assert reply["error_code"] == "unsupported_protocol"

        # case: a body that is not a report
        status, reply = await _post(
            session, base, enrolment.token, {"events": [{"seq": 0}]}
        )
        assert status == 400
        assert reply["error_code"] == "invalid_request"

        # case: a name longer than a row shows is kept cut, not refused
        long_name = _event(1, "node.health")
        long_name["metadata"] = {
            "agents": [{"agent_id": "a", "name": "n" * 500, "status": "started"}]
        }
        status, _ = await _post(
            session, base, enrolment.token, {"run_id": "run-1", "events": [long_name]}
        )
        assert status == 200
        health = await storage.computer_health([enrolment.computer])
        assert health[0].health is not None
        assert health[0].health.payload["metadata"]["agents"][0]["name"] == "n" * 100

        # case: an agent id that could not be one: it ends up in element ids,
        # css anchors and urls, so the door is where it is refused
        for bad in ('x" onmouseover="alert(1)', "a b", "x" * 65, 7):
            event = _event(1)
            event["correlation"]["node_id"] = bad
            status, reply = await _post(
                session, base, enrolment.token, {"run_id": "run-1", "events": [event]}
            )
            assert status == 400, bad
            assert reply["error_code"] == "invalid_request"

        # case: an event the pages read into, shaped wrong
        for name, metadata in (
            ("node.health", {"agents": 1}),
            ("node.health", {"agents": [{"agent_id": "a", "name": "A"}]}),
            ("usage.updated", {"total": {"total_tokens": "many"}}),
            ("usage.updated", {"total": {"total_tokens": 2**63}}),
            # a number spelled as text would pass a lenient check and be kept as text
            ("node.health", {"interval_ms": "60000"}),
        ):
            status, reply = await _post(
                session,
                base,
                enrolment.token,
                {"run_id": "run-1", "events": [_event(1, name, **metadata)]},
            )
            assert status == 400, (name, metadata)
            assert reply["error_code"] == "invalid_request"

        # case: a time the pages could not show
        for when in (-1, 10**100):
            event = _event(1)
            event["created_at_ms"] = when
            status, reply = await _post(
                session, base, enrolment.token, {"run_id": "run-1", "events": [event]}
            )
            assert status == 400, when
            assert reply["error_code"] == "invalid_request"

        # case: a body bigger than one report may be
        status, reply = await _post(
            session,
            base,
            enrolment.token,
            {"run_id": "run-1", "events": [_event(1, text="x" * (1024 * 1024))]},
        )
        assert status == 413
        assert reply["error_code"] == "invalid_request"

        # case: the right token still works after all that
        status, _ = await _post(session, base, enrolment.token, body)
        assert status == 200


@pytest.mark.asyncio
async def test_retention_keeps_recent_events_and_the_last_health_beat(
    tmp_path: Path,
) -> None:
    storage = SqliteStorage(tmp_path / "bcs.sqlite3", retention_days=1)
    await storage.start()
    try:
        enrolment = await storage.add_computer("kana")
        computer_id = enrolment.computer.id
        events = [
            _event(1, "node.health", ready=True),
            _event(2, "runtime.turn.completed"),
            _event(3, "node.health", ready=False),
            _event(4, "usage.updated", total={"total_tokens": 10}),
            _event(5, "usage.updated", total={"total_tokens": 20}),
        ]
        await storage.record_events(
            computer_id, "run-1", [Event.model_validate(item) for item in events]
        )
        # case: only the newest health beat survives a report
        assert await storage.count_events(computer_id) == 4

        # age everything past the retention; the session then speaks once more
        async with aiosqlite.connect(tmp_path / "bcs.sqlite3") as connection:
            await connection.execute("UPDATE events SET received_at_ms = 0")
            await connection.commit()
        await storage.record_events(
            computer_id, "run-1", [Event.model_validate(_event(6, "tool_call.started"))]
        )
        await storage.sweep()

        # case: the last health beat and the last usage of a session still
        # heard from, which today's count is taken from, outlive the retention
        async with (
            aiosqlite.connect(tmp_path / "bcs.sqlite3") as connection,
            connection.execute(
                "SELECT seq, event_name FROM events WHERE computer_id = ? ORDER BY seq",
                (computer_id,),
            ) as cursor,
        ):
            remaining = [tuple(row) async for row in cursor]
        assert remaining == [
            (3, "node.health"),
            (5, "usage.updated"),
            (6, "tool_call.started"),
        ]

        # case: once the session has been silent for the whole retention, its
        # usage goes too; only the health beat stays
        async with aiosqlite.connect(tmp_path / "bcs.sqlite3") as connection:
            await connection.execute("UPDATE events SET received_at_ms = 0")
            await connection.commit()
        await storage.sweep()
        async with (
            aiosqlite.connect(tmp_path / "bcs.sqlite3") as connection,
            connection.execute(
                "SELECT event_name FROM events WHERE computer_id = ?", (computer_id,)
            ) as cursor,
        ):
            assert [row[0] async for row in cursor] == ["node.health"]
    finally:
        await storage.stop()


@pytest.mark.asyncio
async def test_computers_come_in_pages_that_survive_new_enrolments(
    tmp_path: Path,
) -> None:
    storage = SqliteStorage(tmp_path / "bcs.sqlite3", retention_days=30)
    await storage.start()
    try:
        names = [f"computer-{index}" for index in range(5)]
        for name in names:
            await storage.add_computer(name)

        first = await storage.list_computers(limit=2)
        assert [item.name for item in first] == names[:2]

        # a computer enrolled between pages does not shift what comes next
        await storage.add_computer("latecomer")
        second = await storage.list_computers(limit=2, after=first[-1].id)
        assert [item.name for item in second] == names[2:4]
        rest = await storage.list_computers(limit=10, after=second[-1].id)
        assert [item.name for item in rest] == [names[4], "latecomer"]

        # case: everything loaded so far, asked for again as one stretch
        loaded = await storage.list_computers(until=second[-1].id)
        assert [item.name for item in loaded] == names[:4]
        assert await storage.find_computer(first[0].id) == first[0]
        assert await storage.find_computer("nope") is None
    finally:
        await storage.stop()


@pytest.mark.asyncio
async def test_a_caller_that_stops_waiting_does_not_stop_the_writer(
    tmp_path: Path,
) -> None:
    storage = SqliteStorage(tmp_path / "bcs.sqlite3", retention_days=30)
    await storage.start()
    try:
        enrolment = await storage.add_computer("gone")
        # case: the request goes away while its write is queued; the write
        # still lands, and the next one is served
        abandoned = asyncio.create_task(storage.remove_computer(enrolment.computer.id))
        await asyncio.sleep(0)
        abandoned.cancel()
        await asyncio.gather(abandoned, return_exceptions=True)
        await storage.add_computer("next")
        assert [item.name for item in await storage.list_computers()] == ["next"]
    finally:
        await storage.stop()
