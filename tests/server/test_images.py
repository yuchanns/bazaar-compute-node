from __future__ import annotations

from pathlib import Path

import pytest

from bazaar_compute_server.images import Images

from ._serving import free_port, serving_app, signed_in


@pytest.mark.asyncio
async def test_only_a_signed_public_address_is_fetched(tmp_path: Path) -> None:
    """An address the server did not sign, or one on the server's own
    network, is turned away before anything is fetched; what is not on the
    web is not addressed at all."""

    async with (
        serving_app(tmp_path) as (base, app),
        signed_in(base, app.state.storage) as session,
    ):
        images: Images = app.state.images
        address = images.address("https://pictures.test/p.png")
        assert address is not None
        forged = address.replace("p.png", "q.png")
        async with session.get(f"{base}{forged}") as r:
            assert r.status == 403
        private = images.address(f"http://127.0.0.1:{free_port()}/p.png")
        async with session.get(f"{base}{private}") as r:
            assert r.status == 502
        assert images.address("data:image/png;base64,AAAA") is None
