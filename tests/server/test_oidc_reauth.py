"""Automatic reauthentication against real Keycloak SSO in a temporary container."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import httpx2
import pytest

from bazaar_compute_server.clock import now_ms
from bazaar_compute_server.config import ServerConfiguration
from bazaar_compute_server.permissions import Permission
from bazaar_compute_server.storage import OIDCProvider, Role

from ._serving import free_port, serving_app


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_expired_oidc_session_resumes_once_with_existing_sso(
    tmp_path: Path,
) -> None:
    playwright = pytest.importorskip("playwright.async_api")
    if shutil.which("docker") is None:
        pytest.skip("Docker is required for real Keycloak SSO")
    expect = playwright.expect
    port, identity_port = free_port(), free_port()
    base = f"http://127.0.0.1:{port}"
    issuer = f"http://127.0.0.1:{identity_port}/realms/bcs-test"
    provider_id = str(uuid4())
    callback = f"{base}/login/oidc/{provider_id}/callback"
    realm = tmp_path / "realm.json"
    realm.write_text(
        json.dumps(
            {
                "realm": "bcs-test",
                "enabled": True,
                "sslRequired": "none",
                "clients": [
                    {
                        "clientId": "bcs-test",
                        "secret": "isolated-secret",
                        "protocol": "openid-connect",
                        "publicClient": False,
                        "standardFlowEnabled": True,
                        "redirectUris": [callback],
                    }
                ],
                "users": [
                    {
                        "username": "colleague",
                        "enabled": True,
                        "email": "colleague@example.com",
                        "firstName": "Test",
                        "lastName": "Colleague",
                        "emailVerified": True,
                        "credentials": [
                            {
                                "type": "password",
                                "value": "test-password",
                                "temporary": False,
                            }
                        ],
                    }
                ],
            }
        )
    )
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
                "bcs-reauth-" + uuid4().hex,
                "-p",
                f"127.0.0.1:{identity_port}:8080",
                "-v",
                f"{realm}:/opt/keycloak/data/import/realm.json:ro",
                "quay.io/keycloak/keycloak:26.0.8",
                "start-dev",
                "--import-realm",
                "--hostname",
                f"http://127.0.0.1:{identity_port}",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    ).stdout.strip()
    try:
        async with httpx2.AsyncClient() as client:
            async with asyncio.timeout(90):
                while True:
                    try:
                        if (
                            await client.get(
                                issuer + "/.well-known/openid-configuration"
                            )
                        ).is_success:
                            break
                    except httpx2.TransportError:
                        pass
                    await asyncio.sleep(0.2)
        async with playwright.async_playwright() as driver:
            browser = await driver.chromium.launch(
                executable_path="/usr/bin/microsoft-edge", headless=True
            )
            context = await browser.new_context(locale="en-US")
            page = await context.new_page()
            directory = tmp_path / "server"
            configuration = ServerConfiguration(
                listen=f"127.0.0.1:{port}", session_minutes=10
            )
            async with serving_app(directory, port, configuration=configuration) as (
                _,
                app,
            ):
                storage = app.state.storage
                role = Role(
                    str(uuid4()),
                    "Readers",
                    frozenset({Permission.AGENTS_VIEW, Permission.COMPUTERS_VIEW}),
                    now_ms(),
                    now_ms(),
                )
                await storage.save_role(role)
                await storage.set_default_role(role.id)
                await storage.save_oidc_provider(
                    OIDCProvider(
                        provider_id,
                        "Company SSO",
                        "",
                        issuer,
                        "bcs-test",
                        "isolated-secret",
                        callback,
                        now_ms(),
                        now_ms(),
                        session_minutes=1,
                    )
                )
                await storage.save_login_order([provider_id, "password"])
                await page.goto(base + "/login")
                await page.get_by_role("link", name="Company SSO", exact=True).click()
                await page.locator("#username").fill("colleague")
                await page.locator("#password").fill("test-password")
                await page.locator("#kc-login").click()
                await page.wait_for_url(base + "/agents")
                first = next(
                    cookie
                    for cookie in await context.cookies()
                    if cookie["name"] == "bcs_session"
                )
                # Let the configured one-minute lifetime actually elapse; both
                # browser expiry and server expiry use the real clock.
                await page.goto(base + "/login")
                await asyncio.sleep(61)
            # New application instance, same persistent data, same browser.
            async with serving_app(directory, port, configuration=configuration) as (
                _,
                app,
            ):
                # Replaying the expired signed cookie also expires server-side,
                # even though the server-wide setting is ten minutes.
                expired = await context.request.get(
                    base + "/settings/appearance?view=personal",
                    headers={"Cookie": f"bcs_session={first['value']}"},
                    max_redirects=0,
                )
                assert 300 <= expired.status < 400
                async with page.expect_request(
                    lambda request: "prompt=none" in request.url
                ):
                    await page.goto(base + "/settings/appearance?view=personal")
                await page.wait_for_url(base + "/settings/appearance?view=personal")
                await expect(page.locator("#theme")).to_be_visible()
                second = next(
                    cookie
                    for cookie in await context.cookies()
                    if cookie["name"] == "bcs_session"
                )
                assert second["value"] != first["value"]
                await page.screenshot(path=str(tmp_path / "resumed.png"))

                # The application's real background poll must restore this page,
                # including its query and fragment, after the access cookie expires.
                await page.goto(base + "/computers?view=personal#computer-list")
                async with page.expect_request(
                    lambda request: "prompt=none" in request.url
                ):
                    await context.clear_cookies(name="bcs_session")
                await page.wait_for_url(base + "/computers?view=personal#computer-list")
                await expect(page.locator("#computer-list")).to_be_visible()

                # Preserve the recovery hint but remove IdP SSO and BCS access
                # cookies: the real IdP responds login_required to prompt=none.
                cookies = await context.cookies()
                await context.clear_cookies()
                await context.add_cookies(
                    [cookie for cookie in cookies if cookie["name"] == "bcs_reauth"]
                )
                await page.goto(base + "/agents")
                await expect(page.locator(".login-provider")).to_be_visible()
                await page.goto(base + "/agents")
                await page.wait_for_url(base + "/login")
                await expect(
                    page.get_by_role("link", name="Company SSO", exact=True)
                ).to_be_visible()
                await page.get_by_role("link", name="Company SSO", exact=True).click()
                await page.locator("#username").fill("colleague")
                await page.locator("#password").fill("test-password")
                await page.locator("#kc-login").click()
                await page.wait_for_url(base + "/agents")
                await context.request.post(base + "/logout")
                await page.goto(base + "/agents")
                await page.wait_for_url(base + "/login")
                await expect(page.locator(".login-provider")).to_be_visible()
                accounts = [
                    account
                    for account in await app.state.storage.list_accounts()
                    if account.auth_type == "oidc"
                ]
                assert len(accounts) == 1
            await browser.close()
    finally:
        with (tmp_path / "keycloak.log").open("w") as log:
            await asyncio.to_thread(
                subprocess.run,
                ["docker", "logs", container],
                stdout=log,
                stderr=log,
                check=False,
            )
        await asyncio.to_thread(
            subprocess.run,
            ["docker", "rm", "-f", container],
            capture_output=True,
            check=False,
        )
