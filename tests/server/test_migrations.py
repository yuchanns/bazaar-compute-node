from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from bazaar_compute_server.contrib.sqlite.migrations.registry import (
    MIGRATIONS,
    MigrationChecksumError,
    apply_migrations,
)
from bazaar_compute_server.contrib.sqlite.storage import SqliteStorage


@pytest.mark.asyncio
async def test_the_ledger_is_applied_once_and_guards_what_it_applied(
    tmp_path: Path,
) -> None:
    path = tmp_path / "bcs.sqlite3"
    storage = SqliteStorage(path, retention_days=30)
    await storage.start()
    await storage.stop()

    async with aiosqlite.connect(path) as connection:
        connection.row_factory = aiosqlite.Row
        async with connection.execute(
            "SELECT version, migration_name, checksum FROM schema_migrations"
        ) as cursor:
            rows = [tuple(row) async for row in cursor]
        # case: every migration in the ledger is recorded with its checksum
        assert rows == [(item.version, item.name, item.checksum) for item in MIGRATIONS]

        # case: a second start finds nothing to do
        assert await apply_migrations(connection) == MIGRATIONS[-1].version

        # case: a ledger entry that no longer matches the code is refused
        await connection.execute(
            "UPDATE schema_migrations SET checksum = 'tampered' WHERE version = 1"
        )
        await connection.commit()
        with pytest.raises(MigrationChecksumError):
            await apply_migrations(connection)
