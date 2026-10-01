from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5, uuid7

import pytest
from bcn_test_support import StaticChannelBuilder, TestChannel

from bazaar_compute_node.app.application import NodeApplication
from bazaar_compute_node.app.config import (
    AgentConfiguration,
    ChannelConfiguration,
    NodeConfiguration,
    RuntimeConfiguration,
)
from bazaar_compute_node.app.registry import AdapterRegistry, AgentAdapterFactories
from bazaar_compute_node.contrib.codex.plugin import builder
from bazaar_compute_node.core.agent import State
from bazaar_compute_node.core.models import (
    Message,
    MessageDirection,
    Review,
    SenderIdentity,
)

from ._serving import TESTER, enrol, serving_app, with_password
from .test_pages import _short

pytestmark = pytest.mark.e2e


class SearchRegistry(AdapterRegistry):
    def __init__(self, channels: dict[str, TestChannel]) -> None:
        self.channels = channels

    def load_agent(
        self, *, channels: Sequence[str], runtimes: Sequence[str]
    ) -> AgentAdapterFactories:
        return AgentAdapterFactories(
            channels={
                kind: StaticChannelBuilder(self.channels[kind]) for kind in channels
            },
            runtimes={kind: builder.build for kind in runtimes},
        )


async def settled(page) -> None:
    await page.wait_for_function(
        "() => { const s=Alpine.$data(document.querySelector('#message-search')).s; return !s.busy && s.resultsVersion === s.version }",
        timeout=15000,
    )


async def loaded(page, count: int) -> None:
    await page.wait_for_function(
        "count => Alpine.$data(document.querySelector('#message-search')).s.count === count",
        arg=count,
    )
    await settled(page)


@pytest.mark.asyncio
async def test_search_control_browser_filters_and_subscription_state(
    system_temp_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    playwright = pytest.importorskip("playwright.async_api")
    async_playwright, expect = playwright.async_playwright, playwright.expect

    out = Path(os.environ.get("BCS_SEARCH_ARTIFACTS", str(system_temp_dir / "screens")))
    out.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    traffic: list[str] = []
    report: dict[str, object] = {}
    async with serving_app(system_temp_dir / "server") as (base, server):
        await with_password(server.state.storage, *TESTER)
        enrolled = await enrol(server.state.storage, "Search workstation")
        monkeypatch.setenv("BCN_SERVER_TOKEN", enrolled.token)
        agent_id = str(uuid7())
        channel = TestChannel()
        configuration = NodeConfiguration(
            version_check=False,
            storage="sqlite",
            audit="server",
            control="server",
            audit_options={"url": base, "token_env": "BCN_SERVER_TOKEN"},
            control_options={"url": base, "token_env": "BCN_SERVER_TOKEN"},
            agents=(
                AgentConfiguration(
                    id=agent_id,
                    name="Release colleague",
                    channels=(ChannelConfiguration(kind="test"),),
                    runtimes=(
                        RuntimeConfiguration(
                            kind="codex", model="gpt-5.6-luna", effort="max"
                        ),
                    ),
                ),
            ),
        )
        node = NodeApplication(
            configuration=configuration,
            shared_factories=AdapterRegistry().load_shared(
                storage="sqlite", audit="server", control="server"
            ),
            registry=SearchRegistry({"test": channel}),
            endpoint_path=system_temp_dir / "search.sock",
        )
        await node.start()
        try:
            agent = node.agents[agent_id]
            sessions = [str(uuid7()), str(uuid7())]
            messages = []
            for index in range(86):
                number = index % 2
                at = int(
                    datetime(2026, 9, 28 + index % 3, 12, tzinfo=UTC).timestamp() * 1000
                )
                message = Message(
                    direction=MessageDirection.INBOUND,
                    seq=0,
                    message_id=str(uuid7()),
                    thread_id=sessions[number],
                    channel_session_id=sessions[number],
                    channel="test",
                    provider_thread_id=sessions[number],
                    provider_message_id=str(uuid7()),
                    target=f"dm:{sessions[number]}",
                    sender=SenderIdentity(
                        id="alice" if number == 0 else "bob",
                        name="alice" if number == 0 else "bob",
                        display_name="Alice" if number == 0 else "Bob",
                    ),
                    body=f"部署 staging 发布计划 {index}：先灰度两台，观察 30 分钟，异常由 Alice 回滚。"
                    + "发布前核对监控与缓存配置。" * 15,
                    received_at_ms=at,
                    notifies_runtime=False,
                )
                messages.append(message)
                await channel.inject(message)
            threads = [
                str(uuid5(NAMESPACE_URL, f"bcn:{agent_id}:bcn-session:{value}"))
                for value in sessions
            ]
            async with asyncio.timeout(30):
                while (
                    len(await agent.storage.list_messages(threads[0])) < 43
                    or len(await agent.storage.list_messages(threads[1])) < 43
                ):
                    await asyncio.sleep(0.05)
                while not (
                    await server.state.storage.computer_health([enrolled.computer])
                )[0].health:
                    await asyncio.sleep(0.05)
            for thread in threads:
                await agent.orchestrator.command_service.review_contact(
                    thread, Review.APPROVED
                )
            scope = await _short(server.state.storage, enrolled.computer.id, agent_id)
            async with async_playwright() as driver:
                browser = await driver.chromium.launch(
                    executable_path="/usr/bin/microsoft-edge", headless=True
                )
                try:
                    context = await browser.new_context(
                        viewport={"width": 1280, "height": 900},
                        timezone_id="Asia/Shanghai",
                        locale="en-US",
                    )
                    page = await context.new_page()
                    page.on(
                        "pageerror",
                        lambda error, errors=errors: errors.append(str(error)),
                    )
                    page.on(
                        "request",
                        lambda request, traffic=traffic: traffic.append(
                            request.url.removeprefix(base)
                        ),
                    )
                    await page.goto(base + "/login")
                    await page.locator("#name").fill(TESTER[0])
                    await page.locator("#password").fill(TESTER[1])
                    await page.locator("#login button").click()
                    await page.wait_for_url("**/agents")
                    await page.goto(f"{base}/agents/{scope}")
                    await expect(page.locator("#contacts .li")).to_have_count(2)
                    await page.keyboard.press("Control+f")
                    await expect(page.locator(".search-dialog")).to_be_visible()
                    await page.locator("#search-query").fill("部署 staging")
                    await loaded(page, 20)
                    await settled(page)
                    await page.locator(".search-results").evaluate(
                        "el => {el.scrollTop = el.scrollHeight; el.dispatchEvent(new Event('scroll'))}"
                    )
                    await loaded(page, 40)
                    await page.locator(".search-results").evaluate(
                        "el => {el.scrollTop = 500; el.dispatchEvent(new Event('scroll'))}"
                    )
                    await page.locator(".search-filters button").nth(2).click()
                    await page.locator(".search-date").first.click()
                    await page.evaluate(
                        "() => {const c=Alpine.$data(document.querySelector('#message-search'));c.s.month='2026-09';c.s.cursor='2026-09-28'}"
                    )
                    await page.locator(".calendar-heading button").last.click()
                    await expect(page.locator(".calendar-heading b")).to_contain_text(
                        "October"
                    )
                    await page.locator(".calendar-heading button").first.click()
                    await expect(page.locator(".calendar-heading b")).to_contain_text(
                        "September"
                    )
                    await page.locator('[data-date="2026-09-28"]').click()
                    await page.locator('[data-date="2026-09-29"]').click()
                    await page.locator(".search-date-actions .p").click()
                    await loaded(page, 20)
                    await settled(page)
                    date_state = await page.evaluate(
                        "Alpine.$data(document.querySelector('#message-search')).s"
                    )
                    response = await context.request.get(
                        f"{base}/agents/{scope}/search?query=部署%20staging&after_ms={int(datetime(2026, 9, 27, 16, tzinfo=UTC).timestamp() * 1000)}&before_ms={int(datetime(2026, 9, 29, 16, tzinfo=UTC).timestamp() * 1000) - 1}&limit=50",
                        headers={"X-Timezone": "Asia/Shanghai"},
                    )
                    html = await response.text()
                    dated_ids = [
                        item.message_id
                        for item in messages
                        if datetime.fromtimestamp(item.received_at_ms / 1000, UTC).day
                        in (28, 29)
                    ]
                    returned_ids = set(re.findall(r'data-message="([^"]+)"', html))
                    assert returned_ids and returned_ids.issubset(dated_ids)
                    report["date_range"] = {
                        "after": date_state["after"],
                        "before": date_state["before"],
                        "matching_sources": len(dated_ids),
                    }
                    await page.locator(".search-toolbar button").click()
                    await loaded(page, 20)
                    await settled(page)
                    await page.locator(".search-filters button").first.click()
                    await expect(
                        page.locator("#search-contacts .search-option")
                    ).to_have_count(3)
                    await page.locator("#search-contacts .search-option").nth(1).click()
                    await loaded(page, 20)
                    await settled(page)
                    await page.locator(".search-filters button").nth(1).click()
                    await expect(
                        page.locator("#search-senders .search-option")
                    ).to_have_count(2)
                    await page.locator("#search-senders .search-option").nth(1).click()
                    await loaded(page, 20)
                    await settled(page)
                    chosen = await page.evaluate(
                        "Alpine.$data(document.querySelector('#message-search')).s.target"
                    )
                    chosen_thread = threads[0]
                    for thread in threads:
                        if (await agent.storage.list_messages(thread))[
                            0
                        ].target == chosen:
                            chosen_thread = thread
                            break
                    # A real provider creates the CLI binding; the test channel remains the control plane.
                    question = Message(
                        direction=MessageDirection.INBOUND,
                        seq=0,
                        message_id=str(uuid7()),
                        thread_id=sessions[threads.index(chosen_thread)],
                        channel_session_id=sessions[threads.index(chosen_thread)],
                        channel="test",
                        provider_thread_id=sessions[threads.index(chosen_thread)],
                        provider_message_id=str(uuid7()),
                        target=chosen,
                        sender=SenderIdentity(id="alice", name="alice"),
                        received_at_ms=int(datetime.now(UTC).timestamp() * 1000),
                        body="我在整理发布安排。之前 staging 发布计划最后决定怎么灰度，观察多久，异常由谁回滚？",
                    )
                    actor = agent.actors.for_thread(chosen_thread)
                    await channel.inject(question)
                    async with asyncio.timeout(240):
                        while (
                            not channel.sent_messages
                            or agent.orchestrator.session_runtime_state(actor)
                            is not State.IDLE
                        ):
                            await asyncio.sleep(0.1)
                    runtime = agent.orchestrator.runtime_session(actor)
                    assert runtime is not None and agent._wrapper_path is not None
                    environment = agent._build_command_environment(
                        actor.id, runtime.id, runtime_index=0
                    )
                    process = await asyncio.create_subprocess_exec(
                        str(agent._wrapper_path),
                        "message",
                        "search",
                        "--query",
                        "部署 staging",
                        "--target",
                        chosen,
                        "--sender",
                        "@alice" if chosen_thread == threads[0] else "@bob",
                        "--limit",
                        "50",
                        env=environment,
                        stdout=asyncio.subprocess.PIPE,
                    )
                    stdout, _ = await process.communicate()
                    cli = stdout.decode()
                    sender = "@alice" if chosen_thread == threads[0] else "@bob"
                    response = await context.request.get(
                        f"{base}/agents/{scope}/search",
                        params={
                            "query": "部署 staging",
                            "target": chosen,
                            "sender": sender,
                            "limit": "50",
                        },
                    )
                    html = await response.text()
                    source_ids = [
                        item.message_id
                        for item in messages
                        if item.thread_id == sessions[threads.index(chosen_thread)]
                    ]
                    assert process.returncode == 0 and all(
                        value in cli and value in html for value in source_ids
                    )
                    report["cli_control"] = {
                        "sources": source_ids,
                        "mode": "session",
                        "operator_scope": "both conversations",
                    }
                    # The search node is outside real subscription swaps, including open calendar drafts.
                    await page.locator(".search-toolbar button").click()
                    await loaded(page, 20)
                    await settled(page)
                    await page.locator(".search-results").evaluate(
                        "el => {el.scrollTop = el.scrollHeight; el.dispatchEvent(new Event('scroll'))}"
                    )
                    await loaded(page, 40)
                    await page.locator(".search-results").evaluate(
                        "el => {el.scrollTop = 500; el.dispatchEvent(new Event('scroll'))}"
                    )
                    await page.locator(".search-filters button").nth(2).click()
                    await page.locator(".search-date").first.click()
                    await expect(
                        page.locator('.calendar-days [tabindex="0"]')
                    ).to_be_focused()
                    before = await page.evaluate(
                        "() => {window.searchNode=document.querySelector('#message-search'); const s=Alpine.$data(window.searchNode).s; return {query:s.query,items:s.items.map(item => item.html),count:s.count,offset:s.offset,selected:s.selected,scroll:s.scroll,panel:s.panel,calendar:s.calendar,draftAfter:s.draftAfter,draftBefore:s.draftBefore,focus:document.activeElement.dataset.date}}"
                    )
                    traffic.clear()
                    for index in range(3):
                        await channel.inject(
                            Message(
                                direction=MessageDirection.INBOUND,
                                seq=0,
                                message_id=str(uuid7()),
                                thread_id=sessions[0],
                                channel_session_id=sessions[0],
                                channel="test",
                                provider_thread_id=sessions[0],
                                provider_message_id=str(uuid7()),
                                target=f"dm:{sessions[0]}",
                                sender=SenderIdentity(id="alice", name="alice"),
                                body=f"订阅状态更新 {index}",
                                received_at_ms=int(
                                    datetime.now(UTC).timestamp() * 1000
                                ),
                                notifies_runtime=False,
                            )
                        )
                        await asyncio.sleep(6)
                    await expect(
                        page.locator('.calendar-days [tabindex="0"]')
                    ).to_be_focused()
                    after = await page.evaluate(
                        "() => {const s=Alpine.$data(document.querySelector('#message-search')).s; return {query:s.query,items:s.items.map(item => item.html),count:s.count,offset:s.offset,selected:s.selected,scroll:s.scroll,panel:s.panel,calendar:s.calendar,draftAfter:s.draftAfter,draftBefore:s.draftBefore,focus:document.activeElement.dataset.date}}"
                    )
                    (out / "subscription-debug.json").write_text(
                        json.dumps(
                            {
                                "before": {
                                    k: v for k, v in before.items() if k != "items"
                                },
                                "after": {
                                    k: v for k, v in after.items() if k != "items"
                                },
                                "active": await page.evaluate(
                                    "document.activeElement.outerHTML"
                                ),
                            },
                            ensure_ascii=False,
                            indent=2,
                        )
                    )
                    assert before == after
                    assert sum(url.startswith("/poll") for url in traffic) >= 3 and any(
                        "/contacts?" in url for url in traffic
                    )
                    assert await page.evaluate(
                        "window.searchNode === document.querySelector('#message-search')"
                    )
                    report["subscription"] = {
                        "rounds": 3,
                        "requests": traffic,
                        "state_preserved": True,
                    }
                    await page.screenshot(path=str(out / "desktop-calendar.png"))
                    await page.keyboard.press("Escape")
                    await page.keyboard.press("Escape")
                    await page.keyboard.press("Escape")
                    await expect(page.locator(".search-dialog")).to_be_hidden()
                    await page.keyboard.press("Control+f")
                    await loaded(page, 40)
                    # Real HTTP query concurrency: only the latest version may become visible.
                    await page.locator("#search-query").fill("部署")
                    await page.wait_for_timeout(310)
                    await page.locator("#search-query").fill("订阅状态更新")
                    await loaded(page, 3)
                    await page.screenshot(path=str(out / "desktop-results.png"))
                    await settled(page)
                    await page.locator("#search-query").focus()
                    await page.keyboard.press("ArrowDown")
                    await page.keyboard.press("Enter")
                    await expect(
                        page.locator('#chat[data-active="true"]')
                    ).to_be_visible()
                    await page.keyboard.press("Control+f")
                    await loaded(page, 3)
                    await page.locator(".search-close").click()
                    await page.locator(".switch .tab").first.click()
                    await page.keyboard.press("Control+f")
                    await loaded(page, 3)
                    report["navigation_restored"] = True
                    cookies = await context.cookies()
                    matrix = []
                    for language in ("en-US", "zh-CN"):
                        mobile = await browser.new_context(
                            viewport={"width": 390, "height": 844},
                            locale=language,
                            timezone_id="Asia/Shanghai",
                            has_touch=True,
                        )
                        await mobile.add_cookies(cookies)
                        preview = await mobile.new_page()
                        preview.on("pageerror", lambda error: errors.append(str(error)))
                        await preview.goto(f"{base}/agents/{scope}")
                        await preview.locator(".contacts-toolbar .search-open").click()
                        await preview.locator("#search-query").fill("部署 staging")
                        await loaded(preview, 20)
                        await settled(preview)
                        await preview.locator(".search-filters button").nth(2).click()
                        await preview.locator(".search-date").first.click()
                        for width in (320, 390, 430, 768, 959, 960):
                            await preview.set_viewport_size(
                                {"width": width, "height": 844}
                            )
                            for theme in ("light", "dark"):
                                await preview.evaluate(
                                    "(theme) => document.documentElement.dataset.theme = theme",
                                    theme,
                                )
                                for month in ("2026-09", "2026-08"):
                                    await preview.evaluate(
                                        '(month) => { const s=Alpine.$data(document.querySelector("#message-search")).s;s.month=month;s.cursor=month+"-01" }',
                                        month,
                                    )
                                    await preview.wait_for_timeout(30)
                                    bounds = await preview.locator(
                                        ".search-dialog"
                                    ).bounding_box()
                                    assert (
                                        bounds
                                        and bounds["x"] >= 12
                                        and bounds["x"] + bounds["width"] <= width - 12
                                    )
                                    assert await preview.evaluate(
                                        "document.documentElement.scrollWidth <= innerWidth"
                                    )
                                    days = await preview.locator(
                                        ".calendar-days button"
                                    ).count()
                                    assert days in (35, 42)
                                    matrix.append(
                                        {
                                            "width": width,
                                            "language": language,
                                            "theme": theme,
                                            "month": month,
                                            "days": days,
                                            "bounds": bounds,
                                        }
                                    )
                                if width in (320, 390, 960):
                                    await preview.screenshot(
                                        path=str(
                                            out / f"{language}-{theme}-{width}.png"
                                        )
                                    )
                        await preview.set_viewport_size({"width": 390, "height": 360})
                        await preview.locator(
                            ".search-date-actions .p"
                        ).scroll_into_view_if_needed()
                        await preview.screenshot(
                            path=str(out / f"{language}-short.png")
                        )
                        await preview.locator('.calendar-days [tabindex="0"]').focus()
                        await preview.keyboard.press("ArrowRight")
                        await playwright.expect(
                            preview.locator('[data-date="2026-08-02"]')
                        ).to_be_focused()
                        await preview.keyboard.press("Enter")
                        await preview.keyboard.press("ArrowDown")
                        await playwright.expect(
                            preview.locator('[data-date="2026-08-09"]')
                        ).to_be_focused()
                        await preview.keyboard.press("Enter")
                        await preview.locator(".search-date-cancel").click()
                        await preview.locator("#search-query").focus()
                        await preview.locator("#search-query").evaluate(
                            "el => {el.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true})); el.value='部署';el.dispatchEvent(new InputEvent('input',{bubbles:true,isComposing:true}));el.dispatchEvent(new CompositionEvent('compositionend',{bubbles:true}));el.dispatchEvent(new InputEvent('input',{bubbles:true}))}"
                        )
                        await loaded(preview, 20)
                        await settled(preview)
                        await mobile.close()
                    report["matrix"] = matrix
                    report["browser_errors"] = errors
                    assert not errors, errors
                    (out / "verification.json").write_text(
                        json.dumps(report, ensure_ascii=False, indent=2)
                    )
                    await context.close()
                finally:
                    await browser.close()
        finally:
            await node.stop()


@pytest.mark.asyncio
async def test_search_sender_pages_and_agent_navigation(
    system_temp_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    playwright = pytest.importorskip("playwright.async_api")
    async with serving_app(system_temp_dir / "server") as (base, server):
        await with_password(server.state.storage, *TESTER)
        enrolled = await enrol(server.state.storage, "Search scopes")
        monkeypatch.setenv("BCN_SERVER_TOKEN", enrolled.token)
        channels = {"test": TestChannel(), "other": TestChannel()}
        ids = [str(uuid7()), str(uuid7())]
        names = ["Alpha colleague", "Beta colleague"]
        configuration = NodeConfiguration(
            version_check=False,
            storage="sqlite",
            audit="server",
            control="server",
            audit_options={"url": base, "token_env": "BCN_SERVER_TOKEN"},
            control_options={"url": base, "token_env": "BCN_SERVER_TOKEN"},
            agents=tuple(
                AgentConfiguration(
                    id=agent_id,
                    name=names[index],
                    channels=(ChannelConfiguration(kind=kind),),
                    runtimes=(RuntimeConfiguration(kind="codex"),),
                )
                for index, (agent_id, kind) in enumerate(
                    zip(ids, channels, strict=True)
                )
            ),
        )
        node = NodeApplication(
            configuration=configuration,
            shared_factories=AdapterRegistry().load_shared(
                storage="sqlite", audit="server", control="server"
            ),
            registry=SearchRegistry(channels),
            endpoint_path=system_temp_dir / "scopes.sock",
        )
        await node.start()
        try:
            for index, kind in enumerate(channels):
                session = str(uuid7())
                for number in range(30):
                    await channels[kind].inject(
                        Message(
                            direction=MessageDirection.INBOUND,
                            seq=0,
                            message_id=str(uuid7()),
                            thread_id=session,
                            channel_session_id=session,
                            channel=kind,
                            provider_thread_id=session,
                            provider_message_id=str(uuid7()),
                            target=f"dm:{session}",
                            sender=SenderIdentity(
                                id=f"partner-{number}",
                                name=f"partner-{number}",
                                display_name=f"Partner {number}",
                            ),
                            body=f"联调记录：{names[index]} 与 Partner {number} 已确认发布安排。",
                            received_at_ms=1790784000000 + number * 1000,
                            notifies_runtime=False,
                        )
                    )
                for number, at in enumerate(
                    (
                        int(datetime(2026, 9, 27, 16, tzinfo=UTC).timestamp() * 1000),
                        int(
                            datetime(
                                2026, 9, 29, 15, 59, 59, 999000, tzinfo=UTC
                            ).timestamp()
                            * 1000
                        ),
                    )
                ):
                    await channels[kind].inject(
                        Message(
                            direction=MessageDirection.INBOUND,
                            seq=0,
                            message_id=str(uuid7()),
                            thread_id=session,
                            channel_session_id=session,
                            channel=kind,
                            provider_thread_id=session,
                            provider_message_id=str(uuid7()),
                            target=f"dm:{session}",
                            sender=SenderIdentity(
                                id=f"partner-{number}", name=f"partner-{number}"
                            ),
                            body=f"日期边界：{names[index]} 的起止日记录。",
                            received_at_ms=at,
                            notifies_runtime=False,
                        )
                    )
                thread = str(
                    uuid5(NAMESPACE_URL, f"bcn:{ids[index]}:bcn-session:{session}")
                )
                async with asyncio.timeout(20):
                    while (
                        len(await node.agents[ids[index]].storage.list_messages(thread))
                        < 32
                    ):
                        await asyncio.sleep(0.05)
                await node.agents[
                    ids[index]
                ].orchestrator.command_service.review_contact(thread, Review.APPROVED)
            async with asyncio.timeout(20):
                while not (
                    await server.state.storage.computer_health([enrolled.computer])
                )[0].health:
                    await asyncio.sleep(0.05)
            async with playwright.async_playwright() as driver:
                browser = await driver.chromium.launch(
                    executable_path="/usr/bin/microsoft-edge", headless=True
                )
                try:
                    context = await browser.new_context(
                        viewport={"width": 1280, "height": 900}
                    )
                    page = await context.new_page()
                    errors = []
                    page.on(
                        "pageerror",
                        lambda error, errors=errors: errors.append(str(error)),
                    )
                    await page.goto(base + "/login")
                    await page.locator("#name").fill(TESTER[0])
                    await page.locator("#password").fill(TESTER[1])
                    await page.locator("#login button").click()
                    await page.wait_for_url("**/agents")
                    scope = await _short(
                        server.state.storage, enrolled.computer.id, ids[0]
                    )
                    response = await context.request.get(
                        f"{base}/agents/{scope}/search",
                        params={
                            "query": "日期边界",
                            "after_ms": str(
                                int(
                                    datetime(2026, 9, 27, 16, tzinfo=UTC).timestamp()
                                    * 1000
                                )
                            ),
                            "before_ms": str(
                                int(
                                    datetime(
                                        2026, 9, 29, 15, 59, 59, 999000, tzinfo=UTC
                                    ).timestamp()
                                    * 1000
                                )
                            ),
                        },
                    )
                    boundary_ids = re.findall(
                        r'data-message="([^"]+)"', await response.text()
                    )
                    assert len(boundary_ids) == 2

                    await (
                        page.locator("#agent-list .li")
                        .filter(has_text=names[0])
                        .click()
                    )
                    await page.locator(".contacts-toolbar .search-open").click()
                    await page.locator("#search-query").fill("联调")
                    await loaded(page, 20)
                    await settled(page)
                    await page.locator(".search-filters button").nth(1).click()
                    await page.wait_for_function(
                        "() => {const s=Alpine.$data(document.querySelector('#message-search')).s;return s.senders.length === s.count}"
                    )
                    first_senders = await page.evaluate(
                        "Alpine.$data(document.querySelector('#message-search')).s.senders"
                    )
                    loaded_senders = await page.evaluate(
                        "() => [...new Set(Alpine.$data(document.querySelector('#message-search')).s.items.map(item => new DOMParser().parseFromString(item.html, 'text/html').querySelector('.search-hit').dataset.sender))]"
                    )
                    assert {item["token"] for item in first_senders} == set(
                        loaded_senders
                    )
                    await page.keyboard.press("Escape")
                    await page.locator(".search-results .search-more").click()
                    await settled(page)
                    await page.wait_for_function(
                        "() => {const s=Alpine.$data(document.querySelector('#message-search')).s;return !s.more && s.senders.length === s.count}"
                    )
                    senders = await page.evaluate(
                        "Alpine.$data(document.querySelector('#message-search')).s.senders"
                    )
                    loaded_senders = await page.evaluate(
                        "() => [...new Set(Alpine.$data(document.querySelector('#message-search')).s.items.map(item => new DOMParser().parseFromString(item.html, 'text/html').querySelector('.search-hit').dataset.sender))]"
                    )
                    assert {item["token"] for item in senders} == set(loaded_senders)
                    assert len(senders) > len(first_senders)
                    await page.locator(".search-filters button").nth(1).click()
                    await playwright.expect(
                        page.locator("#search-senders .search-option")
                    ).to_have_count(len(senders) + 1)
                    await page.locator("#search-senders .search-option").last.click()
                    await settled(page)
                    await loaded(page, 1)
                    await playwright.expect(
                        page.locator(".search-hit")
                    ).to_contain_text(names[0])
                    alpha = await page.evaluate(
                        "() => {const s=Alpine.$data(document.querySelector('#message-search')).s;return {query:s.query,sender:s.sender,senders:s.senders,items:s.items.map(item => item.html),count:s.count}}"
                    )
                    await page.locator(".search-close").click()
                    await (
                        page.locator("#agent-list .li")
                        .filter(has_text=names[1])
                        .click()
                    )
                    await page.locator(".contacts-toolbar .search-open").click()
                    await page.locator("#search-query").fill("联调")
                    await loaded(page, 20)
                    await settled(page)
                    await playwright.expect(
                        page.locator(".search-hit").first
                    ).to_contain_text(names[1])
                    await page.locator(".search-close").click()
                    await (
                        page.locator("#agent-list .li")
                        .filter(has_text=names[0])
                        .click()
                    )
                    await page.locator(".contacts-toolbar .search-open").click()
                    await loaded(page, 1)
                    restored = await page.evaluate(
                        "() => {const s=Alpine.$data(document.querySelector('#message-search')).s;return {query:s.query,sender:s.sender,senders:s.senders,items:s.items.map(item => item.html),count:s.count}}"
                    )
                    assert restored == alpha
                    out = Path(
                        os.environ.get(
                            "BCS_SEARCH_ARTIFACTS", str(system_temp_dir / "screens")
                        )
                    )
                    out.mkdir(parents=True, exist_ok=True)
                    (out / "scopes-verification.json").write_text(
                        json.dumps(
                            {
                                "agents": ids,
                                "sender_options_first_page": len(first_senders),
                                "sender_options": len(senders),
                                "sender_options_from_loaded_results": True,
                                "state_restored": True,
                                "date_boundary_sources": boundary_ids,
                                "errors": errors,
                            },
                            ensure_ascii=False,
                            indent=2,
                        )
                    )
                    assert not errors, errors
                    await context.close()
                finally:
                    await browser.close()
        finally:
            await node.stop()


@pytest.mark.asyncio
async def test_search_focus_preserves_history_and_discards_old_responses(
    system_temp_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bazaar_compute_node.core.models import (
        ChannelSession,
        ChannelTargetKind,
        Thread,
    )

    playwright = pytest.importorskip("playwright.async_api")
    out = Path(os.environ.get("BCS_SEARCH_ARTIFACTS", str(system_temp_dir / "screens")))
    out.mkdir(parents=True, exist_ok=True)
    report = []
    async with serving_app(system_temp_dir / "server") as (base, server):
        await with_password(server.state.storage, *TESTER)
        enrolled = await enrol(server.state.storage, "History workstation")
        monkeypatch.setenv("BCN_SERVER_TOKEN", enrolled.token)
        agent_id = str(uuid7())
        channel = TestChannel()
        configuration = NodeConfiguration(
            version_check=False,
            storage="sqlite",
            audit="server",
            control="server",
            audit_options={"url": base, "token_env": "BCN_SERVER_TOKEN"},
            control_options={"url": base, "token_env": "BCN_SERVER_TOKEN"},
            agents=(
                AgentConfiguration(
                    id=agent_id,
                    name="History colleague",
                    channels=(ChannelConfiguration(kind="test"),),
                    runtimes=(RuntimeConfiguration(kind="codex"),),
                ),
            ),
        )
        node = NodeApplication(
            configuration=configuration,
            shared_factories=AdapterRegistry().load_shared(
                storage="sqlite", audit="server", control="server"
            ),
            registry=SearchRegistry({"test": channel}),
            endpoint_path=system_temp_dir / "history.sock",
        )
        await node.start()
        try:
            sessions = [str(uuid7()), str(uuid7())]
            threads = [
                str(uuid5(NAMESPACE_URL, f"bcn:{agent_id}:bcn-session:{session}"))
                for session in sessions
            ]
            messages = []
            for index, session in enumerate(sessions):
                rows = []
                for number in range(100):
                    message = Message(
                        direction=MessageDirection.INBOUND,
                        seq=0,
                        message_id=str(uuid7()),
                        thread_id=session,
                        channel_session_id=session,
                        channel="test",
                        provider_thread_id=session,
                        provider_message_id=str(uuid7()),
                        target=f"dm:{session}",
                        sender=SenderIdentity(name=f"partner-{number % 2}"),
                        body=f"历史定位 会话{index} 编号{number:04d}。\n\n发布确认记录：先查看附近的讨论，再核对灰度安排和回滚责任人。\n\n"
                        + "记录中的日期和讨论内容保持原样。" * 5,
                        received_at_ms=1790784000000 + number * 1000,
                        notifies_runtime=False,
                    )
                    rows.append(message)
                    await channel.inject(message)
                messages.append(rows)
            async with asyncio.timeout(20):
                while any(
                    [
                        len(await node.agents[agent_id].storage.list_messages(thread))
                        < 100
                        for thread in threads
                    ]
                ):
                    await asyncio.sleep(0.05)
            for thread in threads:
                await node.agents[agent_id].orchestrator.command_service.review_contact(
                    thread, Review.APPROVED
                )
            empty_sessions = [str(uuid7()), str(uuid7())]
            empty_refs = []
            identity = channel.get_identity()
            assert identity is not None
            for empty_session in empty_sessions:
                empty_channel = ChannelSession(
                    id=str(
                        uuid5(
                            NAMESPACE_URL,
                            f"bcn:{agent_id}:channel-session:{empty_session}",
                        )
                    ),
                    channel="test",
                    channel_identity=identity.id,
                    provider_thread_id=empty_session,
                    target_kind=ChannelTargetKind.DM,
                    created_at_ms=1790784000000,
                    updated_at_ms=1790784000000,
                    review=Review.APPROVED,
                )
                empty_thread = str(
                    uuid5(NAMESPACE_URL, f"bcn:{agent_id}:bcn-session:{empty_session}")
                )
                await node.agents[agent_id].storage.save_channel_session(empty_channel)
                await node.agents[agent_id].storage.save_thread(
                    Thread(
                        id=empty_thread,
                        channel_session_id=empty_channel.id,
                        workspace_id=agent_id,
                        created_at_ms=1790784000000,
                        updated_at_ms=1790784000000,
                    )
                )
                empty_refs.append(await _short(server.state.storage, empty_thread))
            scope = await _short(server.state.storage, enrolled.computer.id, agent_id)
            async with playwright.async_playwright() as driver:
                browser = await driver.chromium.launch(
                    executable_path="/usr/bin/microsoft-edge", headless=True
                )
                try:
                    for width, language, theme, reduced in [
                        (390, "zh-CN", "dark", True),
                        (1280, "en-US", "light", False),
                    ]:
                        context = await browser.new_context(
                            viewport={
                                "width": width,
                                "height": 900 if width > 959 else 844,
                            },
                            locale=language,
                            has_touch=width <= 959,
                            reduced_motion="reduce" if reduced else "no-preference",
                        )
                        page = await context.new_page()
                        errors = []
                        traffic = []
                        page.on(
                            "pageerror",
                            lambda error, errors=errors: errors.append(str(error)),
                        )
                        page.on(
                            "request",
                            lambda request, traffic=traffic: traffic.append(
                                request.url.removeprefix(base)
                            ),
                        )
                        await page.goto(base + "/login")
                        await page.locator("#name").fill(TESTER[0])
                        await page.locator("#password").fill(TESTER[1])
                        await page.locator("#login button").click()
                        await page.wait_for_url("**/agents")
                        await page.goto(f"{base}/agents/{scope}")
                        await page.evaluate(
                            "theme => document.documentElement.dataset.theme=theme",
                            theme,
                        )
                        await page.locator(".contacts-toolbar .search-open").click()
                        await page.locator("#search-query").fill(
                            "历史定位 会话0 编号0020"
                        )
                        await settled(page)
                        await playwright.expect(
                            page.locator(".search-hit")
                        ).to_be_visible()
                        source = messages[0][20].message_id
                        if width <= 959:
                            await page.locator(".search-hit").tap()
                        else:
                            await page.locator(".search-hit").click()
                        target = page.locator(f"#message-{source}")
                        await playwright.expect(target).to_be_visible()
                        await page.wait_for_function(
                            "() => document.querySelector('#contacts .li.on')?.id === 'contact-'+document.querySelector('#chat').dataset.thread"
                        )
                        await playwright.expect(target).to_have_class(
                            re.compile("message-focus")
                        )
                        await page.screenshot(
                            path=str(out / f"history-focused-{width}.png")
                        )
                        original = await target.text_content()
                        await page.wait_for_function(
                            "() => !document.querySelector('.message-focus')"
                        )
                        anchor = await page.evaluate("Alpine.store('history').reading")
                        await playwright.expect(target).to_be_visible()
                        assert await target.text_content() == original
                        assert anchor
                        # A real whole-history read rebuilds around the current reading anchor.
                        await page.evaluate(
                            "() => {const el=document.querySelector('#history');return htmx.ajax('GET', el.dataset.url, {source:el,target:el,swap:'outerHTML'})}"
                        )
                        await playwright.expect(target).to_be_visible()
                        await page.wait_for_function(
                            "anchor => {const h=document.querySelector('#history');const row=document.getElementById('message-'+anchor.message);return row && Math.abs(row.getBoundingClientRect().top-h.getBoundingClientRect().top-anchor.offset)<2}",
                            arg=anchor,
                        )
                        # Locate a loaded hit without replacing the conversation/history.
                        await page.evaluate(
                            "window.historyBeforeFocus=document.querySelector('#history')"
                        )
                        await page.locator("#chat .search-open").click()
                        await page.locator(".search-hit").click()
                        await playwright.expect(target).to_have_class(
                            re.compile("message-focus")
                        )
                        assert await page.evaluate(
                            "window.historyBeforeFocus===document.querySelector('#history')"
                        )
                        await page.wait_for_function(
                            "() => !document.querySelector('.message-focus')"
                        )
                        anchor = await page.evaluate("Alpine.store('history').reading")
                        # Keep actual native HTTP responses, and delay consumption to overlap navigation.
                        await page.evaluate("""() => {
                          window.held=false;window.releaseHistory=null;
                          const hold=event => {const ctx=event.detail.ctx;if(ctx.target?.id!=='history')return;
                            document.removeEventListener('htmx:before:request',hold);
                            const fetch=ctx.fetch;
                            ctx.fetch=async(...args)=>{const response=await fetch(...args);await response.clone().text();window.held=true;await new Promise(resolve=>window.releaseHistory=resolve);return response;};
                          };
                          document.addEventListener('htmx:before:request',hold);
                          const el=document.querySelector('#history');void htmx.ajax('GET',el.dataset.url,{source:el,target:el,swap:'outerHTML'});
                        }""")
                        await page.wait_for_function("window.held")
                        await page.locator("#chat .search-open").click()
                        await page.locator("#search-query").fill(
                            "历史定位 会话0 编号0070"
                        )
                        await settled(page)
                        await page.locator(".search-hit").click()
                        current = messages[0][70].message_id
                        await playwright.expect(
                            page.locator(f"#message-{current}")
                        ).to_be_visible()
                        await page.evaluate("window.releaseHistory()")
                        await page.wait_for_function(
                            "() => !document.querySelector('.message-focus')"
                        )
                        await playwright.expect(
                            page.locator(f"#message-{current}")
                        ).to_be_visible()
                        await page.locator("#history").evaluate(
                            "el => {el.scrollTop=0;el.dispatchEvent(new Event('scroll'))}"
                        )
                        anchor = await page.evaluate("Alpine.store('history').reading")
                        await playwright.expect(
                            page.locator(f"#message-{messages[0][20].message_id}")
                        ).to_be_attached()
                        await page.wait_for_function(
                            "anchor => {const h=document.querySelector('#history');const row=document.getElementById('message-'+anchor.message);return row && Math.abs(row.getBoundingClientRect().top-h.getBoundingClientRect().top-anchor.offset)<2}",
                            arg=anchor,
                        )
                        # Actual messages and normal five-second subscriptions append context while reading stays put.
                        for round_ in range(3):
                            message = Message(
                                direction=MessageDirection.INBOUND,
                                seq=0,
                                message_id=str(uuid7()),
                                thread_id=sessions[0],
                                channel_session_id=sessions[0],
                                channel="test",
                                provider_thread_id=sessions[0],
                                provider_message_id=str(uuid7()),
                                target=f"dm:{sessions[0]}",
                                sender=SenderIdentity(name="partner-0"),
                                body=f"阅读位置验收 {width} 第{round_}轮追加：原讨论仍保留。",
                                received_at_ms=1790874000000 + width * 10 + round_,
                                notifies_runtime=False,
                            )
                            await channel.inject(message)
                            await playwright.expect(
                                page.locator(f"#message-{message.message_id}")
                            ).to_be_attached(timeout=20000)
                            await page.wait_for_function(
                                "anchor => {const h=document.querySelector('#history');const row=document.getElementById('message-'+anchor.message);return row && Math.abs(row.getBoundingClientRect().top-h.getBoundingClientRect().top-anchor.offset)<2}",
                                arg=anchor,
                            )
                        await page.screenshot(
                            path=str(out / f"history-reading-{width}.png")
                        )
                        (out / "history-progress.json").write_text(
                            json.dumps({"width": width, "phase": "reading verified"})
                        )
                        if width > 959:
                            assert node.control is not None
                            await node.control.stop(timeout=5)
                            await node.health_reporter.stop(timeout=5)
                            (out / "history-progress.json").write_text(
                                json.dumps(
                                    {
                                        "width": width,
                                        "phase": "waiting for real offline detection",
                                    }
                                )
                            )
                            await playwright.expect(
                                page.locator("#tail .empty")
                            ).to_be_attached(timeout=140000)
                            await page.evaluate(
                                "() => {const el=document.querySelector('#history');return htmx.ajax('GET',el.dataset.url,{source:el,target:el,swap:'outerHTML'})}"
                            )
                            await playwright.expect(
                                page.locator("#history .empty").first
                            ).to_be_visible()
                            await node.control.start(timeout=5)
                            await node.health_reporter.start(timeout=5)
                            await playwright.expect(
                                page.locator(f"#message-{anchor['message']}")
                            ).to_be_visible(timeout=20000)
                            await page.wait_for_function(
                                "anchor => {const h=document.querySelector('#history');const row=document.getElementById('message-'+anchor.message);return row && Math.abs(row.getBoundingClientRect().top-h.getBoundingClientRect().top-anchor.offset)<2}",
                                arg=anchor,
                            )
                        (out / "history-progress.json").write_text(
                            json.dumps({"width": width, "phase": "recovery verified"})
                        )
                        # A focus at the loaded window's bottom still preserves reading when the tail grows.
                        await page.locator("#chat .search-open").click()
                        await page.locator("#search-query").fill(
                            f"阅读位置验收 {width} 第2轮追加"
                        )
                        await settled(page)
                        await page.locator(".search-hit").click()
                        await page.wait_for_function(
                            "() => !document.querySelector('.message-focus') && Alpine.$data(document.querySelector('#history')).atBottom"
                        )
                        bottom_anchor = await page.evaluate(
                            "Alpine.store('history').reading"
                        )
                        bottom_message = Message(
                            direction=MessageDirection.INBOUND,
                            seq=0,
                            message_id=str(uuid7()),
                            thread_id=sessions[0],
                            channel_session_id=sessions[0],
                            channel="test",
                            provider_thread_id=sessions[0],
                            provider_message_id=str(uuid7()),
                            target=f"dm:{sessions[0]}",
                            sender=SenderIdentity(name="partner-0"),
                            body=f"底部定位后追加 {width}。\n\n"
                            + "这段新增讨论不应移动正在查看的原消息。\n\n" * 12,
                            received_at_ms=1790875000000 + width,
                            notifies_runtime=False,
                        )
                        await channel.inject(bottom_message)
                        await playwright.expect(
                            page.locator(f"#message-{bottom_message.message_id}")
                        ).to_be_attached(timeout=20000)
                        await page.wait_for_function(
                            "anchor => {const h=document.querySelector('#history');const row=document.getElementById('message-'+anchor.message);return row && Math.abs(row.getBoundingClientRect().top-h.getBoundingClientRect().top-anchor.offset)<2}",
                            arg=bottom_anchor,
                        )
                        # The explicit bottom action resumes following, then ordinary navigation still opens latest.
                        await page.locator(".chat-jump").click()
                        await page.wait_for_function(
                            "() => !Alpine.store('history').reading && Alpine.$data(document.querySelector('#history')).atBottom"
                        )
                        await page.locator("#chat .search-open").click()
                        await page.locator(".search-toolbar button").click()
                        await page.locator("#search-query").fill(
                            "历史定位 会话1 编号0030"
                        )
                        await settled(page)
                        await page.locator(".search-hit").click()
                        await playwright.expect(
                            page.locator(f"#message-{messages[1][30].message_id}")
                        ).to_be_visible()
                        await page.locator("#chat .search-open").click()
                        saved = await page.evaluate(
                            "Alpine.$data(document.querySelector('#message-search')).s.query"
                        )
                        assert saved == "历史定位 会话1 编号0030"
                        await page.locator(".search-close").click()
                        if width <= 959:
                            focused_url = page.url
                            await page.locator("#chat .back").click()
                            await page.wait_for_url(f"{base}/agents/{scope}")
                            await playwright.expect(
                                page.locator(f"#contact-{empty_refs[1]}")
                            ).to_be_visible()
                            await page.go_back()
                            await page.wait_for_url(focused_url)
                            await playwright.expect(
                                page.locator(f"#message-{messages[1][30].message_id}")
                            ).to_be_visible()
                            await page.locator("#chat .search-open").click()
                            assert (
                                await page.locator("#search-query").input_value()
                                == saved
                            )
                            await page.locator(".search-close").click()
                            await page.go_forward()
                            await page.wait_for_url(f"{base}/agents/{scope}")
                            await playwright.expect(
                                page.locator(f"#contact-{empty_refs[1]}")
                            ).to_be_visible()
                            await page.go_back()
                            await page.wait_for_url(focused_url)
                            await playwright.expect(
                                page.locator(f"#message-{messages[1][30].message_id}")
                            ).to_be_visible()
                        if width <= 959:
                            await page.locator("#chat .back").click()
                            await page.wait_for_url(f"{base}/agents/{scope}")
                        empty_session = empty_sessions[0 if width > 959 else 1]
                        empty_ref = empty_refs[0 if width > 959 else 1]
                        await page.locator(f"#contact-{empty_ref}").click()
                        await playwright.expect(
                            page.locator("#history .empty").first
                        ).to_be_visible()
                        empty_message = Message(
                            direction=MessageDirection.INBOUND,
                            seq=0,
                            message_id=str(uuid7()),
                            thread_id=empty_session,
                            channel_session_id=empty_session,
                            channel="test",
                            provider_thread_id=empty_session,
                            provider_message_id=str(uuid7()),
                            target=f"dm:{empty_session}",
                            sender=SenderIdentity(name="partner-0"),
                            body=f"空会话恢复 {width}：第一条讨论已经到达。",
                            received_at_ms=1790884000000 + width,
                            notifies_runtime=False,
                        )
                        await channel.inject(empty_message)
                        await playwright.expect(
                            page.locator(f"#message-{empty_message.message_id}")
                        ).to_be_visible(timeout=20000)
                        await page.wait_for_function(
                            "() => Alpine.$data(document.querySelector('#history')).atBottom"
                        )
                        report.append(
                            {
                                "width": width,
                                "language": language,
                                "theme": theme,
                                "reduced_motion": reduced,
                                "subscription_rounds": 3,
                                "anchor": anchor,
                                "same_history_focus": True,
                                "earlier_context_loaded": True,
                                "bottom_focus_preserved": True,
                                "whole_history_restored": True,
                                "stale_response_discarded": True,
                                "offline_recovery": width > 959,
                                "empty_history_recovery": True,
                                "touch_navigation": width <= 959,
                                "back_forward_restored": width <= 959,
                                "errors": errors,
                                "traffic": traffic,
                            }
                        )
                        assert not errors, errors
                        await context.close()
                finally:
                    await browser.close()
            (out / "history-verification.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2)
            )
        finally:
            await node.stop()
