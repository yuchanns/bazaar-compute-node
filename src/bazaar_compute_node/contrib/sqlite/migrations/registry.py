from __future__ import annotations

from collections.abc import Callable
from time import monotonic_ns, time_ns
from typing import TYPE_CHECKING

from .model import Migration
from .v01_initial_node_schema import SCHEMA_MIGRATION
from .v02_session_mapping_indexes import SESSION_MAPPING_INDEX_MIGRATION
from .v03_message_log_indexes import MESSAGE_LOG_INDEX_MIGRATION
from .v04_runtime_attempt_facts import RUNTIME_ATTEMPT_FACT_MIGRATION
from .v05_inbound_message_references import INBOUND_MESSAGE_REFERENCE_MIGRATION
from .v06_inbound_message_reference_integrity import (
    INBOUND_MESSAGE_REFERENCE_INTEGRITY_MIGRATION,
)
from .v07_inbound_provider_identity import INBOUND_PROVIDER_IDENTITY_MIGRATION
from .v08_transient_stream_events import TRANSIENT_STREAM_EVENT_MIGRATION
from .v09_remove_runtime_events import RUNTIME_EVENTS_REMOVAL_MIGRATION
from .v10_add_outbound_attachments import OUTBOUND_ATTACHMENTS_MIGRATION
from .v11_remove_runtime_session_mapping import (
    RUNTIME_SESSION_MAPPING_REMOVAL_MIGRATION,
)
from .v12_add_reminders import REMINDER_MIGRATION
from .v13_add_agent_ownership import AGENT_OWNERSHIP_MIGRATION
from .v14_add_inbox_discovery_indexes import INBOX_DISCOVERY_MIGRATION
from .v15_add_handoffs import HANDOFF_MIGRATION
from .v16_remove_outbound_drafts import OUTBOUND_DRAFT_REMOVAL_MIGRATION
from .v17_remove_agent_identity_triggers import STORAGE_ACCESS_MIGRATION
from .v18_unify_messages import MESSAGE_UNIFICATION_MIGRATION
from .v19_reminder_system_messages import REMINDER_SYSTEM_MESSAGE_MIGRATION

if TYPE_CHECKING:
    from ..executor import SqliteSession


def _current_time_ms() -> int:
    return time_ns() // 1_000_000


class MigrationError(RuntimeError):
    """The database cannot be safely brought to the application schema."""


class MigrationChecksumError(MigrationError):
    """A migration ledger entry no longer matches the application migration."""


def _migration_ledger(*migrations: Migration) -> tuple[Migration, ...]:
    versions = tuple(migration.version for migration in migrations)
    if versions != tuple(range(1, len(migrations) + 1)):
        raise RuntimeError("SQLite migrations must use consecutive ordered versions")
    return migrations


MIGRATIONS = _migration_ledger(
    SCHEMA_MIGRATION,
    SESSION_MAPPING_INDEX_MIGRATION,
    MESSAGE_LOG_INDEX_MIGRATION,
    RUNTIME_ATTEMPT_FACT_MIGRATION,
    INBOUND_MESSAGE_REFERENCE_MIGRATION,
    INBOUND_MESSAGE_REFERENCE_INTEGRITY_MIGRATION,
    INBOUND_PROVIDER_IDENTITY_MIGRATION,
    TRANSIENT_STREAM_EVENT_MIGRATION,
    RUNTIME_EVENTS_REMOVAL_MIGRATION,
    OUTBOUND_ATTACHMENTS_MIGRATION,
    RUNTIME_SESSION_MAPPING_REMOVAL_MIGRATION,
    REMINDER_MIGRATION,
    AGENT_OWNERSHIP_MIGRATION,
    INBOX_DISCOVERY_MIGRATION,
    HANDOFF_MIGRATION,
    OUTBOUND_DRAFT_REMOVAL_MIGRATION,
    STORAGE_ACCESS_MIGRATION,
    MESSAGE_UNIFICATION_MIGRATION,
    REMINDER_SYSTEM_MESSAGE_MIGRATION,
)


async def apply_migrations(
    transaction: SqliteSession,
    *,
    clock: Callable[[], int] = _current_time_ms,
) -> int:
    """Apply the ordered migration ledger inside the caller's transaction."""

    ledger_exists = (
        await transaction.fetchone(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'table' AND name = 'schema_migrations'"
        )
        is not None
    )
    applied_rows = []
    if ledger_exists:
        applied_rows = await transaction.fetchall(
            "SELECT version, migration_name, checksum "
            "FROM schema_migrations ORDER BY version"
        )
        known_versions = {migration.version for migration in MIGRATIONS}
        unknown_versions = {
            int(row["version"])
            for row in applied_rows
            if int(row["version"]) not in known_versions
        }
        if unknown_versions:
            raise MigrationError(
                "database contains unknown migration versions: "
                + ", ".join(str(version) for version in sorted(unknown_versions))
            )

    applied_by_version = {int(row["version"]): row for row in applied_rows}
    preexisting_ledger = ledger_exists
    latest_version = 0
    missing_version = False
    for migration in MIGRATIONS:
        row = applied_by_version.get(migration.version)
        if row is None:
            missing_version = True
            continue
        if missing_version:
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
        latest_version = migration.version

    if preexisting_ledger and latest_version == 0:
        raise MigrationError(
            f"migration ledger is missing version {MIGRATIONS[0].version}"
        )

    for migration in MIGRATIONS:
        if migration.version <= latest_version:
            continue
        started_at_ns = monotonic_ns()
        for statement in migration.statements:
            await transaction.execute(statement)
        await transaction.execute(
            "INSERT INTO schema_migrations "
            "(version, migration_name, checksum, applied_at_ms, duration_ms) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                migration.version,
                migration.name,
                migration.checksum,
                clock(),
                (monotonic_ns() - started_at_ns) // 1_000_000,
            ),
        )
        latest_version = migration.version

    return latest_version


__all__ = [
    "MIGRATIONS",
    "MigrationChecksumError",
    "MigrationError",
    "apply_migrations",
]
