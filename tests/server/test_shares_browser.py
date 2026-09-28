"""Role sharing through real browsers, SQLite, and a TestChannel-driven node."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import cast
from uuid import uuid7

import pytest
from bcn_test_support.channel import TestChannel

from bazaar_compute_node.core.channel import Channel
from bazaar_compute_server.clock import now_ms
from bazaar_compute_server.permissions import Permission as P
from bazaar_compute_server.polling import TOPICS
from bazaar_compute_server.storage import Role

from ._node import AGENT_ID, node_reporting_to
from ._serving import TESTER, enrol, serving_app, with_password
from .test_pages import _short
from .test_review import _stranger


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_role_sharing_and_member_actions_in_the_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    playwright = pytest.importorskip("playwright.async_api")
    expect = playwright.expect
    async with serving_app(tmp_path / "server") as (base, app):
        storage = app.state.storage
        await with_password(storage, *TESTER)
        await with_password(storage, "colleague", TESTER[1])
        member = await storage.find_account("colleague")
        assert member is not None
        reader = Role(
            str(uuid7()),
            "Readers",
            frozenset({P.COMPUTERS_VIEW, P.AGENTS_VIEW}),
            now_ms(),
            now_ms(),
        )
        reviewer = Role(
            str(uuid7()),
            "Reviewers",
            frozenset({P.AGENTS_APPROVE, P.AGENTS_UPDATE}),
            now_ms(),
            now_ms(),
        )
        await storage.save_role(reader)
        await storage.save_role(reviewer)
        await storage.set_account_roles(member.id, [reader.id, reviewer.id])
        await with_password(storage, "resource-owner", TESTER[1])
        enrolled = await enrol(storage, "Shared workstation", owner="resource-owner")
        monkeypatch.setenv("BCN_SERVER_TOKEN", enrolled.token)
        directory = tmp_path / "node"
        directory.mkdir()
        node = await node_reporting_to(base, directory)
        try:
            agent = node.agents[AGENT_ID]
            channel = cast(
                TestChannel, cast(Channel, agent.channel.members[0])._channel
            )
            await channel.inject(
                replace(
                    _stranger(1, str(uuid7())),
                    received_at_ms=now_ms(),
                    body="我们正在准备项目评审，想请你帮忙整理讨论记录。",
                    mentions_agent=True,
                )
            )
            async with asyncio.timeout(15):
                while not (await storage.computer_health([enrolled.computer]))[
                    0
                ].health:
                    await asyncio.sleep(0.05)
            key = await _short(storage, enrolled.computer.id, AGENT_ID)
            computer_key = key.split("/")[0]
            async with playwright.async_playwright() as driver:
                browser = await driver.chromium.launch(
                    executable_path="/usr/bin/microsoft-edge", headless=True
                )
                admin = await browser.new_context(
                    locale="en-US", viewport={"width": 1360, "height": 1000}
                )
                colleague = await browser.new_context(
                    locale="en-US", viewport={"width": 1360, "height": 1000}
                )
                owner = await admin.new_page()
                viewer = await colleague.new_page()
                for page, name in ((owner, TESTER[0]), (viewer, member.name)):
                    await page.goto(base + "/login")
                    await page.locator("#name").fill(name)
                    await page.locator("#password").fill(TESTER[1])
                    await page.locator("#login button").click()
                    await page.wait_for_url("**/agents")
                await expect(
                    owner.locator(f'a[href="/agents/{key}"]').first
                ).to_be_visible()
                await owner.goto(base + "/computers")
                await expect(
                    owner.get_by_text("Shared workstation", exact=True).first
                ).to_be_visible()
                await owner.goto(f"{base}/agents/{key}/profile")
                await owner.locator("#agent-name").fill(
                    "Administrator managed assistant"
                )
                await owner.get_by_role("button", name="Save", exact=True).click()
                await expect(owner.get_by_text("Saved.", exact=True)).to_be_visible()
                await owner.goto(f"{base}/computers/{computer_key}")
                await owner.get_by_role("button", name="Sharing", exact=True).click()
                await expect(owner.locator(".role-sharing")).to_be_visible()
                for label in ("View computers",):
                    await owner.get_by_label(label, exact=True).check()
                await (
                    owner.locator(".share-list")
                    .first.get_by_label("Readers", exact=True)
                    .check()
                )
                await owner.get_by_role(
                    "button", name="Share with selected roles", exact=True
                ).click()
                await (
                    owner.locator(".role-sharing")
                    .get_by_role("button", name="Save", exact=True)
                    .click()
                )
                await expect(
                    owner.locator('.role-sharing input[name="changes"]')
                ).to_have_value('{"grants":{}}')
                await owner.screenshot(path=str(tmp_path / "share-computer.png"))
                await viewer.goto(f"{base}/computers/{computer_key}")
                await expect(
                    viewer.get_by_text("Shared workstation", exact=True).first
                ).to_be_visible()
                await owner.goto(f"{base}/agents/{key}/profile?tab=sharing")
                await (
                    owner.locator(".share-list")
                    .first.get_by_label("Readers", exact=True)
                    .check()
                )
                await owner.get_by_role(
                    "button", name="Share with selected roles", exact=True
                ).click()
                await owner.get_by_role("button", name="Save", exact=True).click()
                await expect(
                    owner.locator('.role-sharing input[name="changes"]')
                ).to_have_value('{"grants":{}}')
                await viewer.goto(f"{base}/agents/{key}/profile")
                await expect(viewer.locator("#agent-name")).to_be_disabled()
                # The administrator reviews contacts; the recipient browses
                # the approved conversation through its viewing grant.
                await owner.goto(f"{base}/agents/{key}?review=pending")
                await owner.locator('#contacts a[href*="/contacts/"]').first.click()
                await owner.get_by_role("button", name="Allow", exact=True).click()
                await expect(owner.locator("#history")).to_be_visible()
                await viewer.goto(f"{base}/agents/{key}?review=approved")
                await viewer.locator('#contacts a[href*="/contacts/"]').first.click()
                await expect(viewer.locator("#history")).to_be_visible()

                thread_key = str((await storage.shorten(["stranger"]))[0])
                fields = {
                    "computer": computer_key,
                    "agent": key.split("/")[1],
                    "thread": thread_key,
                    "review": "approved",
                }
                subscriptions = [
                    {
                        "id": topic,
                        "scope": {
                            "topic": topic,
                            **{field: fields[field] for field in descriptor.required},
                        },
                        "seen": None,
                    }
                    for topic, descriptor in TOPICS.items()
                ]
                polled = await colleague.request.post(
                    base + "/poll", data={"subscriptions": subscriptions}
                )
                assert polled.ok
                assert all(
                    "state" in change for change in (await polled.json())["changes"]
                )

                polled = await admin.request.post(
                    base + "/poll", data={"subscriptions": subscriptions}
                )
                assert polled.ok
                assert all(
                    "state" in change for change in (await polled.json())["changes"]
                )

                # An explicit per-agent grant enables editing without changing
                # the shared computer's other actions or the member's roles.
                await owner.goto(f"{base}/agents/{key}/profile?tab=sharing")
                await owner.get_by_label("View", exact=True).uncheck()
                await owner.get_by_label("Edit", exact=True).check()
                await (
                    owner.locator(".share-list")
                    .first.get_by_label("Reviewers", exact=True)
                    .check()
                )
                await owner.get_by_role(
                    "button", name="Share with selected roles", exact=True
                ).click()
                await (
                    owner.locator("#agent-sharing")
                    .get_by_role("button", name="Save", exact=True)
                    .click()
                )
                await expect(
                    owner.locator('#agent-sharing input[name="changes"]')
                ).to_have_value('{"grants":{}}')
                await viewer.goto(f"{base}/agents/{key}/profile")
                await expect(viewer.locator("#agent-name")).to_be_enabled()
                await viewer.locator("#agent-name").fill("Shared assistant")
                await viewer.get_by_role("button", name="Save", exact=True).click()
                await expect(viewer.get_by_text("Saved.", exact=True)).to_be_visible()

                # Revoke the computer share while keeping explicit agent grants.
                await owner.set_viewport_size({"width": 390, "height": 844})
                await owner.goto(f"{base}/computers/{computer_key}")
                await owner.get_by_role("button", name="Sharing", exact=True).click()
                await (
                    owner.locator(".share-list")
                    .last.locator(".share-role")
                    .filter(has_text="Readers")
                    .get_by_role("checkbox")
                    .check()
                )
                await owner.screenshot(path=str(tmp_path / "share-mobile.png"))
                await owner.get_by_role(
                    "button", name="Revoke selected shares", exact=True
                ).click()
                await (
                    owner.locator(".role-sharing")
                    .get_by_role("button", name="Save", exact=True)
                    .click()
                )
                await expect(
                    owner.locator('.role-sharing input[name="changes"]')
                ).to_have_value('{"grants":{}}')
                await viewer.goto(base + "/agents")
                await viewer.locator(f'a[href="/agents/{key}"]').first.click()
                await expect(viewer.locator("#agent-contacts-head")).to_be_visible()
                await viewer.goto(f"{base}/agents/{key}/profile")
                await expect(viewer.locator("#agent-name")).to_be_enabled()
                await viewer.locator("#agent-name").fill("Agent-only assistant")
                await viewer.get_by_role("button", name="Save", exact=True).click()
                await expect(viewer.get_by_text("Saved.", exact=True)).to_be_visible()
                polled = await colleague.request.post(
                    base + "/poll", data={"subscriptions": subscriptions}
                )
                changes = {
                    change["id"]: change for change in (await polled.json())["changes"]
                }
                for topic, descriptor in TOPICS.items():
                    if descriptor.permission.kind == "computer":
                        assert changes[topic].get("unavailable")
                    else:
                        assert "state" in changes[topic]
                await viewer.goto(base + "/computers")
                await expect(viewer.locator("#computer-view")).to_have_count(0)
                await browser.close()
        finally:
            await node.stop()


@pytest.mark.e2e
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["agent", "computer"])
async def test_share_transfer_and_saved_roles(tmp_path: Path, kind: str) -> None:
    from bazaar_compute_server.protocol import Event

    playwright = pytest.importorskip("playwright.async_api")
    expect = playwright.expect
    async with serving_app(tmp_path / "server") as (base, app):
        storage = app.state.storage
        await with_password(storage, *TESTER)
        enrolled = await enrol(storage, "Design workstation")
        await storage.record_events(
            enrolled.computer.id,
            "run",
            [
                Event.model_validate(
                    {
                        "seq": 1,
                        "event_name": "node.health",
                        "state": "completed",
                        "created_at_ms": now_ms(),
                        "correlation": {},
                        "metadata": {
                            "agents": [
                                {
                                    "agent_id": "transfer-agent",
                                    "name": "CloudStrife",
                                    "status": "started",
                                }
                            ]
                        },
                    }
                )
            ],
        )
        for name in ("运营人员", "内容编辑", "协作成员"):
            await storage.save_role(
                Role(
                    str(uuid7()),
                    name,
                    frozenset({P.AGENTS_VIEW, P.AGENTS_UPDATE, P.AGENTS_DELETE}),
                    now_ms(),
                    now_ms(),
                )
            )
        key = await _short(storage, enrolled.computer.id, "transfer-agent")
        async with playwright.async_playwright() as driver:
            browser = await driver.chromium.launch(
                executable_path="/usr/bin/microsoft-edge", headless=True
            )
            context = await browser.new_context(
                locale="zh-CN", viewport={"width": 1360, "height": 900}
            )
            page = await context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.goto(base + "/login")
            await page.locator("#name").fill(TESTER[0])
            await page.locator("#password").fill(TESTER[1])
            await page.locator("#login button").click()
            await page.wait_for_url("**/agents")
            if kind == "agent":
                await page.goto(f"{base}/agents/{key}/profile?tab=activity")
            else:
                await page.goto(f"{base}/computers/{key.split('/')[0]}")
            await page.get_by_role("button", name="分享", exact=True).click()
            await expect(page.locator(".role-sharing")).to_be_visible()
            left = page.locator(".share-list").first
            right = page.locator(".share-list").last
            await page.get_by_label(
                "修改" if kind == "agent" else "创建智能体", exact=True
            ).check()
            for name in ("运营人员", "内容编辑"):
                await left.get_by_label(name, exact=True).check()
            await page.get_by_role("button", name="分享给选中角色", exact=True).click()
            await expect(right.get_by_text("运营人员", exact=True)).to_be_visible()
            await expect(right.get_by_text("内容编辑", exact=True)).to_be_visible()
            await (
                page.locator(".role-sharing")
                .get_by_role("button", name="保存", exact=True)
                .click()
            )
            await expect(
                page.locator('.role-sharing input[name="changes"]')
            ).to_have_value('{"grants":{}}')
            await page.reload()
            await expect(right.get_by_text("运营人员", exact=True)).to_be_visible()
            await expect(right.get_by_text("内容编辑", exact=True)).to_be_visible()
            # Update two roles together, then restore the pending change.
            await right.get_by_label("全选", exact=True).check()
            await page.get_by_label(
                "审核联系人" if kind == "agent" else "删除电脑", exact=True
            ).check()
            await page.get_by_role("button", name="修改权限", exact=True).click()
            await page.get_by_role("button", name="取消", exact=True).click()
            await expect(
                page.get_by_role("button", name="保存", exact=True)
            ).to_be_disabled()
            await right.get_by_label("全选", exact=True).check()
            await page.get_by_role("button", name="修改权限", exact=True).click()
            await page.get_by_role("button", name="保存", exact=True).click()
            await expect(
                page.locator('.role-sharing input[name="changes"]')
            ).to_have_value('{"grants":{}}')
            await page.reload()
            await expect(right.locator(".share-summary").first).to_contain_text(
                "审核联系人" if kind == "agent" else "删除电脑"
            )
            # Search, cancel leaving an unsaved grant, and revoke a saved role.
            await page.get_by_label("搜索未分享的角色").fill("协作")
            await left.get_by_label("协作成员", exact=True).check()
            await page.get_by_role("button", name="分享给选中角色", exact=True).click()

            if kind == "computer":
                await page.locator("#computer-view").evaluate(
                    "el => htmx.trigger(el, 'poll-refresh')"
                )
                await expect(right.get_by_text("协作成员", exact=True)).to_be_visible()
                await expect(
                    page.get_by_role("button", name="保存", exact=True)
                ).to_be_enabled()

            async def stay(dialog):
                await dialog.dismiss()

            page.on("dialog", stay)
            await page.get_by_role(
                "button", name="活动" if kind == "agent" else "概览", exact=True
            ).click()
            await expect(page.locator(".role-sharing")).to_be_visible()
            page.remove_listener("dialog", stay)
            await page.get_by_role("button", name="取消", exact=True).click()
            await (
                right.locator(".share-role")
                .filter(has_text="内容编辑")
                .get_by_role("checkbox")
                .check()
            )
            await page.get_by_role(
                "button", name="撤回选中角色的分享", exact=True
            ).click()
            await page.get_by_role("button", name="保存", exact=True).click()
            await expect(
                page.locator('.role-sharing input[name="changes"]')
            ).to_have_value('{"grants":{}}')
            await page.reload()
            await expect(left.get_by_label("内容编辑", exact=True)).to_be_visible()
            await left.get_by_label("协作成员", exact=True).check()
            await page.screenshot(
                path=str(tmp_path / "sharing-desktop.png"), full_page=True
            )
            await page.set_viewport_size({"width": 390, "height": 844})
            await page.screenshot(
                path=str(tmp_path / "sharing-mobile.png"), full_page=True
            )
            assert await page.evaluate(
                "document.documentElement.scrollWidth <= window.innerWidth"
            )
            for width in (360, 390):
                await page.set_viewport_size({"width": width, "height": 844})
                await page.get_by_role(
                    "button", name="分享给选中角色", exact=True
                ).click()
                await page.get_by_role("button", name="保存", exact=True).click()
                await expect(
                    page.locator('.role-sharing input[name="changes"]')
                ).to_have_value('{"grants":{}}')
                await page.reload()
                await (
                    right.locator(".share-role")
                    .filter(has_text="协作成员")
                    .get_by_role("checkbox")
                    .check()
                )
                await page.get_by_role(
                    "button", name="撤回选中角色的分享", exact=True
                ).click()
                await page.get_by_role("button", name="保存", exact=True).click()
                await expect(
                    page.locator('.role-sharing input[name="changes"]')
                ).to_have_value('{"grants":{}}')
                await page.reload()
                await left.get_by_label("协作成员", exact=True).check()
                assert await page.evaluate(
                    "document.documentElement.scrollWidth <= window.innerWidth"
                )
            await right.scroll_into_view_if_needed()
            await page.screenshot(
                path=str(tmp_path / "sharing-mobile-bottom.png"), full_page=True
            )
            assert not errors
            await browser.close()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_computer_sharer_preserves_administrator_grants(tmp_path: Path) -> None:
    from bazaar_compute_server.storage import RoleShare

    playwright = pytest.importorskip("playwright.async_api")
    expect = playwright.expect
    async with serving_app(tmp_path / "server") as (base, app):
        storage = app.state.storage
        await with_password(storage, "coordinator", TESTER[1])
        account = await storage.find_account("coordinator")
        assert account is not None
        role = Role(
            str(uuid7()),
            "Coordinators",
            frozenset({P.COMPUTERS_VIEW, P.COMPUTERS_SHARE}),
            now_ms(),
            now_ms(),
        )
        recipients = Role(
            str(uuid7()),
            "Review team",
            frozenset({P.COMPUTERS_VIEW, P.AGENTS_CREATE}),
            now_ms(),
            now_ms(),
        )
        await storage.save_role(role)
        await storage.save_role(recipients)
        await storage.set_account_roles(account.id, [role.id])
        computer = (
            await enrol(storage, "Project workstation", owner=account.name)
        ).computer
        await storage.save_role_share(
            RoleShare(
                recipients.id,
                "computer",
                computer.id,
                frozenset({P.COMPUTERS_VIEW, P.AGENTS_CREATE}),
            )
        )
        key = (await storage.shorten([computer.id]))[0]
        async with playwright.async_playwright() as driver:
            browser = await driver.chromium.launch(
                executable_path="/usr/bin/microsoft-edge", headless=True
            )
            page = await browser.new_page(locale="en-US")
            try:
                await page.goto(base + "/login")
                await page.locator("#name").fill(account.name)
                await page.locator("#password").fill(TESTER[1])
                await page.locator("#login button").click()
                await page.wait_for_url("**/agents")
                await expect(
                    page.get_by_role("heading", name="Access denied", exact=True)
                ).to_be_visible()
                await page.goto(f"{base}/computers/{key}")
                await page.get_by_role("button", name="Sharing", exact=True).click()
                left = page.locator(".share-list").first
                right = page.locator(".share-list").last
                for enabled in (False, True):
                    if enabled:
                        await left.get_by_label("Review team", exact=True).check()
                        await page.get_by_role(
                            "button", name="Share with selected roles", exact=True
                        ).click()
                    else:
                        await (
                            right.locator(".share-role")
                            .filter(has_text="Review team")
                            .get_by_role("checkbox")
                            .check()
                        )
                        await page.get_by_role(
                            "button", name="Revoke selected shares", exact=True
                        ).click()
                    await page.get_by_role("button", name="Save", exact=True).click()
                    await expect(
                        page.locator('.role-sharing input[name="changes"]')
                    ).to_have_value('{"grants":{}}')
                    shares = await storage.list_role_shares("computer", computer.id)
                    grant = next(
                        share for share in shares if share.role_id == recipients.id
                    )
                    assert P.AGENTS_CREATE in grant.permissions
                    await page.reload()
                    panel = right if enabled else left
                    await expect(
                        panel.get_by_text("Review team", exact=True)
                    ).to_be_visible()
            finally:
                await browser.close()
