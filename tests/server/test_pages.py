from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp
import aiosqlite
import pytest

from bazaar_compute_server.clock import clock_text, local, now_ms, start_of_today_ms
from bazaar_compute_server.fleet import PAGE_SIZE
from bazaar_compute_server.protocol import Event
from bazaar_compute_server.rendering import identicon
from bazaar_compute_server.storage import IStorage

from ._serving import TESTER, enrol, root_id, serving, signed_in, with_password


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


async def _short(storage: IStorage, *values: str) -> str:
    """The values as a link names them: their numbers, joined with slashes."""

    return "/".join(str(number) for number in await storage.shorten(values))


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
        enrolment = await enrol(storage, "kana")
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
                        sender={
                            "id": "7",
                            "name": "hanchin",
                            "display_name": "Hanchin",
                        },
                    ),
                    _event(3, "runtime.request.turn.started", "agent-1"),
                    # a send is written down twice on the node; the card reads
                    # the delivery record and not the tool's echo of it
                    _event(6, "channel.outbound.sent", "agent-1"),
                    _event(7, "tool.bcc.message.send.sent", "agent-1"),
                    _event(4, "tool_call.started", "agent-1", name="Bash"),
                    _event(
                        5,
                        "usage.updated",
                        "agent-1",
                        total={
                            "input_tokens": 1000,
                            "output_tokens": 200,
                            "cached_input_tokens": 34,
                            "total_tokens": 1234,
                        },
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
        assert "<html" in page
        # case: a static file is named by its content, so a browser holding
        # the last one comes for the new one
        assert '<script src="/static/htmx.min.js?v=' in page
        css = page.split('href="/static/app.css?v=')[1].split('"')[0]
        async with session.get(f"{base}/static/app.css?v={css}") as response:
            assert response.status == 200
        # case: a page from another build asking for a piece is sent back for
        # the whole page; one from this build gets its piece
        build = page.split('"X-Build": "')[1].split('"')[0]
        async with session.get(
            f"{base}/agents/list",
            headers={"HX-Request": "true", "X-Build": "elsewhen"},
        ) as response:
            assert response.status == 204
            assert response.headers["HX-Trigger"] == "stale"
        assert 'id="stale" hidden' in page
        async with session.get(
            f"{base}/agents/list", headers={"HX-Request": "true", "X-Build": build}
        ) as response:
            assert response.status == 200
        assert "智能体" in page and "有马佳奈" in page
        assert 'class="dot busy"' in page

        # case: a running agent can be opened by the numbers its row carries;
        # its name heads the right column. a number that names nothing, or
        # anything that is not a number, is not there
        one = await _short(storage, enrolment.computer.id, "agent-1")
        assert f'href="/agents/{one}"' in page
        assert enrolment.computer.id not in page
        status, opened = await _get(session, f"{base}/agents/{one}")
        assert status == 200 and opened.count("有马佳奈") >= 2
        # case: the column's tabs stand above the list, lit at once on a click
        # and dropping a refresh of the list still on its way
        tabs = opened.split('<div class="switch">')[1].split('id="contacts"')[0]
        assert tabs.count('hx-sync="#contacts:replace"') == 2
        assert 'id="pending-review" hidden' in tabs
        status, _ = await _get(session, f"{base}/agents/{one.split('/')[0]}/999999")
        assert status == 404
        for odd in ("²", "9" * 30):
            status, _ = await _get(session, f"{base}/computers/{odd}")
            assert status == 404, odd
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
            f"{base}/agents/{one}/activity",
            **{"Accept-Language": "zh-CN"},
        )
        assert status == 200
        assert "正在处理 B小町 #bcn" in card
        assert "开始工具调用 · Bash" in card
        assert "消息已接收 · B小町 #bcn · Hanchin" in card
        assert card.count("消息已发送") == 1
        # the running total is summed up below the list, not read out in it
        assert "用量已更新" not in card
        assert "今日用量：输入 1K · 输出 200 · 缓存命中 34 · $0.50" in card
        assert "tool_call.started" not in card

        # case: a computer wears its system as its mark, named only in its tooltip
        status, detail = await _get(
            session, f"{base}/computers", **{"Accept-Language": "zh-CN"}
        )
        assert status == 200 and 'class="av mark"' in detail and ">Linux<" not in detail
        assert '<span class="os" title="Linux">' in detail
        assert "Python" not in detail

        # case: the lists and the detail pane ask for themselves again, keeping
        # the selected row, so what they show follows the computers
        cid = await _short(storage, enrolment.computer.id)
        status, fragment = await _get(session, f"{base}/agents/list?selected={one}")
        assert (
            status == 200
            and 'id="agent-list"' in fragment
            and 'hx-swap="outerMorph"' in fragment
        )
        assert 'class="li agent on"' in fragment and "<html" not in fragment
        # case: an agent's row is one line: the computer is its system's mark,
        # named only in the tooltip, and the status dot hangs off the row
        # itself, past the box its contents are clipped to
        assert '<span class="os" title="kana"><span class="i">' in fragment
        assert ">kana<" not in fragment and "<small>" not in fragment
        assert re.search(r'</span>\s*</div>\s*<div class="dot busy"', fragment)
        status, fragment = await _get(session, f"{base}/computers/list?selected={cid}")
        assert status == 200 and 'id="computer-list"' in fragment
        assert 'class="li on"' in fragment
        status, fragment = await _get(session, f"{base}/computers/{cid}/detail")
        assert (
            status == 200
            and 'id="computer-view"' in fragment
            and "有马佳奈" in fragment
        )
        # case: the computer's agents sit in a box, the way to add one off its corner
        roster = fragment.split('<div class="roster">')[1].split(
            '<div class="roster-foot">'
        )[0]
        assert "有马佳奈" in roster and 'class="icons"' in roster
        # the system is the computer's, said once in its header, not per agent
        assert 'class="os"' not in roster
        assert (
            '<span class="os" title="Linux"><span class="i">'
            in fragment.split('<div class="body">')[0]
        )
        assert 'class="roster-foot"><button class="add"' in fragment
        assert " disabled" not in fragment.split('<button class="add"')[1].split(">")[0]

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
        enrolment = await enrol(storage, "kana")
        agents = [{"agent_id": "agent-1", "name": "IE", "status": "started"}]
        turn = _event(2, "runtime.request.turn.started", "agent-1")
        turn["created_at_ms"] = moment
        usage = _event(
            3,
            "usage.updated",
            "agent-1",
            total={
                "input_tokens": 1000,
                "output_tokens": 234,
                "cached_input_tokens": 0,
            },
        )
        usage["created_at_ms"] = moment
        await storage.record_events(
            enrolment.computer.id,
            "run-1",
            [Event.model_validate(item) for item in (_health(agents, 1), turn, usage)],
        )
        url = f"{base}/agents/{await _short(storage, enrolment.computer.id, 'agent-1')}/activity"
        _, on_early = await _get(session, url, **{"X-Timezone": str(early)})
        _, on_late = await _get(session, url, **{"X-Timezone": str(late)})
        _, unsaid = await _get(session, url, **{"X-Timezone": "Mars/Olympus"})

        # nothing used today on the late side: the card says nothing of it
        assert "1K in · 234 out · 0 cached" in on_early and "Today:" not in on_late
        assert "$" not in on_early
        assert clock_text(moment, early) in on_early
        assert clock_text(moment, late) in on_late
        assert clock_text(moment, local()) in unsaid

        # case: the same session keeps running past midnight; its running total
        # counts for today only by what it grew, a fresh session in full; the
        # cost shows once a runtime that prices its tokens reports one
        grown = _event(
            4,
            "usage.updated",
            "agent-1",
            total={
                "input_tokens": 4000,
                "cache_write_input_tokens": 1000,
                "output_tokens": 234,
            },
        )
        grown["created_at_ms"] = starts[late] + 60_000
        fresh = _event(
            5,
            "usage.updated",
            "agent-1",
            total={"input_tokens": 2000, "cached_input_tokens": 300},
            cost_usd=1.5,
            runtime_session_id="rs-2",
        )
        fresh["created_at_ms"] = starts[late] + 120_000
        await storage.record_events(
            enrolment.computer.id,
            "run-1",
            [Event.model_validate(item) for item in (grown, fresh)],
        )
        _, on_late = await _get(session, url, **{"X-Timezone": str(late)})
        assert "6K in · 0 out · 300 cached · $1.50" in on_late

        # case: the count reads in K, M, B and T as it grows
        huge = _event(
            6,
            "usage.updated",
            "agent-1",
            total={"input_tokens": 2_500_000_000},
            runtime_session_id="rs-3",
        )
        huge["created_at_ms"] = starts[late] + 180_000
        await storage.record_events(
            enrolment.computer.id, "run-1", [Event.model_validate(huge)]
        )
        _, on_late = await _get(session, url, **{"X-Timezone": str(late)})
        assert "2.5B in" in on_late
        vast = _event(
            7,
            "usage.updated",
            "agent-1",
            total={"input_tokens": 3_000_000_000_000},
            runtime_session_id="rs-4",
        )
        vast["created_at_ms"] = starts[late] + 240_000
        await storage.record_events(
            enrolment.computer.id, "run-1", [Event.model_validate(vast)]
        )
        _, on_late = await _get(session, url, **{"X-Timezone": str(late)})
        assert "3.0T in" in on_late


@pytest.mark.asyncio
async def test_a_long_turn_still_reads_as_busy(tmp_path: Path) -> None:
    """The turn boundary is what counts, however many events came after it."""

    async with (
        serving(tmp_path) as (base, storage),
        signed_in(base, storage) as session,
    ):
        enrolment = await enrol(storage, "kana")
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
async def test_a_turn_open_in_another_conversation_keeps_the_agent_busy(
    tmp_path: Path,
) -> None:
    """One conversation's turn ending says nothing about the other's."""

    async with (
        serving(tmp_path) as (base, storage),
        signed_in(base, storage) as session,
    ):
        enrolment = await enrol(storage, "kana")
        agents = [{"agent_id": "agent-1", "name": "IE", "status": "started"}]
        second_start = _event(3, "runtime.request.turn.started", "agent-1")
        second_end = _event(4, "runtime.turn.completed", "agent-1")
        for item in (second_start, second_end):
            item["correlation"]["thread_id"] = "thread-2"
        await storage.record_events(
            enrolment.computer.id,
            "run-1",
            [
                Event.model_validate(item)
                for item in (
                    _health(agents, 1),
                    _event(2, "runtime.request.turn.started", "agent-1"),
                    second_start,
                    second_end,
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
        assert (
            f'href="/computers/{await _short(storage, enrolment.computer.id)}"' in page
        )
        assert enrolment.computer.id not in page
        status, agents = await _get(
            session, f"{base}/agents", **{"Accept-Language": "en"}
        )
        assert 'class="dot offline"' in agents
        assert 'class="li agent off"' in agents
        assert (
            f'href="/agents/{await _short(storage, enrolment.computer.id, "a")}"'
            not in agents
        )


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

        # case: the name is asked for in a box over the page; a blank one
        # keeps the box up and leaves the page behind it alone
        status, form = await _get(session, f"{base}/computers/new")
        assert status == 200 and 'name="name"' in form
        assert '<div class="veil" id="enrol"' in form
        # the new page is drawn afresh, not morphed over the one open before
        assert 'hx-target="#main" hx-swap="innerHTML"' in form
        async with session.post(
            f"{base}/computers", data={"name": "  "}, headers={"HX-Request": "true"}
        ) as response:
            assert response.headers["HX-Retarget"] == "#enrol"
            assert response.headers["HX-Reswap"] == "outerHTML"

        async with session.post(
            f"{base}/computers",
            data={"name": "kana"},
            headers={"HX-Request": "true", "Accept-Language": "zh-CN"},
        ) as response:
            assert response.status == 200
            page = await response.text()
            pushed = response.headers["HX-Push-Url"]
        computers = await storage.list_computers(await root_id(storage), limit=10)
        assert [item.name for item in computers] == ["kana"]
        token_prefix = f"{computers[0].id}:"
        # case: the module comes back open on the new computer, its row
        # picked and its page under the commands
        assert pushed == f"/computers/{await _short(storage, computers[0].id)}"
        assert 'class="li off on"' in page
        short = await _short(storage, computers[0].id)
        assert f'<div id="connect-{short}" hx-morph-skip>' in page
        # the commands are a code block, coloured by their shell; read as text
        # they are what gets copied
        text = html.unescape(re.sub(r"<[^>]+>", "", page))
        connect = text.split("接入命令")[1]
        assert connect.index("bcn server connect") < connect.index("电脑上的智能体")
        assert 'class="for md" id="for-unix"' in page and 'class="tok-' in page
        # case: the snippet carries the token and the server's own address,
        # once for either kind of machine, open on the browser's own kind
        assert f"bcn server connect --url {base} --token {token_prefix}" in text
        assert "仅显示一次" in page
        assert "install.sh | sh" in text and "install.ps1 | iex" in text
        assert 'id="snippet-unix" checked' in page
        async with session.post(
            f"{base}/computers",
            data={"name": "ie"},
            headers={"HX-Request": "true", "Sec-CH-UA-Platform": '"Windows"'},
        ) as response:
            assert 'id="snippet-windows" checked' in await response.text()

        # case: the token is not shown again anywhere
        status, again = await _get(
            session, f"{base}/computers/{await _short(storage, computers[0].id)}"
        )
        assert status == 200 and token_prefix not in again and "kana" in again
        # the pane's own refresh leaves the box to keep what it holds
        status, pane = await _get(
            session,
            f"{base}/computers/{await _short(storage, computers[0].id)}/detail",
        )
        assert f'<div id="connect-{short}" hx-morph-skip></div>' in pane
        assert f'id="remove-{short}" hx-morph-skip' in pane
        # case: a computer not up yet cannot take an agent in
        assert (
            " disabled>" in pane.split('<button class="add"')[1].split("</button>")[0]
        )


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
        enrolment = await enrol(storage, "old")
        computer_id = enrolment.computer.id
        # an agent that has only ever been named in a health beat
        idle = [{"agent_id": "agent-idle", "name": "idle", "status": "started"}]
        await storage.record_events(
            computer_id, "run-1", [Event.model_validate(_health(idle, 1))]
        )

        # case: the button asks first, then the computer and its events go
        short = await _short(storage, computer_id)
        status, question = await _get(
            session, f"{base}/computers/{short}/remove", **{"Accept-Language": "zh-CN"}
        )
        assert status == 200 and f'hx-delete="/computers/{short}"' in question
        async with session.delete(
            f"{base}/computers/{short}", headers={"HX-Request": "true"}
        ) as response:
            assert response.status == 200
            assert response.headers["HX-Push-Url"] == "/computers"
            assert "old" not in await response.text()
        assert await storage.list_computers(await root_id(storage), limit=10) == []
        assert await storage.count_events(computer_id) == 0
        # case: nobody stands in any relation to it or its agents any more
        async with (
            aiosqlite.connect(tmp_path / "bcs.sqlite3") as connection,
            connection.execute("SELECT COUNT(*) FROM relations") as cursor,
        ):
            assert await cursor.fetchone() == (0,)

        # case: removing it again is nothing
        async with session.delete(f"{base}/computers/{short}") as response:
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
            enrolment = await enrol(storage, f"computer-{index}")
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
        # a computer's cursor is its number; an agent's is its computer's and
        # its own
        edges = {
            "/computers": (
                await _short(storage, ids[PAGE_SIZE - 1]),
                await _short(storage, ids[-1]),
            ),
            "/agents": (
                await _short(storage, ids[PAGE_SIZE - 1], f"agent-{PAGE_SIZE - 1}"),
                await _short(storage, ids[-1], f"agent-{PAGE_SIZE + 4}"),
            ),
        }

        # case: a page shows the first rows and where the next page starts
        for url, (edge, _) in edges.items():
            _, html = await _get(session, base + url)
            assert f'data-after="{edge}"' in html, url
            _, html = await _get(session, f"{base}{url}/list")
            assert _rows(html) == PAGE_SIZE, url
            assert f'data-after="{edge}"' in html, url

        # case: the page past the edge is the rest; it ends at the last row
        # and says so without asking for more
        for url, (edge, last) in edges.items():
            _, html = await _get(session, f"{base}{url}/list?after={edge}")
            assert _rows(html) == 5, url
            assert f'data-after="{last}"' in html, url
            assert "revealed" not in html, url

        # case: the refresh brings back everything loaded so far, edge included
        for url, (edge, last) in edges.items():
            _, html = await _get(session, f"{base}{url}/list?until={edge}")
            assert _rows(html) == PAGE_SIZE, url
            assert f'data-after="{edge}"' in html and "revealed" in html, url
            _, html = await _get(session, f"{base}{url}/list?until={last}")
            assert _rows(html) == PAGE_SIZE + 5, url
            assert f'data-after="{last}"' in html, url
            assert "revealed" not in html, url

        # case: agents page by agent: one computer with many of them fills
        # pages of its own
        crowd = await enrol(storage, "crowd")
        await storage.record_events(
            crowd.computer.id,
            "run-1",
            [
                Event.model_validate(
                    _health(
                        [
                            {
                                "agent_id": f"z{index:03d}",
                                "name": "z",
                                "status": "started",
                            }
                            for index in range(PAGE_SIZE + 1)
                        ],
                        1,
                    )
                )
            ],
        )
        _, html = await _get(session, f"{base}/agents/list?after={edges['/agents'][1]}")
        assert _rows(html) == PAGE_SIZE
        crowded = await _short(storage, crowd.computer.id, f"z{PAGE_SIZE - 1:03d}")
        assert f'data-after="{crowded}"' in html
        assert "revealed" in html
        _, html = await _get(session, f"{base}/agents/list?after={crowded}")
        assert _rows(html) == 1 and "revealed" not in html

        # case: a computer on a later page still has its own pages
        last = edges["/computers"][1]
        status, html = await _get(session, f"{base}/computers/{last}")
        assert status == 200 and f"computer-{PAGE_SIZE + 4}" in html
        status, _ = await _get(session, f"{base}/agents/{edges['/agents'][1]}")
        assert status == 200
        status, _ = await _get(session, f"{base}/agents/{edges['/agents'][1]}/activity")
        assert status == 200


@pytest.mark.asyncio
async def test_an_account_sees_what_it_enrolled_and_nothing_else(
    tmp_path: Path,
) -> None:
    async with serving(tmp_path) as (base, storage):
        await with_password(storage, "other", TESTER[1])
        mine = await enrol(storage, "mine")
        theirs = await enrol(storage, "theirs", owner="other")
        for enrolment, agent_id in ((mine, "agent-mine"), (theirs, "agent-theirs")):
            await storage.record_events(
                enrolment.computer.id,
                "run-1",
                [
                    Event.model_validate(
                        _health(
                            [
                                {
                                    "agent_id": agent_id,
                                    "name": agent_id,
                                    "status": "started",
                                }
                            ],
                            1,
                        )
                    )
                ],
            )

        async with signed_in(base, storage) as session:
            # case: the lists carry root's computer and agent only
            _, computers = await _get(session, f"{base}/computers/list")
            assert "mine" in computers and "theirs" not in computers
            _, agents = await _get(session, f"{base}/agents/list")
            assert "agent-mine" in agents and "agent-theirs" not in agents

            # case: the other account's things are not there to open, remove or enrol against
            computer = await _short(storage, theirs.computer.id)
            agent = await _short(storage, theirs.computer.id, "agent-theirs")
            for method, url in (
                ("GET", f"/computers/{computer}"),
                ("GET", f"/computers/{computer}/detail"),
                ("GET", f"/computers/{computer}/presence"),
                ("GET", f"/computers/{computer}/remove"),
                ("DELETE", f"/computers/{computer}"),
                ("GET", f"/agents/{agent}"),
                ("GET", f"/agents/{agent}/activity"),
            ):
                async with session.request(method, base + url) as response:
                    assert response.status == 404, (method, url)
            assert await storage.find_computer(theirs.computer.id) is not None

            # case: a computer enrolled from the page belongs to whoever enrolled it
            async with session.post(
                f"{base}/computers", data={"name": "fresh"}
            ) as response:
                assert response.status == 200
            _, computers = await _get(session, f"{base}/computers/list")
            assert "fresh" in computers

        async with signed_in(base, storage, "other") as session:
            _, computers = await _get(session, f"{base}/computers/list")
            assert "theirs" in computers
            assert "mine" not in computers and "fresh" not in computers
            _, agents = await _get(session, f"{base}/agents/list")
            assert "agent-theirs" in agents and "agent-mine" not in agents
