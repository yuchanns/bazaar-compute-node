from __future__ import annotations

import logging
import re
from pathlib import Path
from stat import S_IMODE

import aiohttp
import pytest
from yarl import URL

from bazaar_compute_server.gate import MAX_BODY_BYTES
from bazaar_compute_server.sessions import COOKIE, load_session_key

from ._serving import TESTER, serving, signed_in, with_password


def _jar() -> aiohttp.CookieJar:
    return aiohttp.CookieJar(unsafe=True)


@pytest.mark.asyncio
async def test_nothing_but_the_door_is_open_without_a_session(tmp_path: Path) -> None:
    async with (
        serving(tmp_path) as (base, _),
        aiohttp.ClientSession(cookie_jar=_jar()) as session,
    ):
        # case: a page asks the browser to go to the login page
        async with session.get(f"{base}/agents", allow_redirects=False) as response:
            assert response.status == 303
            assert response.headers["Location"] == "/login"

        # case: htmx is told the same in the way it understands
        async with session.get(
            f"{base}/computers", headers={"HX-Request": "true"}
        ) as response:
            assert response.status == 401
            assert response.headers["HX-Redirect"] == "/login"

        # case: the login page and its assets open, and the node endpoint
        # answers for itself with a token error, not a redirect
        async with session.get(f"{base}/login") as response:
            assert (
                response.status == 200 and 'hx-post="/login"' in await response.text()
            )
        async with session.get(f"{base}/static/app.css") as response:
            assert response.status == 200
        async with session.post(f"{base}/node/reportEvents") as response:
            assert response.status != 303 and "Location" not in response.headers

        # case: a forged cookie is a missing cookie
        session.cookie_jar.update_cookies(
            {COOKIE: "someone:0:abcdefgh:not-a-signature"}
        )
        async with session.get(f"{base}/agents", allow_redirects=False) as response:
            assert response.status == 303

        # case: a login form bigger than any request may be is refused unread
        async with session.post(
            f"{base}/login",
            data={"name": "admin", "password": "x" * (MAX_BODY_BYTES + 1)},
        ) as response:
            assert response.status == 413


@pytest.mark.asyncio
async def test_a_session_key_that_is_not_one_stops_the_start(tmp_path: Path) -> None:
    (tmp_path / "session.key").write_bytes(b"half")
    with pytest.raises(RuntimeError, match="session key"):
        await load_session_key(tmp_path)


@pytest.mark.asyncio
async def test_logging_in_and_out(tmp_path: Path) -> None:
    name, password = TESTER
    async with serving(tmp_path) as (base, storage):
        await with_password(storage, name, password)
        async with aiohttp.ClientSession(cookie_jar=_jar()) as session:
            # case: a wrong password comes back as the form with the reason
            async with session.post(
                f"{base}/login", data={"name": name, "password": "nope"}
            ) as response:
                assert response.status == 401
                body = await response.text()
                assert "<form" in body and "Incorrect" in body
            assert not session.cookie_jar.filter_cookies(URL(base))

            # case: the right one sets the cookie and sends htmx home
            async with session.post(
                f"{base}/login", data={"name": name, "password": password}
            ) as response:
                assert response.status == 204
                assert response.headers["HX-Redirect"] == "/agents"
                set_cookie = response.headers["Set-Cookie"].lower()
            assert set_cookie.startswith(f"{COOKIE}=")
            assert "httponly" in set_cookie and "samesite=lax" in set_cookie
            # case: over plain http the cookie is plain; behind https it is secure
            assert "secure" not in set_cookie
            async with (
                aiohttp.ClientSession(cookie_jar=_jar()) as other,
                other.post(
                    f"{base}/login",
                    data={"name": name, "password": password},
                    headers={"X-Forwarded-Proto": "https"},
                ) as response,
            ):
                assert "secure" in response.headers["Set-Cookie"].lower()
            async with session.get(f"{base}/agents") as response:
                assert response.status == 200

            # case: logging out clears it
            async with session.post(f"{base}/logout") as response:
                assert response.headers["HX-Redirect"] == "/login"
            async with session.get(f"{base}/agents", allow_redirects=False) as response:
                assert response.status == 303


@pytest.mark.asyncio
async def test_changing_the_password_ends_every_other_session(tmp_path: Path) -> None:
    name, password = TESTER
    async with (
        serving(tmp_path) as (base, storage),
        signed_in(base, storage) as first,
        aiohttp.ClientSession(cookie_jar=_jar()) as second,
    ):
        async with second.post(
            f"{base}/login", data={"name": name, "password": password}
        ) as response:
            assert response.status == 204

        # case: a password shorter than eight characters is not one
        async with first.post(
            f"{base}/settings/password", data={"current": password, "new": "short"}
        ) as response:
            assert response.status == 422 and "8" in await response.text()

        # case: the current password has to be right
        async with first.post(
            f"{base}/settings/password", data={"current": "nope", "new": "fresh one"}
        ) as response:
            assert response.status == 401 and "Incorrect" in await response.text()

        # case: changed; this session goes on, the other is shown the door
        async with first.post(
            f"{base}/settings/password",
            data={"current": password, "new": "fresh one"},
        ) as response:
            assert response.status == 200 and "changed" in await response.text()
        async with first.get(f"{base}/settings") as response:
            assert response.status == 200
        async with second.get(f"{base}/agents", allow_redirects=False) as response:
            assert response.status == 303

        # case: only the new password logs in now
        for attempt, expected in ((password, 401), ("fresh one", 204)):
            async with second.post(
                f"{base}/login", data={"name": name, "password": attempt}
            ) as response:
                assert response.status == expected


@pytest.mark.asyncio
async def test_the_first_start_makes_root_and_says_the_password_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="bazaar_compute_server")
    async with serving(tmp_path) as (base, storage):
        admin = await storage.find_account("admin")
        assert admin is not None
        said = [
            record.getMessage()
            for record in caplog.records
            if "admin" in record.getMessage()
        ]
        assert len(said) == 1
        password = re.search(r"password (\S+);", said[0])
        assert password is not None

        # case: the password that was printed is the one that works
        async with (
            aiohttp.ClientSession(cookie_jar=_jar()) as session,
            session.post(
                f"{base}/login", data={"name": "admin", "password": password.group(1)}
            ) as response,
        ):
            assert response.status == 204

        # case: the signing key is the owner's alone
        assert S_IMODE((tmp_path / "session.key").stat().st_mode) == 0o600

    # case: a second start keeps the account and says nothing more
    caplog.clear()
    async with serving(tmp_path) as (_, storage):
        again = await storage.find_account("admin")
        assert again is not None and again.password_hash == admin.password_hash
        assert not [r for r in caplog.records if "password" in r.getMessage()]


@pytest.mark.asyncio
async def test_a_person_chooses_their_language_and_theme(tmp_path: Path) -> None:
    async with (
        serving(tmp_path) as (base, storage),
        signed_in(base, storage) as session,
    ):
        # case: with nothing chosen the browser's language decides, and the
        # page carries no theme of its own
        async with session.get(
            f"{base}/settings", headers={"Accept-Language": "zh-CN"}
        ) as response:
            page = await response.text()
            assert "外观" in page and "data-theme" not in page

        # case: a choice is kept on the account and the page reloads whole
        async with session.post(
            f"{base}/settings/preferences", data={"language": "en", "theme": "dark"}
        ) as response:
            assert response.status == 204 and response.headers["HX-Refresh"] == "true"
        async with session.get(
            f"{base}/settings", headers={"Accept-Language": "zh-CN"}
        ) as response:
            page = await response.text()
            assert "Appearance" in page and 'data-theme="dark"' in page
        # case: the security page holds the password form, and the rail
        # rides along out of band on htmx requests with its highlight moved
        async with session.get(
            f"{base}/settings/security", headers={"HX-Request": "true"}
        ) as response:
            page = await response.text()
            assert 'hx-post="/settings/password"' in page
            assert 'hx-swap-oob="outerMorph"' in page and 'href="/settings" ' in page
        async with session.get(f"{base}/settings/nowhere") as response:
            assert response.status == 404

        # case: an unknown choice is refused
        async with session.post(
            f"{base}/settings/preferences", data={"language": "tlh", "theme": ""}
        ) as response:
            assert response.status == 422


@pytest.mark.asyncio
async def test_errors_hang_the_closed_board(tmp_path: Path) -> None:
    async with (
        serving(tmp_path) as (base, storage),
        signed_in(base, storage) as session,
    ):
        # case: a missing page is the board, whole or as a fragment
        async with session.get(f"{base}/nowhere") as response:
            page = await response.text()
            assert response.status == 404 and ">404<" in page and "<html" in page
        async with session.get(
            f"{base}/nowhere", headers={"HX-Request": "true"}
        ) as response:
            assert response.status == 404 and "<html" not in await response.text()

        # case: the node endpoints keep their envelope
        async with session.get(f"{base}/node/nowhere") as response:
            body = await response.json()
            assert response.status == 404 and body["ok"] is False


@pytest.mark.asyncio
async def test_the_page_carries_the_board_for_when_the_server_is_gone(
    tmp_path: Path,
) -> None:
    async with (
        serving(tmp_path) as (base, storage),
        signed_in(base, storage) as session,
    ):
        async with session.get(f"{base}/agents") as response:
            page = await response.text()
        # case: the shell holds the unreachable board and the listener that
        # hangs it when a request cannot be sent at all
        assert '<template id="unreachable">' in page
        assert "htmx:error" in page and "location.reload()" in page
