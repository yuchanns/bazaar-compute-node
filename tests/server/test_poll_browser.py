from __future__ import annotations

import asyncio
import json
import os
from collections import Counter
from pathlib import Path
from typing import cast

import pytest

from bazaar_compute_server.protocol import Event

from ._serving import TESTER, enrol, serving_app, with_password
from .test_pages import _event, _health, _short


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_subscriptions_update_widgets_and_only_fetch_changed_content(
    tmp_path: Path,
) -> None:
    playwright = pytest.importorskip("playwright.async_api")
    out = Path(os.environ.get("BCS_POLL_ARTIFACTS", str(tmp_path / "screens")))
    out.mkdir(parents=True, exist_ok=True)
    async with serving_app(tmp_path / "server") as (base, app):
        storage = app.state.storage
        await with_password(storage, *TESTER)
        computer = (await enrol(storage, "Polling workstation")).computer
        agent = {
            "agent_id": "poll-browser-agent",
            "name": "Polling colleague",
            "status": "started",
            "channels": ["test"],
            "runtimes": ["test"],
        }
        await storage.record_events(
            computer.id, "browser", [Event.model_validate(_health([agent], 1))]
        )
        refs = await _short(storage, computer.id, agent["agent_id"])
        async with playwright.async_playwright() as driver:
            browser = await driver.chromium.launch(
                executable_path="/usr/bin/microsoft-edge", headless=True
            )
            context = await browser.new_context(viewport={"width": 1280, "height": 900})
            page = await context.new_page()
            errors: list[str] = []
            traffic: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on(
                "request",
                lambda request: traffic.append(
                    request.url.removeprefix(base).split("?")[0]
                ),
            )
            await page.goto(base + "/login")
            await page.locator("#name").fill(TESTER[0])
            await page.locator("#password").fill(TESTER[1])
            await page.locator("#login button").click()
            await page.wait_for_url("**/agents")
            await page.goto(f"{base}/agents/{refs}/profile?tab=activity")
            await playwright.expect(page.locator("#profile-head .dot")).to_have_class(
                "dot running"
            )
            await asyncio.sleep(7)
            traffic.clear()
            await asyncio.sleep(30)
            quiet = Counter(traffic)
            assert (
                quiet["/poll"] >= 2
                and not quiet[f"/agents/{refs}/profile/events"]
                and not quiet["/agents/list"]
            )
            await page.evaluate(
                "window.dots = [...document.querySelectorAll('#agent-list .dot, #profile-head .dot')]"
            )
            await storage.record_events(
                computer.id,
                "browser",
                [
                    Event.model_validate(
                        _event(2, "runtime.request.turn.started", agent["agent_id"])
                    )
                ],
            )
            await playwright.expect(page.locator("#profile-head .dot")).to_have_class(
                "dot busy", timeout=10000
            )
            await playwright.expect(page.locator("#agent-list .dot")).to_have_class(
                "dot busy"
            )
            assert await page.evaluate("window.dots.every(e => e.isConnected)")
            assert (
                "/agents/list" not in traffic
                and f"/agents/{refs}/row" not in traffic
                and f"/agents/{refs}/profile/head" not in traffic
            )
            await page.screenshot(path=str(out / "shared-status-desktop.png"))
            # More than two pages arrive between checks. The visible tail must catch up.
            await storage.record_events(
                computer.id,
                "browser",
                [
                    Event.model_validate(
                        _event(
                            i + 3,
                            "tool_call.completed",
                            agent["agent_id"],
                            name=f"burst-{i}",
                        )
                    )
                    for i in range(121)
                ],
            )
            await playwright.expect(
                page.locator("#events .ln").filter(has_text="burst-120")
            ).to_be_visible(timeout=25000)
            assert (
                await page.locator("#events .ln").filter(has_text="burst-").count()
                == 121
            )
            active = Counter(traffic)
            assert not active["/agents/list"]
            # Metadata has its own small consumers.
            agent["name"] = "Renamed colleague"
            await storage.record_events(
                computer.id, "browser", [Event.model_validate(_health([agent], 125))]
            )
            await playwright.expect(
                page.locator("#profile-head .heading")
            ).to_have_text("Renamed colleague", timeout=10000)
            await playwright.expect(page.locator("#agent-list .t b")).to_have_text(
                "Renamed colleague"
            )
            assert "/agents/list" not in traffic
            await storage.record_events(
                computer.id,
                "browser",
                [
                    Event.model_validate(
                        _health(
                            [
                                agent,
                                {
                                    **agent,
                                    "agent_id": "new-agent",
                                    "name": "New teammate",
                                },
                            ],
                            126,
                        )
                    )
                ],
            )
            await playwright.expect(page.locator("#agent-list .li")).to_have_count(
                2, timeout=10000
            )
            # Leave the event stream: its subscription no longer fetches events.
            await page.locator(".switch .tab").filter(has_text="Status").click()
            await playwright.expect(page.locator("#agent-health")).to_be_visible()
            await asyncio.sleep(1)
            traffic.clear()
            await storage.record_events(
                computer.id,
                "browser",
                [
                    Event.model_validate(
                        _event(
                            127,
                            "tool_call.completed",
                            agent["agent_id"],
                            name="after leaving",
                        )
                    )
                ],
            )
            await asyncio.sleep(7)
            assert not Counter(traffic)[f"/agents/{refs}/profile/events"]
            await page.goto(base + "/computers")
            await playwright.expect(page.locator("#computer-list time")).to_be_visible()
            before = await page.locator("#computer-list time").inner_text()
            traffic.clear()
            await asyncio.sleep(12)
            after = await page.locator("#computer-list time").inner_text()
            assert before != after and not Counter(traffic)["/computers/list"]
            await page.set_viewport_size({"width": 390, "height": 844})
            await page.screenshot(path=str(out / "computer-time-mobile.png"))
            assert not errors, errors
            (out / "requests.json").write_text(
                json.dumps(
                    {
                        "quiet": dict(quiet),
                        "active": dict(active),
                        "time_before": before,
                        "time_after": after,
                    },
                    indent=2,
                )
            )
            await browser.close()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_real_node_reminders_time_and_offline_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace
    from uuid import uuid7

    from bcn_test_support.channel import TestChannel
    from bcn_test_support.runtime import TestRuntime, TestTurnPlan

    from bazaar_compute_node.core.actor import Thread
    from bazaar_compute_node.core.channel import Channel
    from bazaar_compute_node.core.reminder import (
        ReminderCancelRequest,
        ReminderListRequest,
        ReminderScheduleRequest,
        ReminderSnoozeRequest,
        ReminderUpdateRequest,
    )
    from bazaar_compute_server.clock import now_ms

    from ._node import AGENT_ID, node_reporting_to
    from .test_review import _stranger

    playwright = pytest.importorskip("playwright.async_api")
    out = Path(os.environ.get("BCS_POLL_ARTIFACTS", str(tmp_path / "screens")))
    out.mkdir(parents=True, exist_ok=True)
    async with serving_app(tmp_path / "server") as (base, app):
        storage = app.state.storage
        await with_password(storage, *TESTER)
        enrolled = await enrol(storage, "Reminder test computer")
        monkeypatch.setenv("BCN_SERVER_TOKEN", enrolled.token)
        directory = tmp_path / "node"
        directory.mkdir()
        node = await node_reporting_to(base, directory)
        running = True
        try:
            agent = node.agents[AGENT_ID]
            channel = cast(
                TestChannel, cast(Channel, agent.channel.members[0])._channel
            )
            runtime = cast(TestRuntime, agent.runtimes[0])
            runtime.command_service = agent.orchestrator.command_service
            message = replace(
                _stranger(1, str(uuid7())),
                received_at_ms=now_ms(),
                body="我们明天要复核方案，先帮我记下今天讨论的背景。",
                mentions_agent=True,
            )
            await channel.inject(message)
            async with asyncio.timeout(15):
                while not (await storage.computer_health([enrolled.computer]))[
                    0
                ].health:
                    await asyncio.sleep(0.05)
            refs = await _short(storage, enrolled.computer.id, AGENT_ID)
            async with playwright.async_playwright() as driver:
                browser = await driver.chromium.launch(
                    executable_path="/usr/bin/microsoft-edge", headless=True
                )
                context = await browser.new_context(
                    viewport={"width": 1280, "height": 900}
                )
                page = await context.new_page()
                errors = []
                traffic = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on(
                    "request",
                    lambda request: traffic.append(
                        request.url.removeprefix(base).split("?")[0]
                    ),
                )
                await page.goto(base + "/login")
                await page.locator("#name").fill(TESTER[0])
                await page.locator("#password").fill(TESTER[1])
                await page.locator("#login button").click()
                await page.wait_for_url("**/agents")
                await page.goto(f"{base}/agents/{refs}?review=pending")
                await page.locator("#contacts a.li").first.click()
                await page.locator('#chat button[value="approved"]').click()
                await playwright.expect(page.locator("#history")).to_be_visible()
                await page.locator("#chat .face").click()
                await playwright.expect(page.locator("#profile")).to_be_visible()
                await page.evaluate(
                    "window.drawer = document.querySelector('#profile')"
                )

                async def respond(commands, thread_id):
                    checked = await commands.check_messages(Thread(thread_id))
                    await commands.send_message(
                        actor=Thread(thread_id),
                        raw_target=checked[0].messages[0].target,
                        body="已记下方案背景，明天继续复核。",
                        created_at_ms=now_ms(),
                    )
                    await commands.schedule_reminder(
                        Thread(thread_id),
                        ReminderScheduleRequest(
                            title="复核方案背景",
                            message_id=checked[0].messages[0].message_id,
                            next_fire_at_ms=now_ms() + 3600000,
                            repeat_rule=None,
                            timezone="Asia/Shanghai",
                        ),
                    )

                runtime.queue_turn_plan(TestTurnPlan(command_script=respond))
                await channel.inject(
                    replace(
                        message,
                        seq=2,
                        message_id=str(uuid7()),
                        provider_message_id="follow-up",
                        received_at_ms=now_ms(),
                        body="背景补充好了，明天请提醒我复核方案。",
                    )
                )
                await playwright.expect(
                    page.locator("#conversation-reminders")
                ).to_contain_text("复核方案背景", timeout=20000)
                assert await page.evaluate(
                    "window.drawer === document.querySelector('#profile')"
                )
                await page.screenshot(path=str(out / "live-reminder.png"))
                # Each mutation passes through the live runtime command service.
                for seq, action, body in (
                    (3, "update", "明天改为复核发布方案，提醒标题也改一下。"),
                    (4, "snooze", "复核延后一天，请调整提醒时间。"),
                    (5, "cancel", "方案已经提前确认了，取消那条复核提醒吧。"),
                ):

                    async def revise(commands, thread_id, action=action):
                        await commands.check_messages(Thread(thread_id))
                        reminders = await commands.list_reminders(
                            Thread(thread_id), ReminderListRequest()
                        )
                        reminder = reminders.reminders[0]
                        if action == "update":
                            await commands.update_reminder(
                                Thread(thread_id),
                                ReminderUpdateRequest(
                                    reminder_id=reminder.reminder_id,
                                    evaluated_at_ms=now_ms(),
                                    title="复核发布方案",
                                ),
                            )
                        elif action == "snooze":
                            await commands.snooze_reminder(
                                Thread(thread_id),
                                ReminderSnoozeRequest(
                                    reminder_id=reminder.reminder_id,
                                    evaluated_at_ms=now_ms(),
                                    duration_ms=86400000,
                                ),
                            )
                        else:
                            await commands.cancel_reminder(
                                Thread(thread_id),
                                ReminderCancelRequest(
                                    reminder_id=reminder.reminder_id,
                                    evaluated_at_ms=now_ms(),
                                ),
                            )

                    before_time = await page.locator(
                        "#conversation-reminders time"
                    ).get_attribute("data-time")
                    runtime.queue_turn_plan(TestTurnPlan(command_script=revise))
                    await channel.inject(
                        replace(
                            message,
                            seq=seq,
                            message_id=str(uuid7()),
                            provider_message_id=f"revision-{seq}",
                            received_at_ms=now_ms(),
                            body=body,
                        )
                    )
                    if action == "update":
                        await playwright.expect(
                            page.locator("#conversation-reminders")
                        ).to_contain_text("复核发布方案", timeout=20000)
                    elif action == "snooze":
                        async with asyncio.timeout(20):
                            while (
                                await page.locator(
                                    "#conversation-reminders time"
                                ).get_attribute("data-time")
                                == before_time
                            ):
                                await asyncio.sleep(0.1)
                    else:
                        await playwright.expect(
                            page.locator("#conversation-reminders .row")
                        ).to_have_count(0, timeout=20000)
                    assert await page.evaluate(
                        "window.drawer === document.querySelector('#profile')"
                    )
                await page.locator("#profile .hd button").click()
                await asyncio.sleep(6)
                before = await page.locator("#contacts time").first.inner_text()
                traffic.clear()
                await asyncio.sleep(12)
                after = await page.locator("#contacts time").first.inner_text()
                assert before != after
                assert not any(
                    url.endswith(("/contacts", "/reminders")) for url in traffic
                )
                await context.set_offline(True)
                await playwright.expect(page.locator("#unreachable")).to_be_visible(
                    timeout=12000
                )
                await context.set_offline(False)
                await playwright.expect(page.locator("#history")).to_be_visible(
                    timeout=12000
                )
                # An actual stopped test node crosses the production heartbeat deadline.
                await node.stop()
                running = False
                await playwright.expect(page.locator("#agent-list .dot")).to_have_class(
                    "dot offline", timeout=140000
                )
                await page.screenshot(path=str(out / "node-offline.png"))
                node = await node_reporting_to(base, directory)
                running = True
                await playwright.expect(page.locator("#agent-list .dot")).to_have_class(
                    "dot running", timeout=20000
                )
                await page.screenshot(path=str(out / "node-recovered.png"))
                assert not errors, errors
                await browser.close()
        finally:
            if running:
                await node.stop()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_browser_keeps_its_cursors_across_server_restart(tmp_path: Path) -> None:
    from ._serving import free_port

    playwright = pytest.importorskip("playwright.async_api")
    port = free_port()
    server = serving_app(tmp_path / "server", port)
    base, app = await server.__aenter__()
    live = True
    try:
        storage = app.state.storage
        await with_password(storage, *TESTER)
        computer = (await enrol(storage, "Restart workstation")).computer
        agent = {
            "agent_id": "restart-agent",
            "name": "Restart colleague",
            "status": "started",
            "channels": [],
            "runtimes": ["test"],
        }
        await storage.record_events(
            computer.id, "restart", [Event.model_validate(_health([agent], 1))]
        )
        async with playwright.async_playwright() as driver:
            browser = await driver.chromium.launch(
                executable_path="/usr/bin/microsoft-edge", headless=True
            )
            page = await browser.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.goto(base + "/login")
            await page.locator("#name").fill(TESTER[0])
            await page.locator("#password").fill(TESTER[1])
            await page.locator("#login button").click()
            await page.wait_for_url("**/agents")
            await playwright.expect(page.locator("#agent-list .dot")).to_have_class(
                "dot running"
            )
            await page.evaluate(
                "window.row = document.querySelector('#agent-list .li')"
            )
            await server.__aexit__(None, None, None)
            live = False
            await playwright.expect(page.locator("#unreachable")).to_be_visible(
                timeout=10000
            )
            server = serving_app(tmp_path / "server", port)
            _, app = await server.__aenter__()
            live = True
            await app.state.storage.record_events(
                computer.id,
                "restart",
                [
                    Event.model_validate(
                        _event(2, "runtime.request.turn.started", agent["agent_id"])
                    )
                ],
            )
            await playwright.expect(page.locator("#agent-list .dot")).to_have_class(
                "dot busy", timeout=10000
            )
            await playwright.expect(page.locator("#agent-list")).to_be_visible()
            assert await page.evaluate(
                "window.row === document.querySelector('#agent-list .li')"
            )
            # An old page uses the existing Stale middleware and stops polling.
            await page.evaluate("document.body.dataset.build = 'older-build'")
            await playwright.expect(page.locator("#stale")).to_be_visible(timeout=10000)
            requests = []
            page.on("request", lambda request: requests.append(request.url))
            await asyncio.sleep(7)
            assert not any(url.endswith("/poll") for url in requests)
            assert not errors, errors
            await browser.close()
    finally:
        if live:
            await server.__aexit__(None, None, None)


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_health_usage_and_computer_queue_follow_reports(tmp_path: Path) -> None:
    playwright = pytest.importorskip("playwright.async_api")
    async with serving_app(tmp_path / "server") as (base, app):
        storage = app.state.storage
        await with_password(storage, *TESTER)
        computer = (await enrol(storage, "Health workstation")).computer
        agent = {
            "agent_id": "health-agent",
            "name": "Health colleague",
            "status": "started",
            "channels": ["test"],
            "runtimes": ["test"],
        }
        await storage.record_events(
            computer.id, "health-browser", [Event.model_validate(_health([agent], 1))]
        )
        refs = await _short(storage, computer.id, agent["agent_id"])
        async with playwright.async_playwright() as driver:
            browser = await driver.chromium.launch(
                executable_path="/usr/bin/microsoft-edge", headless=True
            )
            page = await browser.new_page()
            await page.goto(base + "/login")
            await page.locator("#name").fill(TESTER[0])
            await page.locator("#password").fill(TESTER[1])
            await page.locator("#login button").click()
            await page.wait_for_url("**/agents")
            await page.goto(f"{base}/agents/{refs}/profile?tab=status")
            await playwright.expect(page.locator("#agent-health")).to_be_visible()
            report = _health(
                [
                    {
                        **agent,
                        "orchestrator_health": {
                            "background_failures": {"worker": "connection lost"}
                        },
                    }
                ],
                2,
            )
            await storage.record_events(
                computer.id,
                "health-browser",
                [
                    Event.model_validate(report),
                    Event.model_validate(
                        _event(
                            3,
                            "usage.updated",
                            agent["agent_id"],
                            total={
                                "input_tokens": 1000,
                                "output_tokens": 200,
                                "cached_input_tokens": 34,
                            },
                            cost_usd=0.5,
                        )
                    ),
                ],
            )
            await playwright.expect(page.locator("#agent-health")).to_contain_text(
                "connection lost", timeout=10000
            )
            await playwright.expect(page.locator("#agent-health")).to_contain_text(
                "$0.50", timeout=10000
            )
            await page.goto(f"{base}/computers/{refs.split('/')[0]}")
            await playwright.expect(page.locator("#computer-view")).to_be_visible()
            await page.evaluate(
                "window.computer = document.querySelector('#computer-view'); window.computerRow = document.querySelector('#computer-list .li')"
            )
            report = _health([agent], 4)
            report["metadata"]["audit"]["queued"] = 7
            await storage.record_events(
                computer.id, "health-browser", [Event.model_validate(report)]
            )
            await playwright.expect(
                page.locator('#computer-view [x-show="queued"]')
            ).to_contain_text("7", timeout=10000)
            assert await page.evaluate(
                "window.computer === document.querySelector('#computer-view') && window.computerRow === document.querySelector('#computer-list .li')"
            )
            await browser.close()
