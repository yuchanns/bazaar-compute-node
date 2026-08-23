from __future__ import annotations

from collections.abc import Callable
from time import monotonic_ns, time_ns
from typing import TYPE_CHECKING

from .agent_migration import AGENT_OWNERSHIP_MIGRATION
from .handoff_migration import HANDOFF_MIGRATION
from .inbox_migration import INBOX_DISCOVERY_MIGRATION
from .migration import Migration
from .reminder_migration import REMINDER_MIGRATION

if TYPE_CHECKING:
    from .repository import SqliteTransaction


def _current_time_ms() -> int:
    return time_ns() // 1_000_000


class MigrationError(RuntimeError):
    """The database cannot be safely brought to the application schema."""


class MigrationChecksumError(MigrationError):
    """A migration ledger entry no longer matches the application migration."""


SCHEMA_MIGRATION = Migration(
    version=1,
    name="initial_node_schema",
    statements=(
        """
        -- Immutable migration ledger and checksum verification record.
        CREATE TABLE schema_migrations (
            -- Monotonic migration version used for application-level ledger checks.
            version INTEGER PRIMARY KEY,
            -- Human-readable migration name.
            migration_name TEXT,
            -- Migration content checksum.
            checksum TEXT,
            -- Migration application time.
            applied_at_ms INTEGER,
            -- Migration execution duration.
            duration_ms INTEGER
        )
        """,
        """
        -- Singleton node identity, workspace binding, and cached schema version.
        CREATE TABLE node_state (
            -- Fixed application-managed row key for the singleton state record.
            singleton_key INTEGER PRIMARY KEY,
            -- Stable identifier of this node installation.
            node_id TEXT,
            -- Cached version of the migration ledger.
            schema_version INTEGER,
            -- UUIDv7-backed identifier of the shared workspace used by all runtime sessions.
            workspace_id TEXT,
            -- Creation time of the node state.
            created_at_ms INTEGER,
            -- Last update time of node metadata or schema cache.
            updated_at_ms INTEGER,
            -- Non-sensitive node metadata encoded as JSON.
            metadata_json TEXT
        )
        """,
        """
        -- Provider thread identity and channel-level following state.
        CREATE TABLE channel_sessions (
            -- Stable local identifier for the normalized channel session.
            id TEXT PRIMARY KEY,
            -- Selected channel adapter name.
            channel TEXT,
            -- Provider-native routable thread identity used for lookup.
            provider_thread_id TEXT,
            -- Normalized target category used by the command layer.
            target_kind TEXT,
            -- Application-managed following flag.
            following INTEGER,
            -- Non-sensitive provider identity references encoded as JSON.
            provider_identity_ref_json TEXT,
            -- Creation time of the channel session.
            created_at_ms INTEGER,
            -- Last update time of channel identity or lifecycle state.
            updated_at_ms INTEGER,
            -- Last normalized inbound time observed for this session.
            last_inbound_at_ms INTEGER,
            -- Last outbound attempt time observed for this session.
            last_outbound_at_ms INTEGER
        )
        """,
        """
        -- Stable bcn session bound to one channel session and the shared workspace.
        CREATE TABLE bcn_sessions (
            -- Stable local identifier exposed to the runtime command wrapper.
            id TEXT PRIMARY KEY,
            -- Application-managed association to a channel session.
            channel_session_id TEXT,
            -- UUIDv7-backed identifier of the shared workspace used by this session.
            workspace_id TEXT,
            -- Creation time of the bcn session.
            created_at_ms INTEGER,
            -- Last update time of durable session metadata.
            updated_at_ms INTEGER,
            -- Last message or runtime activity time.
            last_activity_at_ms INTEGER,
            -- Non-sensitive session metadata encoded as JSON.
            metadata_json TEXT
        )
        """,
        """
        -- One durable agent runtime/thread binding.
        CREATE TABLE runtime_sessions (
            -- Stable local identifier for one runtime binding.
            id TEXT PRIMARY KEY,
            -- Application-managed association to a bcn session.
            bcn_session_id TEXT,
            -- Application-managed channel session association for correlation.
            channel_session_id TEXT,
            -- Selected agent runtime adapter name.
            runtime TEXT,
            -- Runtime adapter or protocol version used for this process.
            runtime_version TEXT,
            -- Provider-native runtime thread identifier when available.
            provider_thread_id TEXT,
            -- Creation time of the runtime session record.
            created_at_ms INTEGER,
            -- Last update time of durable runtime metadata.
            updated_at_ms INTEGER,
            -- Non-sensitive runtime metadata encoded as JSON.
            metadata_json TEXT
        )
        """,
        """
        -- Durable runtime turn state used for completion, interruption, and reconciliation.
        CREATE TABLE runtime_turns (
            -- Stable local identifier for one runtime turn.
            turn_id TEXT PRIMARY KEY,
            -- Application-managed association to the runtime session.
            session_id TEXT,
            -- Provider-native turn identifier when available.
            provider_turn_id TEXT,
            -- Client message identifier that caused this turn.
            client_user_message_id TEXT,
            -- Application-managed turn lifecycle state.
            state TEXT,
            -- Turn start time.
            started_at_ms INTEGER,
            -- Turn completion time when a terminal result is known.
            completed_at_ms INTEGER,
            -- Latest normalized runtime event name.
            last_event_name TEXT,
            -- Stable application error category for the turn.
            error_kind TEXT,
            -- Redacted summary of the turn failure.
            error_message TEXT,
            -- Non-sensitive turn metadata encoded as JSON.
            metadata_json TEXT
        )
        """,
        """
        -- Append-only normalized inbound message log with the node-local delivery sequence.
        CREATE TABLE inbound_messages (
            -- Stable local UUIDv7 message identifier used as the physical row identity.
            message_id TEXT PRIMARY KEY,
            -- Node-local monotonic sequence used for cursor and snapshot boundaries.
            seq INTEGER,
            -- Application-managed association to a bcn session.
            session_id TEXT,
            -- Application-managed association to a channel session.
            channel_session_id TEXT,
            -- Channel adapter name that normalized the message.
            channel TEXT,
            -- Provider-native routable thread identity mapped to the channel session.
            provider_thread_id TEXT,
            -- Provider-native message identifier used for application-level deduplication.
            provider_message_id TEXT,
            -- Provider timestamp, if supplied.
            provider_time_ms INTEGER,
            -- Local receipt time.
            received_at_ms INTEGER,
            -- Provider-neutral sender identity shown to the runtime.
            sender TEXT,
            -- Normalized sender or event type.
            message_type TEXT,
            -- Canonical target used by reply commands.
            canonical_target TEXT,
            -- Provider-neutral direct-message or group classification.
            target_kind TEXT,
            -- Provider-native identifier of the message being replied to.
            reply_to_provider_message_id TEXT,
            -- Normalized message body.
            body TEXT,
            -- Whether the provider reports an explicit mention of the agent.
            mentions_agent INTEGER,
            -- Persisted application decision to expose this message as unread.
            notifies_runtime INTEGER,
            -- Controlled reference to retained provider payload data.
            provider_payload_ref TEXT,
            -- Non-sensitive normalized metadata encoded as JSON.
            metadata_json TEXT
        )
        """,
        """
        -- Terminal local descriptors for provider-neutral inbound attachments.
        CREATE TABLE inbound_attachments (
            attachment_id TEXT PRIMARY KEY,
            message_id TEXT,
            ordinal INTEGER,
            name TEXT,
            kind TEXT,
            state TEXT,
            media_type TEXT,
            relative_path TEXT,
            size_bytes INTEGER,
            error TEXT
        )
        """,
        """
        -- Outbound command attempts, fresh-check evidence, provider receipt, and delivery state.
        CREATE TABLE outbound_messages (
            -- Stable local UUIDv7 identifier for one outbound command attempt.
            outbound_message_id TEXT PRIMARY KEY,
            -- Stable identifier of the originating command invocation.
            command_id TEXT,
            -- Application-managed association to a bcn session.
            session_id TEXT,
            -- Application-managed association to a channel session.
            channel_session_id TEXT,
            -- Canonical target supplied to the send command.
            target TEXT,
            -- Local inbound message identity for an optional reply intent.
            reply_to_message_id TEXT,
            -- Outbound message body captured for retry and audit.
            body TEXT,
            -- Application-managed delivery lifecycle state.
            state TEXT,
            -- Application-managed fresh-check result.
            fresh_check_state TEXT,
            -- Inbound snapshot boundary used by the command.
            snapshot_seq INTEGER,
            -- Current inbound boundary observed during fresh-check.
            current_inbound_seq INTEGER,
            -- Provider-native message identifier after provider acceptance.
            provider_message_id TEXT,
            -- Controlled reference to the provider delivery receipt.
            provider_receipt_ref TEXT,
            -- Creation time of the outbound attempt.
            created_at_ms INTEGER,
            -- Time at which the provider call was attempted.
            provider_attempted_at_ms INTEGER,
            -- Completion time of the provider call.
            completed_at_ms INTEGER,
            -- Time at which a refused draft was persisted.
            draft_saved_at_ms INTEGER,
            -- Stable application error category for the attempt.
            error_kind TEXT,
            -- Redacted summary of the outbound failure.
            error_message TEXT,
            -- Human- and machine-actionable next step.
            next_action TEXT,
            -- Non-sensitive outbound metadata encoded as JSON.
            metadata_json TEXT
        )
        """,
        """
        -- Per-session delivery cursor and the latest inbox snapshot used by fresh-check.
        CREATE TABLE consumer_cursors (
            -- Stable bcn session identifier used as the cursor record identity.
            session_id TEXT PRIMARY KEY,
            -- Highest inbound sequence already delivered by check.
            delivered_through_seq INTEGER,
            -- Latest inbound sequence observed by check or read.
            inbox_snapshot_seq INTEGER,
            -- Operation that produced the latest snapshot.
            inbox_snapshot_source TEXT,
            -- Time at which the latest snapshot was recorded.
            inbox_snapshot_at_ms INTEGER,
            -- Last check operation time.
            last_check_at_ms INTEGER,
            -- Last read operation time.
            last_read_at_ms INTEGER,
            -- Last cursor or snapshot update time.
            updated_at_ms INTEGER
        )
        """,
        """
        -- Append-only operational and audit events with cross-component correlation fields.
        CREATE TABLE runtime_events (
            -- Node-local monotonic sequence for the event log.
            event_seq INTEGER PRIMARY KEY,
            -- Stable event identifier for external correlation.
            event_id TEXT,
            -- Event creation time.
            created_at_ms INTEGER,
            -- Normalized log severity.
            level TEXT,
            -- Stable event name.
            event_name TEXT,
            -- Application-managed event state.
            state TEXT,
            -- Event duration when the operation has completed.
            duration_ms INTEGER,
            -- Node identifier that emitted the event.
            node_id TEXT,
            -- Channel adapter name associated with the event.
            channel TEXT,
            -- Runtime adapter name associated with the event.
            runtime TEXT,
            -- Channel session correlation identifier.
            channel_session_id TEXT,
            -- Bcn session correlation identifier.
            bcn_session_id TEXT,
            -- Agent runtime session correlation identifier.
            runtime_session_id TEXT,
            -- Runtime turn correlation identifier.
            turn_id TEXT,
            -- Provider or protocol request correlation identifier.
            request_id TEXT,
            -- Local command correlation identifier.
            command_id TEXT,
            -- Related inbound message sequence when available.
            inbound_seq INTEGER,
            -- Related outbound message identifier when available.
            outbound_message_id TEXT,
            -- Stable application error category.
            error_kind TEXT,
            -- Runtime error type after redaction.
            error_type TEXT,
            -- Redacted error summary.
            error_message TEXT,
            -- Controlled reference to a retained traceback.
            traceback_ref TEXT,
            -- Non-sensitive event metadata encoded as JSON.
            metadata_json TEXT
        )
        """,
        """
        CREATE INDEX idx_inbound_session_seq
            ON inbound_messages (session_id, seq)
        """,
        """
        CREATE INDEX idx_inbound_seq
            ON inbound_messages (seq)
        """,
        """
        CREATE INDEX idx_inbound_channel_received
            ON inbound_messages (channel_session_id, received_at_ms)
        """,
        """
        CREATE INDEX idx_outbound_session_created
            ON outbound_messages (session_id, created_at_ms)
        """,
        """
        CREATE INDEX idx_outbound_state_created
            ON outbound_messages (state, created_at_ms)
        """,
        """
        CREATE INDEX idx_runtime_turns_session_state
            ON runtime_turns (session_id, state, started_at_ms)
        """,
        """
        CREATE INDEX idx_runtime_events_session_seq
            ON runtime_events (bcn_session_id, event_seq)
        """,
        """
        CREATE INDEX idx_runtime_events_name_seq
            ON runtime_events (event_name, event_seq)
        """,
        """
        CREATE INDEX idx_runtime_events_created
            ON runtime_events (created_at_ms)
        """,
    ),
)

SESSION_MAPPING_INDEX_MIGRATION = Migration(
    version=2,
    name="session_mapping_indexes",
    statements=(
        """
        -- Provider identity lookup used by channel session get-or-create.
        CREATE INDEX idx_channel_sessions_provider_identity
            ON channel_sessions (
                channel,
                provider_thread_id
            )
        """,
        """
        -- Channel-to-bcn session lookup used during recovery reconciliation.
        CREATE INDEX idx_bcn_sessions_channel
            ON bcn_sessions (channel_session_id)
        """,
        """
        -- Bcn-to-runtime session lookup used during process reconciliation.
        CREATE INDEX idx_runtime_sessions_bcn
            ON runtime_sessions (bcn_session_id)
        """,
    ),
)

MESSAGE_LOG_INDEX_MIGRATION = Migration(
    version=3,
    name="message_log_indexes",
    statements=(
        """
        -- Provider-scoped inbound deduplication lookup.
        CREATE INDEX idx_inbound_provider_identity
            ON inbound_messages (channel, provider_message_id)
        """,
        """
        -- Target-filtered history lookup for one bcn session.
        CREATE INDEX idx_inbound_session_target_seq
            ON inbound_messages (session_id, canonical_target, seq)
        """,
    ),
)

RUNTIME_ATTEMPT_FACT_MIGRATION = Migration(
    version=4,
    name="runtime_attempt_facts",
    statements=(
        """
        CREATE TABLE runtime_attempts (
            turn_id TEXT PRIMARY KEY,
            session_id TEXT,
            client_user_message_id TEXT,
            started_at_ms INTEGER
        )
        """,
        """
        INSERT INTO runtime_attempts (
            turn_id,
            session_id,
            client_user_message_id,
            started_at_ms
        )
        SELECT
            turn_id,
            session_id,
            client_user_message_id,
            started_at_ms
        FROM runtime_turns
        WHERE client_user_message_id IS NOT NULL
        """,
        """
        DROP INDEX idx_runtime_turns_session_state
        """,
        """
        DROP TABLE runtime_turns
        """,
        """
        CREATE INDEX idx_runtime_attempts_session_started
            ON runtime_attempts (session_id, started_at_ms)
        """,
    ),
)

INBOUND_MESSAGE_REFERENCE_MIGRATION = Migration(
    version=5,
    name="inbound_message_references",
    statements=(
        """
        ALTER TABLE inbound_messages
            RENAME COLUMN reply_to_provider_message_id TO reply_to_message_id
        """,
        """
        UPDATE inbound_messages AS current
        SET reply_to_message_id = (
            SELECT referenced.message_id
            FROM inbound_messages AS referenced
            WHERE referenced.channel = current.channel
              AND referenced.provider_message_id = current.reply_to_message_id
            ORDER BY referenced.seq
            LIMIT 1
        )
        WHERE current.reply_to_message_id IS NOT NULL
        """,
        """
        CREATE INDEX idx_inbound_reply_to_message
            ON inbound_messages (reply_to_message_id)
        """,
    ),
)

INBOUND_MESSAGE_REFERENCE_INTEGRITY_MIGRATION = Migration(
    version=6,
    name="inbound_message_reference_integrity",
    statements=(
        """
        UPDATE inbound_messages AS current
        SET reply_to_message_id = NULL
        WHERE current.reply_to_message_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1
              FROM inbound_messages AS referenced
              WHERE referenced.message_id = current.reply_to_message_id
                AND referenced.session_id = current.session_id
                AND referenced.seq < current.seq
          )
        """,
    ),
)

INBOUND_PROVIDER_IDENTITY_MIGRATION = Migration(
    version=7,
    name="inbound_provider_identity",
    statements=(
        """
        DROP INDEX idx_inbound_provider_identity
        """,
        """
        CREATE UNIQUE INDEX idx_inbound_provider_identity
            ON inbound_messages (
                channel,
                provider_thread_id,
                provider_message_id
            )
        """,
    ),
)

TRANSIENT_STREAM_EVENT_MIGRATION = Migration(
    version=8,
    name="transient_stream_events",
    statements=(
        """
        DELETE FROM runtime_events
        WHERE event_name = 'codex.turn.progress'
          AND (
              json_extract(metadata_json, '$.provider_method') = 'turn/progress'
              OR (
                  json_extract(metadata_json, '$.provider_method') LIKE 'item/%'
                  AND json_extract(metadata_json, '$.provider_method') NOT IN (
                      'item/started',
                      'item/completed',
                      'item/autoApprovalReview/started',
                      'item/autoApprovalReview/completed'
                  )
              )
          )
        """,
    ),
)

RUNTIME_EVENTS_REMOVAL_MIGRATION = Migration(
    version=9,
    name="remove_runtime_events",
    statements=(
        """
        ALTER TABLE schema_migrations
        ADD COLUMN compaction_completed_at_ms INTEGER
        """,
        """
        DROP TABLE runtime_events
        """,
    ),
)

OUTBOUND_ATTACHMENTS_MIGRATION = Migration(
    version=10,
    name="add_outbound_attachments",
    statements=(
        """
        ALTER TABLE outbound_messages
        ADD COLUMN attachments_json TEXT NOT NULL DEFAULT '[]'
        """,
    ),
)

RUNTIME_SESSION_MAPPING_REMOVAL_MIGRATION = Migration(
    version=11,
    name="remove_runtime_session_mapping",
    statements=(
        """
        DROP INDEX idx_runtime_sessions_bcn
        """,
        """
        DROP TABLE runtime_sessions
        """,
    ),
)

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
)


async def apply_migrations(
    transaction: SqliteTransaction,
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
