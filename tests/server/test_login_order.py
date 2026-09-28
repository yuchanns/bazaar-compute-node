from __future__ import annotations

from pathlib import Path
from uuid import uuid7

import pytest

from bazaar_compute_server.clock import now_ms
from bazaar_compute_server.storage import OIDCProvider

from ._serving import TESTER, serving_app, with_password


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_login_order_and_switching_in_browser(tmp_path: Path) -> None:
    playwright = pytest.importorskip("playwright.async_api")
    expect = playwright.expect
    async with serving_app(tmp_path / "server") as (base, app):
        storage = app.state.storage
        await with_password(storage, *TESTER)
        for number in range(7):
            provider = OIDCProvider(
                str(uuid7()),
                f"Workspace {number + 1}",
                "",
                "https://example.com",
                "browser",
                "secret",
                base + "/callback",
                now_ms(),
                now_ms(),
            )
            await storage.save_oidc_provider(provider)
        async with playwright.async_playwright() as driver:
            browser = await driver.chromium.launch(
                executable_path="/usr/bin/microsoft-edge", headless=True
            )
            context = await browser.new_context(
                viewport={"width": 1280, "height": 900}, locale="en-US"
            )
            page = await context.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.goto(base + "/login")
            await page.locator("#name").fill(TESTER[0])
            await page.locator("#password").fill(TESTER[1])
            await page.locator("#login button").click()
            await page.wait_for_url("**/agents")
            await page.goto(base + "/settings/oidc")
            row = page.locator(".login-order-row").filter(has_text="Workspace 1")
            await row.get_by_role("button", name="Move up", exact=True).click()
            await expect(row).to_contain_text("Preferred")
            await page.reload()
            await expect(row).to_contain_text("Preferred")
            await page.screenshot(path=str(tmp_path / "order-desktop.png"))
            await page.set_viewport_size({"width": 390, "height": 844})
            await page.screenshot(path=str(tmp_path / "order-mobile.png"))
            assert await page.evaluate(
                "document.documentElement.scrollWidth <= innerWidth"
            )
            await context.clear_cookies()
            await page.goto(base + "/login")
            await expect(
                page.get_by_role("link", name="Workspace 1", exact=True)
            ).to_be_visible()
            await page.screenshot(path=str(tmp_path / "login-mobile.png"))
            boxes = await page.locator(".login-alternative:visible").evaluate_all(
                "items => items.map(item => item.getBoundingClientRect().top)"
            )
            assert max(boxes) > min(boxes)
            await page.get_by_role("button", name="Workspace 3", exact=True).click()
            await expect(
                page.get_by_role("link", name="Workspace 3", exact=True)
            ).to_be_visible()
            await page.get_by_role("button", name="Password", exact=True).click()
            await expect(page.locator("#name")).to_be_focused()
            await page.screenshot(path=str(tmp_path / "password-mobile.png"))
            await page.set_viewport_size({"width": 1280, "height": 900})
            await page.reload()
            await expect(
                page.get_by_role("link", name="Workspace 1", exact=True)
            ).to_be_visible()
            await page.screenshot(path=str(tmp_path / "login-desktop.png"))
            await page.get_by_role("button", name="Password", exact=True).click()
            await page.locator("#name").fill(TESTER[0])
            await page.locator("#password").fill(TESTER[1])
            await page.locator("#login button").click()
            await page.wait_for_url("**/agents")
            assert not errors, errors
            await browser.close()
