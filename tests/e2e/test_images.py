from __future__ import annotations

from pathlib import Path

import pytest
from server._serving import serving_app, signed_in

from bazaar_compute_server.images import Images

pytestmark = pytest.mark.e2e

PICTURE = "https://github.githubassets.com/favicons/favicon.png"
# answered with a move to the same picture over https
MOVED = "http://github.com/favicon.ico"
PAGE = "https://github.com/yuchanns/bazaar-compute-node"


@pytest.mark.asyncio
async def test_a_real_picture_comes_through_the_server(tmp_path: Path) -> None:
    """The page asks for a picture on the web by a signed address; the server
    fetches it and streams it on, follows a move, and passes on nothing that
    is not a picture."""

    async with (
        serving_app(tmp_path) as (base, app),
        signed_in(base, app.state.storage) as session,
    ):
        images: Images = app.state.images
        async with session.get(f"{base}{images.address(PICTURE)}") as r:
            assert r.status == 200
            assert r.headers["Content-Type"].startswith("image/png")
            assert r.headers["Content-Security-Policy"] == "sandbox"
            assert (await r.read()).startswith(b"\x89PNG")
        async with session.get(f"{base}{images.address(MOVED)}") as r:
            assert r.status == 200
            assert r.headers["Content-Type"].startswith("image/")
        async with session.get(f"{base}{images.address(PAGE)}") as r:
            assert r.status == 502
