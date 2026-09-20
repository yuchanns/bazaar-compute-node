from __future__ import annotations

from pathlib import Path

import pytest

from bazaar_compute_server.contrib.sqlite.storage import SqliteStorage
from bazaar_compute_server.refs import Refs


@pytest.mark.asyncio
async def test_a_value_keeps_its_number(tmp_path: Path) -> None:
    """A value gets one short number, the same every time it is asked for
    and after the server has been restarted; a batch keeps its order."""

    path = tmp_path / "bcs.sqlite3"
    storage = SqliteStorage(path, retention_days=30)
    await storage.start()
    try:
        first = await storage.shorten(["computer-a", "agent-b"])
        # case: known and new values in one batch, in the order given
        again = await storage.shorten(["agent-b", "dm:c", "computer-a", "agent-b"])
        assert again == [first[1], again[1], first[0], first[1]]
        assert len({*first, again[1]}) == 3
        # case: a number that was never given stands for nothing
        assert await storage.expand([again[1], 10**6, first[0]]) == [
            "dm:c",
            None,
            "computer-a",
        ]
    finally:
        await storage.stop()

    # case: the numbers survive a restart
    storage = SqliteStorage(path, retention_days=30)
    await storage.start()
    try:
        assert await storage.shorten(["dm:c", "computer-a"]) == [again[1], first[0]]
        # case: the process keeps what it read, and asks only for the rest
        refs = Refs(storage)
        await refs.load(["computer-a", "thread-d"])
        assert refs.ref("computer-a") == first[0]
        assert await refs.expand([refs.ref("thread-d"), 10**6]) == ["thread-d", None]
    finally:
        await storage.stop()
