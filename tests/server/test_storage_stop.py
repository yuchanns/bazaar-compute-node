from __future__ import annotations

import threading
from pathlib import Path

import pytest

from bazaar_compute_server.contrib.sqlite.storage import SqliteStorage


@pytest.mark.asyncio
async def test_storage_closes_even_when_its_writing_was_cancelled(
    tmp_path: Path,
) -> None:
    """A forced quit cancels every task, the storage's writing among them;
    stopping still closes every connection, or their threads hold the
    process from exiting."""

    before = {thread.ident for thread in threading.enumerate()}
    storage = SqliteStorage(tmp_path / "bcs.sqlite3", retention_days=1)
    await storage.start()
    # the connections' own threads, not the loop's executor
    started = [
        t
        for t in threading.enumerate()
        if t.ident not in before and "_connection_worker" in t.name
    ]
    assert started
    writing = storage._writing
    assert writing is not None
    writing.cancel()

    await storage.stop()

    for thread in started:
        thread.join(timeout=5)
    assert not [thread for thread in started if thread.is_alive()]
