"""The ordered, immutable ledger of schema migrations."""

from __future__ import annotations

from time import monotonic_ns

import aiosqlite

from ....clock import now_ms
from .model import Migration
from .v01_initial_server_schema import SCHEMA_MIGRATION
from .v02_refs import REFS_MIGRATION


class MigrationError(RuntimeError):
    """The database disagrees with the migration ledger."""


class MigrationChecksumError(MigrationError):
    """A migration that was applied is not the one the code carries."""


def _migration_ledger(*migrations: Migration) -> tuple[Migration, ...]:
    versions = tuple(migration.version for migration in migrations)
    if versions != tuple(range(1, len(migrations) + 1)):
        raise RuntimeError("sqlite migrations must use consecutive ordered versions")
    return migrations


MIGRATIONS = _migration_ledger(SCHEMA_MIGRATION, REFS_MIGRATION)


async def apply_migrations(connection: aiosqlite.Connection) -> int:
    """Bring the database to the latest version, checking what is already there."""

    async with connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
    ) as cursor:
        ledger_exists = await cursor.fetchone() is not None
    applied: dict[int, aiosqlite.Row] = {}
    if ledger_exists:
        async with connection.execute(
            "SELECT version, migration_name, checksum FROM schema_migrations"
            " ORDER BY version"
        ) as cursor:
            applied = {int(row["version"]): row async for row in cursor}
        unknown = sorted(set(applied) - {item.version for item in MIGRATIONS})
        if unknown:
            raise MigrationError(
                "database contains unknown migration versions: "
                + ", ".join(str(version) for version in unknown)
            )

    latest = 0
    missing = False
    for migration in MIGRATIONS:
        row = applied.get(migration.version)
        if row is None:
            missing = True
            continue
        if missing:
            raise MigrationError(
                "migration ledger contains a later version after a missing "
                f"version before {migration.version}"
            )
        if (
            row["migration_name"] != migration.name
            or row["checksum"] != migration.checksum
        ):
            raise MigrationChecksumError(
                f"migration {migration.version} does not match its ledger entry"
            )
        latest = migration.version
    if ledger_exists and latest == 0:
        raise MigrationError(
            f"migration ledger is missing version {MIGRATIONS[0].version}"
        )

    for migration in MIGRATIONS:
        if migration.version <= latest:
            continue
        started_at_ns = monotonic_ns()
        # the statements and the ledger entry land together or not at all;
        # sqlite's DDL is transactional, so a start cut short leaves nothing
        await connection.execute("BEGIN")
        try:
            for statement in migration.statements:
                await connection.execute(statement)
            await connection.execute(
                "INSERT INTO schema_migrations"
                " (version, migration_name, checksum, applied_at_ms, duration_ms)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    migration.version,
                    migration.name,
                    migration.checksum,
                    now_ms(),
                    (monotonic_ns() - started_at_ns) // 1_000_000,
                ),
            )
        except Exception:
            await connection.rollback()
            raise
        await connection.commit()
        latest = migration.version
    return latest


__all__ = ["MIGRATIONS", "MigrationChecksumError", "MigrationError", "apply_migrations"]
