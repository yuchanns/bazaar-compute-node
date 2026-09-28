from __future__ import annotations

from pathlib import Path

import pytest

from bazaar_compute_server.clock import now_ms
from bazaar_compute_server.permissions import Permission
from bazaar_compute_server.sessions import COOKIE, Sessions, load_session_key
from bazaar_compute_server.storage import OIDCProvider

from ._serving import TESTER, serving_app, with_password


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_roles_and_account_assignments_on_desktop_and_mobile(
    tmp_path: Path,
) -> None:
    playwright = pytest.importorskip("playwright.async_api")
    expect = playwright.expect

    async with serving_app(tmp_path / "server") as (base, app):
        storage = app.state.storage
        await with_password(storage, *TESTER)
        async with playwright.async_playwright() as driver:
            browser = await driver.chromium.launch(
                executable_path="/usr/bin/microsoft-edge", headless=True
            )
            context = await browser.new_context(
                viewport={"width": 1360, "height": 1000}, locale="en-US"
            )
            page = await context.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.goto(base + "/login")
            await page.locator("#name").fill(TESTER[0])
            await page.locator("#password").fill(TESTER[1])
            await page.locator("#login button").click()
            await page.wait_for_url("**/agents")
            await page.goto(base + "/settings/roles")
            await page.get_by_role("link", name="New role", exact=True).first.click()
            await expect(page.locator("#roles-form-new")).to_be_visible()
            await page.get_by_label("Role name", exact=True).fill("Readers")
            await page.get_by_label("View agents", exact=True).check()
            async with page.expect_response(
                lambda response: (
                    response.request.method == "POST" and "/settings/" in response.url
                )
            ) as saved:
                await page.get_by_role("button", name="Save", exact=True).click()
            assert (await saved.value).ok
            await expect(page.locator("#main")).not_to_have_attribute(
                "aria-busy", "true"
            )
            await page.wait_for_url("**/settings/roles/*")
            await expect(page.locator("#role-name")).to_have_value("Readers")
            await page.get_by_role(
                "button", name="Set as default role", exact=True
            ).click()
            await expect(
                page.get_by_text("New accounts receive this role.", exact=False)
            ).to_be_visible()
            reader = await storage.default_role()
            assert reader is not None and Permission.AGENTS_VIEW in reader.permissions

            provider = OIDCProvider(
                "work",
                "Company login",
                "",
                "https://identity.example",
                "client",
                "secret",
                "https://bcs.example/callback",
                now_ms(),
                now_ms(),
            )
            await storage.save_oidc_provider(provider)
            colleague = await storage.oidc_account(
                provider.id,
                provider.issuer,
                "colleague",
                "Test colleague",
                "colleague@example.com",
            )
            await page.get_by_role("link", name="New role", exact=True).click()
            await expect(page.locator("#roles-form-new")).to_be_visible()
            await page.get_by_label("Role name", exact=True).fill("Reviewers")
            await page.get_by_label("Approve contacts", exact=True).check()
            await page.get_by_label("Manage accounts", exact=True).check()
            async with page.expect_response(
                lambda response: (
                    response.request.method == "POST" and "/settings/" in response.url
                )
            ) as saved:
                await page.get_by_role("button", name="Save", exact=True).click()
            assert (await saved.value).ok
            await expect(page.locator("#main")).not_to_have_attribute(
                "aria-busy", "true"
            )
            await expect(page.locator("#role-name")).to_have_value("Reviewers")
            reviewer = next(
                role for role in await storage.list_roles() if role.name == "Reviewers"
            )

            await page.goto(base + "/settings/accounts")
            await page.get_by_role(
                "link", name="Test colleague Company login", exact=False
            ).click()
            await page.locator(".role-picker-toggle").click()
            await page.screenshot(
                path=str(
                    tmp_path
                    / (
                        "account-roles-open-mobile.png"
                        if page.viewport_size["width"] == 390
                        else "account-roles-open-desktop.png"
                    )
                )
            )
            await page.get_by_label("Search roles", exact=True).fill("Review")
            await page.get_by_label("Reviewers", exact=True).check()
            await page.locator(".role-picker-toggle").click()
            async with page.expect_response(
                lambda response: (
                    response.request.method == "POST" and "/settings/" in response.url
                )
            ) as saved:
                await page.get_by_role("button", name="Save", exact=True).click()
            assert (await saved.value).ok
            await expect(page.locator("#main")).not_to_have_attribute(
                "aria-busy", "true"
            )
            await expect(page.get_by_label("Reviewers", exact=True)).to_be_checked()
            assert {role.id for role in await storage.list_roles(colleague.id)} >= {
                reader.id,
                reviewer.id,
            }

            sessions = Sessions()
            sessions.key = await load_session_key(tmp_path / "server")
            member = await browser.new_context(locale="en-US")
            await member.add_cookies(
                [{"name": COOKIE, "value": sessions.issue(colleague), "url": base}]
            )
            member_page = await member.new_page()
            await member_page.goto(base + "/settings")
            await expect(
                member_page.get_by_role("link", name="Manage accounts", exact=False)
            ).to_be_visible()

            assert reviewer.id != reader.id

            # Editing role permissions preserves account assignments.
            await page.goto(base + "/settings/roles")
            await page.get_by_role("link", name="Reviewers", exact=True).click()
            await page.get_by_label("View computers", exact=True).check()
            async with page.expect_response(
                lambda response: (
                    response.request.method == "POST" and "/settings/" in response.url
                )
            ) as saved:
                await page.get_by_role("button", name="Save", exact=True).click()
            assert (await saved.value).ok
            await expect(page.locator("#main")).not_to_have_attribute(
                "aria-busy", "true"
            )
            assert {role.id for role in await storage.list_roles(colleague.id)} >= {
                reader.id,
                reviewer.id,
            }
            await page.screenshot(path=str(tmp_path / "roles-desktop.png"))

            await page.set_viewport_size({"width": 390, "height": 844})
            await page.goto(base + "/settings")
            await page.get_by_role("link", name="Manage roles", exact=False).click()
            await expect(page.locator('[data-pane="entries"]')).to_be_visible()
            await page.get_by_role(
                "link", name="Readers Default role", exact=False
            ).click()
            await expect(page.locator("#role-name")).to_be_visible()
            await page.get_by_label("View computers", exact=True).check()
            async with page.expect_response(
                lambda response: (
                    response.request.method == "POST" and "/settings/" in response.url
                )
            ) as saved:
                await page.get_by_role("button", name="Save", exact=True).click()
            assert (await saved.value).ok
            await expect(page.locator("#main")).not_to_have_attribute(
                "aria-busy", "true"
            )
            await expect(
                page.get_by_label("View computers", exact=True)
            ).to_be_checked()
            await member_page.reload()
            await expect(
                member_page.locator("#rail").get_by_role(
                    "link", name="Computers", exact=True
                )
            ).to_be_visible()
            await page.screenshot(path=str(tmp_path / "roles-mobile.png"))
            await (
                page.locator('[data-pane="entry"]')
                .get_by_role("link", name="Back", exact=True)
                .click()
            )
            await expect(page.locator('[data-pane="entries"]')).to_be_visible()
            assert await page.evaluate(
                "document.documentElement.scrollWidth <= innerWidth"
            )
            await page.get_by_role(
                "link", name="Readers Default role", exact=False
            ).click()
            await (
                page.locator('[data-pane="entry"]')
                .get_by_role("link", name="Back", exact=True)
                .click()
            )
            await page.get_by_role("link", name="Reviewers", exact=True).click()
            await page.get_by_role(
                "button", name="Set as default role", exact=True
            ).click()
            await (
                page.locator('[data-pane="entry"]')
                .get_by_role("link", name="Back", exact=True)
                .click()
            )
            await page.get_by_role("link", name="Readers", exact=True).click()
            page.once("dialog", lambda dialog: dialog.accept())
            await page.get_by_role("button", name="Delete role", exact=True).click()
            await expect(
                page.get_by_role("link", name="Reviewers Default role", exact=False)
            ).to_be_visible()
            assert (await storage.default_role()).id == reviewer.id
            await page.get_by_role("link", name="New role", exact=True).click()
            await expect(page.locator("#roles-form-new")).to_be_visible()
            await page.get_by_label("Role name", exact=True).fill("Mobile operators")
            await page.get_by_label("View agents", exact=True).check()
            async with page.expect_response(
                lambda response: (
                    response.request.method == "POST" and "/settings/" in response.url
                )
            ) as saved:
                await page.get_by_role("button", name="Save", exact=True).click()
            assert (await saved.value).ok
            await page.get_by_role(
                "button", name="Set as default role", exact=True
            ).click()
            await expect(
                page.get_by_text("New accounts receive this role.", exact=False)
            ).to_be_visible()
            mobile = await storage.default_role()
            assert mobile.name == "Mobile operators"
            await page.goto(base + "/settings/accounts")
            await page.get_by_role(
                "link", name="Test colleague Company login", exact=False
            ).click()
            await page.locator(".role-picker-toggle").click()
            await page.screenshot(
                path=str(
                    tmp_path
                    / (
                        "account-roles-open-mobile.png"
                        if page.viewport_size["width"] == 390
                        else "account-roles-open-desktop.png"
                    )
                )
            )
            await page.get_by_label("Search roles", exact=True).fill("Review")
            await page.get_by_label("Reviewers", exact=True).check()
            await page.get_by_label("Search roles", exact=True).fill("Mobile")
            await page.get_by_label("Mobile operators", exact=True).check()
            await page.locator(".role-picker-toggle").click()
            async with page.expect_response(
                lambda response: (
                    response.request.method == "POST" and "/settings/" in response.url
                )
            ) as saved:
                await page.get_by_role("button", name="Save", exact=True).click()
            assert (await saved.value).ok
            assert {role.id for role in await storage.list_roles(colleague.id)} >= {
                reviewer.id,
                mobile.id,
            }
            await expect(page.locator("#main")).not_to_have_attribute(
                "aria-busy", "true"
            )
            await page.screenshot(path=str(tmp_path / "accounts-mobile.png"))
            assert not errors, errors
            await browser.close()
