from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from bazaar_contrib_timer import TIMER, Timer
from bazaar_contrib_timer_wheel import timer_wheel
from bazaar_core import Composition, Context, Contrib, ContribEntry, ContribHub


@pytest.fixture
async def timer() -> AsyncIterator[Timer]:
    services: list[Timer] = []

    @asynccontextmanager
    async def consumer(ctx: Context):
        services.append(ctx.accept(TIMER))
        yield

    async with (
        ContribHub() as hub,
        hub.enable(
            Composition(
                entries=(
                    ContribEntry(name="wheel", definition=timer_wheel),
                    ContribEntry(
                        name="consumer",
                        definition=Contrib(accepts=[TIMER], lifespan=consumer),
                    ),
                ),
                roots=("consumer",),
            )
        ),
    ):
        yield services[0]


@pytest.fixture(autouse=True)
def database_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("BCN_HOME", str(tmp_path))
    return tmp_path / ".bcn" / "bcn.sqlite3"
