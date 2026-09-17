from __future__ import annotations

from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp
import pytest

from bazaar_compute_server.clock import LOCAL, clock_text, now_ms, start_of_today_ms
from bazaar_compute_server.protocol import Event
from bazaar_compute_server.rendering import identicon

from ._serving import serving


def _health(agents: list[dict[str, Any]], seq: int) -> dict[str, Any]:
    return {
        "seq": seq,
        "event_name": "node.health",
        "state": "completed",
        "created_at_ms": now_ms(),
        "correlation": {},
        "metadata": {
            "ready": True,
            "agents": agents,
            "version": "0.2.1rc2",
            "python": "3.14.7",
            "interval_ms": 60_000,
            "audit": {"name": "server", "queued": 0},
        },
    }


def _event(seq: int, event_name: str, agent_id: str, **fields: Any) -> dict[str, Any]:
    return {
        "seq": seq,
        "event_name": event_name,
        "state": "completed",
        "created_at_ms": now_ms(),
        "correlation": {
            "node_id": agent_id,
            "thread_id": "thread-1",
            "runtime_session_id": fields.pop("runtime_session_id", "rs-1"),
        },
        "metadata": fields,
    }


async def _get(
    session: aiohttp.ClientSession, url: str, **headers: str
) -> tuple[int, str]:
    async with session.get(url, headers=headers) as response:
        return response.status, await response.text()


@pytest.mark.asyncio
async def test_the_agents_module_lists_what_computers_report(tmp_path: Path) -> None:
    async with serving(tmp_path) as (base, storage), aiohttp.ClientSession() as session:
        enrolment = await storage.add_computer("kana")
        agents = [
            {
                "agent_id": "agent-1",
                "name": "有马佳奈",
                "status": "started",
                "channels": ["telegram", "lark"],
                "runtimes": ["claudecode"],
            }
        ]
        await storage.record_events(
            enrolment.computer.id,
            "run-1",
            [
                Event.model_validate(item)
                for item in (
                    _health(agents, 1),
                    _event(
                        2,
                        "channel.inbound.persisted",
                        "agent-1",
                        text="hi",
                        target="group:1",
                        target_name="B小町 #bcn",
                    ),
                    _event(3, "runtime.request.turn.started", "agent-1"),
                    _event(4, "tool_call.started", "agent-1", name="Bash"),
                    _event(
                        5,
                        "usage.updated",
                        "agent-1",
                        total={"total_tokens": 1234},
                        cost_usd=0.5,
                    ),
                )
            ],
        )

        # case: the full page carries the shell and the module, in Chinese
        status, page = await _get(
            session, f"{base}/agents", **{"Accept-Language": "zh-CN"}
        )
        assert status == 200
        assert "<html" in page and 'href="/static/htmx.min.js"' not in page
        assert '<script src="/static/htmx.min.js">' in page
        assert "智能体" in page and "有马佳奈" in page
        assert 'class="dot busy"' in page

        # case: a running agent can be opened; its name heads the right column
        status, opened = await _get(
            session, f"{base}/agents/{enrolment.computer.id}/agent-1"
        )
        assert status == 200 and opened.count("有马佳奈") >= 2
        status, _ = await _get(session, f"{base}/agents/{enrolment.computer.id}/nobody")
        assert status == 404

        # case: an htmx request gets only the fragment
        status, fragment = await _get(
            session, f"{base}/agents", **{"HX-Request": "true", "Accept-Language": "en"}
        )
        assert "<html" not in fragment and "Agents" in fragment

        # case: the activity card reads as words, with the turn and today's usage
        status, card = await _get(
            session,
            f"{base}/agents/{enrolment.computer.id}/agent-1/activity",
            **{"Accept-Language": "zh-CN"},
        )
        assert status == 200
        assert "正在处理 B小町 #bcn" in card
        assert "开始工具调用 · Bash" in card
        assert "消息已接收 · B小町 #bcn" in card
        assert "用量已更新 · 1,234" in card
        assert "今日用量：1K token · $0.50" in card
        assert "tool_call.started" not in card

        # case: the static assets the shell needs are served
        status, script = await _get(session, f"{base}/static/htmx.min.js")
        assert status == 200 and '"4.0.0"' in script
        status, _ = await _get(session, f"{base}/static/app.css")
        assert status == 200


@pytest.mark.asyncio
async def test_the_card_keeps_the_viewers_clock(tmp_path: Path) -> None:
    """Times and "today" follow the zone the browser sends, not the server's."""

    tokyo, honolulu = ZoneInfo("Asia/Tokyo"), ZoneInfo("Pacific/Honolulu")
    # a minute before the later of the two midnights: yesterday on that side
    # of the ocean, today on the other, whatever the hour is now
    starts = {tokyo: start_of_today_ms(tokyo), honolulu: start_of_today_ms(honolulu)}
    late, early = sorted(starts, key=lambda zone: starts[zone], reverse=True)
    moment = starts[late] - 60_000
    async with serving(tmp_path) as (base, storage), aiohttp.ClientSession() as session:
        enrolment = await storage.add_computer("kana")
        agents = [{"agent_id": "agent-1", "name": "IE", "status": "started"}]
        turn = _event(2, "runtime.request.turn.started", "agent-1")
        turn["created_at_ms"] = moment
        usage = _event(3, "usage.updated", "agent-1", total={"total_tokens": 1234})
        usage["created_at_ms"] = moment
        await storage.record_events(
            enrolment.computer.id,
            "run-1",
            [Event.model_validate(item) for item in (_health(agents, 1), turn, usage)],
        )
        url = f"{base}/agents/{enrolment.computer.id}/agent-1/activity"
        _, on_early = await _get(session, url, **{"X-Timezone": str(early)})
        _, on_late = await _get(session, url, **{"X-Timezone": str(late)})
        _, unsaid = await _get(session, url, **{"X-Timezone": "Mars/Olympus"})

        assert "1K token" in on_early and "0 token" in on_late
        assert clock_text(moment, early) in on_early
        assert clock_text(moment, late) in on_late
        assert clock_text(moment, LOCAL) in unsaid


@pytest.mark.asyncio
async def test_a_long_turn_still_reads_as_busy(tmp_path: Path) -> None:
    """The turn boundary is what counts, however many events came after it."""

    async with serving(tmp_path) as (base, storage), aiohttp.ClientSession() as session:
        enrolment = await storage.add_computer("kana")
        agents = [{"agent_id": "agent-1", "name": "IE", "status": "started"}]
        await storage.record_events(
            enrolment.computer.id,
            "run-1",
            [
                Event.model_validate(item)
                for item in (
                    _health(agents, 1),
                    _event(2, "runtime.request.turn.started", "agent-1"),
                    *(
                        _event(seq, "tool_call.completed", "agent-1", name="Read")
                        for seq in range(3, 20)
                    ),
                )
            ],
        )
        _, page = await _get(session, f"{base}/agents")
        assert 'class="dot busy"' in page


@pytest.mark.asyncio
async def test_a_silent_computer_shows_offline(tmp_path: Path) -> None:
    async with serving(tmp_path) as (base, storage), aiohttp.ClientSession() as session:
        enrolment = await storage.add_computer("ie")
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
        # age the report itself past two beats
        from bazaar_compute_server.contrib.sqlite.storage import SqliteStorage

        assert isinstance(storage, SqliteStorage)
        await storage._db.execute(
            "UPDATE events SET received_at_ms = ?", (now_ms() - 10 * 60_000,)
        )
        await storage._db.commit()

        status, page = await _get(
            session, f"{base}/computers", **{"Accept-Language": "en"}
        )
        assert status == 200
        assert 'class="dot offline"' in page
        # case: an offline row is greyed and leads nowhere; there is nothing
        # live behind it
        assert '<div class="li on off"' in page
        assert f'href="/computers/{enrolment.computer.id}"' not in page
        status, agents = await _get(
            session, f"{base}/agents", **{"Accept-Language": "en"}
        )
        assert 'class="dot offline"' in agents
        assert 'class="li agent off"' in agents
        assert f'href="/agents/{enrolment.computer.id}/a"' not in agents


@pytest.mark.asyncio
async def test_a_computer_is_added_from_the_page_and_the_token_shown_once(
    tmp_path: Path,
) -> None:
    async with serving(tmp_path) as (base, storage), aiohttp.ClientSession() as session:
        status, empty = await _get(
            session, f"{base}/computers", **{"Accept-Language": "zh-CN"}
        )
        assert status == 200 and "尚未接入电脑" in empty

        status, form = await _get(session, f"{base}/computers/new")
        assert status == 200 and 'name="name"' in form

        async with session.post(
            f"{base}/computers",
            data={"name": "kana"},
            headers={"HX-Request": "true", "Accept-Language": "zh-CN"},
        ) as response:
            assert response.status == 200
            page = await response.text()
        computers = await storage.list_computers(limit=10)
        assert [item.name for item in computers] == ["kana"]
        token_prefix = f"{computers[0].id}:"
        # case: the snippet carries the token and the server's own address
        assert f"bcn server connect --url {base} --token {token_prefix}" in page
        assert "仅显示一次" in page

        # case: the token is not shown again anywhere
        status, again = await _get(session, f"{base}/computers/{computers[0].id}")
        assert status == 200 and token_prefix not in again and "kana" in again


def test_identicons_are_stable_symmetric_marks() -> None:
    mark = identicon("有马佳奈")
    assert mark == identicon("有马佳奈")
    assert mark != identicon("IE")
    assert mark.startswith('<svg viewBox="0 0 5 5"')
    # every left-hand pixel has its mirror
    import re

    cells = {tuple(map(int, m)) for m in re.findall(r'x="(\d)" y="(\d)"', mark)}
    assert all((4 - x, y) in cells for x, y in cells)
