from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp
import aiosqlite
import pytest

from bazaar_compute_server.clock import LOCAL, clock_text, now_ms, start_of_today_ms
from bazaar_compute_server.fleet import PAGE_SIZE
from bazaar_compute_server.protocol import Event
from bazaar_compute_server.rendering import identicon

from ._serving import serving, signed_in


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
            "system": "Linux",
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
    async with (
        serving(tmp_path) as (base, storage),
        signed_in(base, storage) as session,
    ):
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

        # case: a computer wears its system as its mark, and says it in words
        status, detail = await _get(
            session, f"{base}/computers", **{"Accept-Language": "zh-CN"}
        )
        assert status == 200 and 'class="av mark"' in detail and ">Linux<" in detail
        assert "Python" not in detail

        # case: the lists and the detail pane ask for themselves again, keeping
        # the selected row, so what they show follows the computers
        cid = enrolment.computer.id
        status, fragment = await _get(
            session, f"{base}/agents/list?selected={cid}/agent-1"
        )
        assert (
            status == 200
            and 'id="agent-list"' in fragment
            and 'hx-swap="outerMorph"' in fragment
        )
        assert 'class="li agent on"' in fragment and "<html" not in fragment
        status, fragment = await _get(session, f"{base}/computers/list?selected={cid}")
        assert status == 200 and 'id="computer-list"' in fragment and "on" in fragment
        status, fragment = await _get(session, f"{base}/computers/{cid}/detail")
        assert (
            status == 200
            and 'id="computer-view"' in fragment
            and "有马佳奈" in fragment
        )

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
    async with (
        serving(tmp_path) as (base, storage),
        signed_in(base, storage) as session,
    ):
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

        # case: the same session keeps running past midnight; its running total
        # counts for today only by what it grew, a fresh session in full
        grown = _event(4, "usage.updated", "agent-1", total={"total_tokens": 5234})
        grown["created_at_ms"] = starts[late] + 60_000
        fresh = _event(
            5,
            "usage.updated",
            "agent-1",
            total={"total_tokens": 2000},
            runtime_session_id="rs-2",
        )
        fresh["created_at_ms"] = starts[late] + 120_000
        await storage.record_events(
            enrolment.computer.id,
            "run-1",
            [Event.model_validate(item) for item in (grown, fresh)],
        )
        _, on_late = await _get(session, url, **{"X-Timezone": str(late)})
        assert "6K token" in on_late

        # case: the count reads in K, M, B and T as it grows
        huge = _event(
            6,
            "usage.updated",
            "agent-1",
            total={"total_tokens": 2_500_000_000},
            runtime_session_id="rs-3",
        )
        huge["created_at_ms"] = starts[late] + 180_000
        await storage.record_events(
            enrolment.computer.id, "run-1", [Event.model_validate(huge)]
        )
        _, on_late = await _get(session, url, **{"X-Timezone": str(late)})
        assert "2.5B token" in on_late
        vast = _event(
            7,
            "usage.updated",
            "agent-1",
            total={"total_tokens": 3_000_000_000_000},
            runtime_session_id="rs-4",
        )
        vast["created_at_ms"] = starts[late] + 240_000
        await storage.record_events(
            enrolment.computer.id, "run-1", [Event.model_validate(vast)]
        )
        _, on_late = await _get(session, url, **{"X-Timezone": str(late)})
        assert "3.0T token" in on_late


@pytest.mark.asyncio
async def test_a_long_turn_still_reads_as_busy(tmp_path: Path) -> None:
    """The turn boundary is what counts, however many events came after it."""

    async with (
        serving(tmp_path) as (base, storage),
        signed_in(base, storage) as session,
    ):
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
    async with (
        serving(tmp_path) as (base, storage),
        signed_in(base, storage) as session,
    ):
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
        async with aiosqlite.connect(tmp_path / "bcs.sqlite3") as connection:
            await connection.execute(
                "UPDATE events SET received_at_ms = ?", (now_ms() - 10 * 60_000,)
            )
            await connection.commit()

        status, page = await _get(
            session, f"{base}/computers", **{"Accept-Language": "en"}
        )
        assert status == 200
        assert 'class="dot offline"' in page
        # case: an offline computer is greyed but still opens: removing it
        # happens from its page
        assert '<a class="li off on"' in page
        assert f'href="/computers/{enrolment.computer.id}"' in page
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
    async with (
        serving(tmp_path) as (base, storage),
        signed_in(base, storage) as session,
    ):
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
        # case: the snippet carries the token and the server's own address,
        # once for either kind of machine, open on the browser's own kind
        assert f"bcn server connect --url {base} --token {token_prefix}" in page
        assert "仅显示一次" in page
        assert "install.sh | sh" in page and "install.ps1 | iex" in page
        assert 'id="snippet-unix" checked' in page
        async with session.post(
            f"{base}/computers",
            data={"name": "ie"},
            headers={"HX-Request": "true", "Sec-CH-UA-Platform": '"Windows"'},
        ) as response:
            assert 'id="snippet-windows" checked' in await response.text()

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


@pytest.mark.asyncio
async def test_a_computer_is_removed_with_everything_it_said(tmp_path: Path) -> None:
    async with (
        serving(tmp_path) as (base, storage),
        signed_in(base, storage) as session,
    ):
        enrolment = await storage.add_computer("old")
        computer_id = enrolment.computer.id
        await storage.record_events(
            computer_id, "run-1", [Event.model_validate(_health([], 1))]
        )

        # case: the button asks first, then the computer and its events go
        status, question = await _get(
            session,
            f"{base}/computers/{computer_id}/remove",
            **{"Accept-Language": "zh-CN"},
        )
        assert status == 200 and f'hx-delete="/computers/{computer_id}"' in question
        async with session.delete(
            f"{base}/computers/{computer_id}", headers={"HX-Request": "true"}
        ) as response:
            assert response.status == 200
            assert response.headers["HX-Push-Url"] == "/computers"
            assert "old" not in await response.text()
        assert await storage.list_computers(limit=10) == []
        assert await storage.count_events(computer_id) == 0

        # case: removing it again is nothing
        async with session.delete(f"{base}/computers/{computer_id}") as response:
            assert response.status == 404


def _rows(html: str) -> int:
    return len(re.findall(r'class="li[ "]', html))


@pytest.mark.asyncio
async def test_long_lists_come_a_page_at_a_time_as_the_end_scrolls_in(
    tmp_path: Path,
) -> None:
    async with (
        serving(tmp_path) as (base, storage),
        signed_in(base, storage) as session,
    ):
        ids = []
        for index in range(PAGE_SIZE + 5):
            enrolment = await storage.add_computer(f"computer-{index}")
            ids.append(enrolment.computer.id)
            await storage.record_events(
                enrolment.computer.id,
                "run-1",
                [
                    Event.model_validate(
                        _health(
                            [
                                {
                                    "agent_id": f"agent-{index}",
                                    "name": str(index),
                                    "status": "started",
                                }
                            ],
                            1,
                        )
                    )
                ],
            )
        edge = ids[PAGE_SIZE - 1]

        # case: a page shows the first computers and where the next page starts
        for url in ("/agents", "/computers"):
            _, html = await _get(session, base + url)
            assert f'data-after="{edge}"' in html, url
        for url in ("/agents/list", "/computers/list"):
            _, html = await _get(session, base + url)
            assert _rows(html) == PAGE_SIZE, url
            assert f'data-after="{edge}"' in html, url

        # case: the page past the edge is the rest; it ends at the last computer
        # and says so without asking for more
        for url in ("/agents/list", "/computers/list"):
            _, html = await _get(session, f"{base}{url}?after={edge}")
            assert _rows(html) == 5, url
            assert f'data-after="{ids[-1]}"' in html, url
            assert "revealed" not in html, url

        # case: the refresh brings back everything loaded so far, edge included
        for url in ("/agents/list", "/computers/list"):
            _, html = await _get(session, f"{base}{url}?until={edge}")
            assert _rows(html) == PAGE_SIZE, url
            assert f'data-after="{edge}"' in html and "revealed" in html, url
            _, html = await _get(session, f"{base}{url}?until={ids[-1]}")
            assert _rows(html) == PAGE_SIZE + 5, url
            assert f'data-after="{ids[-1]}"' in html, url
            assert "revealed" not in html, url

        # case: a computer on a later page still has its own pages
        last = ids[-1]
        status, html = await _get(session, f"{base}/computers/{last}")
        assert status == 200 and f"computer-{PAGE_SIZE + 4}" in html
        status, _ = await _get(session, f"{base}/agents/{last}/agent-{PAGE_SIZE + 4}")
        assert status == 200
        status, _ = await _get(
            session, f"{base}/agents/{last}/agent-{PAGE_SIZE + 4}/activity"
        )
        assert status == 200
