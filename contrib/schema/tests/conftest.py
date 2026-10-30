from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from bazaar_compute_node.boot import build, node
from bazaar_contrib_storage import DB, Database
from bazaar_core import Context, Contrib, ContribEntry


@pytest.fixture
def open_database():
    @asynccontextmanager
    async def open():
        services: list[Database] = []

        @asynccontextmanager
        async def consume(ctx: Context):
            services.append(ctx.accept(DB))
            yield

        def select(document):
            composition = build(document)
            entry = ContribEntry(
                name="database-consumer",
                definition=Contrib(accepts=[DB], lifespan=consume),
            )
            return composition.model_copy(
                update={
                    "entries": (*composition.entries, entry),
                    "roots": (entry.name,),
                }
            )

        async with node(start=select):
            yield services[0]

    return open


@pytest.fixture(autouse=True)
def database_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("BCN_HOME", str(tmp_path))
    return tmp_path / ".bcn" / "bcn.sqlite3"
