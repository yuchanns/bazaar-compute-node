"""Browser sign-in against a real Dex container and an isolated BCS process.

Requires Docker, ghcr.io/dexidp/dex:v2.44.0 and Edge. Run with
uv run --with playwright pytest tests/server/test_oidc_login.py -m e2e.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import httpx2
import pytest

from bazaar_compute_server.clock import now_ms
from bazaar_compute_server.contrib.sqlite.storage import SqliteStorage
from bazaar_compute_server.permissions import Permission
from bazaar_compute_server.sessions import COOKIE, Sessions, load_session_key
from bazaar_compute_server.storage import Role

from ._serving import TESTER, free_port, with_password


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_oidc_sign_in_and_provider_management(tmp_path: Path) -> None:
    playwright = pytest.importorskip("playwright.async_api")
    if shutil.which("docker") is None:
        pytest.skip("Docker is required for the real Dex service")
    expect = playwright.expect
    port, identity_port = free_port(), free_port()
    base, issuer = f"http://127.0.0.1:{port}", f"http://127.0.0.1:{identity_port}/dex"
    directory = tmp_path / "server"
    directory.mkdir()
    storage = SqliteStorage(directory / "bcs.sqlite3", retention_days=30)
    await storage.start()
    await with_password(storage, *TESTER)
    reader = Role(
        str(uuid4()), "Readers", frozenset({Permission.AGENTS_VIEW}), now_ms(), now_ms()
    )
    reviewer = Role(
        str(uuid4()),
        "Reviewers",
        frozenset({Permission.AGENTS_VIEW, Permission.AGENTS_APPROVE}),
        now_ms(),
        now_ms(),
    )
    computers = Role(
        str(uuid4()),
        "Computer viewers",
        frozenset({Permission.COMPUTERS_VIEW}),
        now_ms(),
        now_ms(),
    )
    settings = Role(
        str(uuid4()),
        "Account managers",
        frozenset({Permission.SETTINGS_ACCOUNTS}),
        now_ms(),
        now_ms(),
    )
    await storage.save_role(computers)
    await storage.save_role(settings)
    await storage.save_role(reader)
    await storage.save_role(reviewer)
    await storage.set_default_role(reader.id)
    name = "bcs-oidc-test-" + uuid4().hex
    container = None
    with (tmp_path / "server.log").open("w") as log:
        server = await asyncio.to_thread(
            subprocess.Popen,
            [
                sys.executable,
                "-c",
                "import sys,uvicorn; from pathlib import Path; from bazaar_compute_server.app import create_app; from bazaar_compute_server.config import ServerConfiguration; uvicorn.run(create_app(ServerConfiguration(listen=sys.argv[1]),Path(sys.argv[2])),host='127.0.0.1',port=int(sys.argv[3]),log_level='warning',access_log=False)",
                f"127.0.0.1:{port}",
                str(directory),
                str(port),
            ],
            stdout=log,
            stderr=log,
        )
        try:
            async with httpx2.AsyncClient() as client:
                async with asyncio.timeout(20):
                    while True:
                        try:
                            if (await client.get(base + "/login")).is_success:
                                break
                        except httpx2.ConnectError:
                            pass
                        await asyncio.sleep(0.1)
            async with playwright.async_playwright() as driver:
                browser = await driver.chromium.launch(
                    executable_path="/usr/bin/microsoft-edge", headless=True
                )
                admin = await browser.new_context(
                    locale="en-US", viewport={"width": 1360, "height": 1000}
                )
                page = await admin.new_page()
                await page.goto(base + "/login")
                await page.locator("#name").fill(TESTER[0])
                await page.locator("#password").fill(TESTER[1])
                await page.locator("#login button").click()
                await page.wait_for_url("**/agents")
                await page.goto(base + "/settings/oidc")
                await page.get_by_role(
                    "link", name="New sign-in method", exact=True
                ).click()
                await expect(page.locator("#oidc-form-new")).to_be_visible()
                callback = await page.get_by_label(
                    "Callback URL", exact=True
                ).input_value()
                # Exercise the editable callback, including the query the IdP
                # requires us to repeat exactly during the code exchange.
                callback += "?deployment=browser"
                await page.get_by_label("Callback URL", exact=True).fill(callback)
                configuration = {
                    "issuer": issuer,
                    "storage": {"type": "memory"},
                    "web": {"http": f"127.0.0.1:{identity_port}"},
                    "oauth2": {"skipApprovalScreen": True},
                    "staticClients": [
                        {
                            "id": "bcs-browser",
                            "secret": "isolated-browser-secret",
                            "name": "BCS",
                            "redirectURIs": [callback],
                        }
                    ],
                    "enablePasswordDB": True,
                    "staticPasswords": [
                        {
                            "email": f"person{i}@example.com",
                            "hash": "$2a$10$2b2cU8CPhOTaGrs1HRQuAueS7JTT5ZHsHSzYiFPm1leZck7Mc8T4W",
                            "username": "Same name",
                            "userID": str(uuid4()),
                        }
                        for i in range(1, 7)
                    ],
                }
                config = tmp_path / "dex.json"
                config.write_text(json.dumps(configuration))
                container = (
                    await asyncio.to_thread(
                        subprocess.run,
                        [
                            "docker",
                            "run",
                            "-d",
                            "--user",
                            str(os.getuid()),
                            "--name",
                            name,
                            "--network",
                            "host",
                            "-v",
                            f"{config}:/etc/dex/config.json:ro",
                            "ghcr.io/dexidp/dex:v2.44.0",
                            "dex",
                            "serve",
                            "/etc/dex/config.json",
                        ],
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                ).stdout.strip()
                async with httpx2.AsyncClient() as client:
                    async with asyncio.timeout(30):
                        while True:
                            try:
                                if (
                                    await client.get(
                                        issuer + "/.well-known/openid-configuration"
                                    )
                                ).is_success:
                                    break
                            except httpx2.ConnectError:
                                pass
                            await asyncio.sleep(0.1)
                await page.get_by_label("Display name", exact=True).fill(
                    "Company sign-in"
                )
                await page.get_by_label("Description (optional)", exact=True).fill(
                    "Use your workspace account"
                )
                async with page.expect_file_chooser() as chooser:
                    await page.get_by_role(
                        "button", name="Choose image", exact=True
                    ).click()
                await (await chooser.value).set_files(
                    Path(
                        "src/bazaar_compute_server/resources/static/apple-touch-icon.png"
                    )
                )
                await expect(page.locator(".provider-logo img")).to_be_visible()
                await page.get_by_label("Issuer URL", exact=True).fill(issuer)
                await page.get_by_label("Client ID", exact=True).fill("bcs-browser")
                await page.get_by_label("Client secret", exact=True).fill(
                    "isolated-browser-secret"
                )
                await page.get_by_label("Session duration (minutes)").fill("25")
                await page.get_by_role("button", name="Save", exact=True).click()
                await expect(
                    page.get_by_text("Secret is set. Leave blank to keep it.")
                ).to_be_visible()
                await expect(
                    page.get_by_label("Callback URL", exact=True)
                ).to_have_value(callback)
                await page.screenshot(path=str(tmp_path / "oidc-desktop.png"))
                await page.reload()
                await expect(
                    page.get_by_label("Client secret", exact=True)
                ).to_be_empty()
                await expect(
                    page.get_by_label("Description (optional)", exact=True)
                ).to_have_value("Use your workspace account")
                await expect(
                    page.get_by_label("Session duration (minutes)")
                ).to_have_value("25")
                await page.set_viewport_size({"width": 390, "height": 844})
                await page.get_by_label("Display name", exact=True).fill("Work sign-in")
                await page.get_by_role("button", name="Save", exact=True).click()
                await expect(page.locator("#main")).not_to_have_attribute(
                    "aria-busy", "true"
                )
                await expect(
                    page.get_by_label("Display name", exact=True)
                ).to_have_value("Work sign-in")
                await page.screenshot(path=str(tmp_path / "oidc-mobile.png"))
                assert await page.evaluate(
                    "document.documentElement.scrollWidth <= innerWidth"
                )

                # Fresh browser contexts force independent authentication at Dex.
                sessions = Sessions()
                sessions.key = await load_session_key(directory)
                identities: dict[int, str] = {}
                for person in (1, 1, 2, 3, 4, 1, 5, 6):
                    if person in (2, 3):
                        await page.get_by_label(
                            "Default role", exact=True
                        ).select_option(reviewer.id if person == 2 else "")
                        await page.get_by_role(
                            "button", name="Save", exact=True
                        ).click()
                        await expect(page.locator("#main")).not_to_have_attribute(
                            "aria-busy", "true"
                        )
                        await page.reload()
                        await expect(
                            page.get_by_label("Default role", exact=True)
                        ).to_have_value(reviewer.id if person == 2 else "")
                        await page.screenshot(
                            path=str(tmp_path / f"provider-role-{person}.png")
                        )
                    if person == 4:
                        await storage.set_default_role(reviewer.id)
                    if person in (5, 6):
                        await storage.set_default_role(
                            computers.id if person == 5 else settings.id
                        )
                    context = await browser.new_context(locale="en-US")
                    login = await context.new_page()
                    await login.goto(base + "/login")
                    await login.get_by_role(
                        "button", name="Work sign-in", exact=True
                    ).click()
                    await expect(login.locator(".login-provider img")).to_be_visible()
                    await expect(login.locator(".login-identity")).to_contain_text(
                        "Use your workspace account"
                    )
                    await expect(
                        login.locator(".login-provider img")
                    ).to_have_js_property("complete", True)
                    assert await login.locator(".login-provider img").evaluate(
                        "image => image.naturalWidth > 0"
                    )
                    await login.get_by_role(
                        "link", name="Work sign-in", exact=True
                    ).click()
                    await login.locator('input[name="login"]').fill(
                        f"person{person}@example.com"
                    )
                    await login.locator('input[name="password"]').fill("password")
                    await login.get_by_role("button", name="Login", exact=True).click()
                    destination = (
                        "/computers"
                        if person == 5
                        else "/settings"
                        if person == 6
                        else "/agents"
                    )
                    label = (
                        "Computers"
                        if person == 5
                        else "Settings"
                        if person == 6
                        else "Agents"
                    )
                    await login.wait_for_url(base + "/agents")
                    await expect(
                        login.locator("#rail").get_by_role(
                            "link", name=label, exact=True
                        )
                    ).to_be_visible()
                    await login.get_by_role("link", name="Bazaar", exact=True).click()
                    await login.wait_for_url(base + "/agents")
                    cookies = await context.cookies(base)
                    cookie = next(
                        cookie for cookie in cookies if cookie["name"] == COOKIE
                    )
                    assert 24 * 60 < cookie["expires"] - now_ms() / 1000 <= 25 * 60
                    claim = sessions.read(
                        next(
                            cookie["value"]
                            for cookie in cookies
                            if cookie["name"] == COOKIE
                        )
                    )
                    assert claim is not None
                    if person in identities:
                        assert claim.account_id == identities[person], (
                            "Returning identity retains its account"
                        )
                    else:
                        assert claim.account_id not in identities.values(), (
                            "Different identities have independent accounts"
                        )
                        identities[person] = claim.account_id
                    role = (
                        computers
                        if person == 5
                        else settings
                        if person == 6
                        else reviewer
                        if person in (2, 4)
                        else reader
                    )
                    assert role in await storage.list_roles(claim.account_id)
                    if person in (5, 6):
                        await login.goto(base + "/agents")
                        await expect(
                            login.get_by_role(
                                "heading", name="Access denied", exact=True
                            )
                        ).to_be_visible()
                        await (
                            login.locator("#rail")
                            .get_by_role("link", name=label, exact=True)
                            .click()
                        )
                        await login.wait_for_url(base + destination)
                    await context.close()
                await page.get_by_role("button", name="Remove logo", exact=True).click()
                await page.get_by_role("button", name="Save", exact=True).click()
                await expect(page.locator("#main")).not_to_have_attribute(
                    "aria-busy", "true"
                )
                assert not (await storage.list_oidc_providers())[0].logo_url
                await page.get_by_label("Logo", exact=True).set_input_files(
                    Path("src/bazaar_compute_server/resources/static/icon-192.png")
                )
                await page.get_by_role("button", name="Save", exact=True).click()
                await expect(page.locator("#main")).not_to_have_attribute(
                    "aria-busy", "true"
                )
                await page.reload()
                await expect(page.locator(".provider-logo img")).to_have_js_property(
                    "naturalWidth", 192
                )
                await page.get_by_label("Description (optional)", exact=True).fill("")
                await page.get_by_role("button", name="Save", exact=True).click()
                await expect(page.locator("#main")).not_to_have_attribute(
                    "aria-busy", "true"
                )
                await page.reload()
                await expect(
                    page.get_by_label("Description (optional)", exact=True)
                ).to_be_empty()
                await page.goto(base + "/login")
                await page.get_by_role(
                    "button", name="Work sign-in", exact=True
                ).click()
                await expect(page.locator(".login-provider")).to_be_visible()
                await expect(page.locator(".login-identity small")).to_have_count(0)
                await browser.close()
        finally:
            if container:
                with (tmp_path / "dex.log").open("w") as dex_log:
                    await asyncio.to_thread(
                        subprocess.run,
                        ["docker", "logs", container],
                        stdout=dex_log,
                        stderr=dex_log,
                        check=False,
                    )
                await asyncio.to_thread(
                    subprocess.run,
                    ["docker", "rm", "-f", container],
                    check=False,
                    capture_output=True,
                )
            server.terminate()
            await asyncio.to_thread(server.wait, 15)
            await storage.stop()
