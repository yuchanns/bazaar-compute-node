from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...]

    @property
    def checksum(self) -> str:
        content = "\n".join(self.statements).encode("utf-8")
        return sha256(content).hexdigest()


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
        -- Provider conversation/thread identity and channel-level following state.
        CREATE TABLE channel_sessions (
            -- Stable local identifier for the normalized channel session.
            channel_session_id TEXT PRIMARY KEY,
            -- Selected channel adapter slug.
            channel_slug TEXT,
            -- Provider-native conversation identity used for lookup.
            provider_conversation_key TEXT,
            -- Provider-native thread or reply identity when one exists.
            provider_thread_key TEXT,
            -- Normalized target category used by the command layer.
            target_kind TEXT,
            -- Application-managed following flag.
            following INTEGER,
            -- Application-managed channel session lifecycle state.
            state TEXT,
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
            bcn_session_id TEXT PRIMARY KEY,
            -- Application-managed association to a channel session.
            channel_session_id TEXT,
            -- UUIDv7-backed identifier of the shared workspace used by this session.
            workspace_id TEXT,
            -- Application-managed bcn session lifecycle state.
            state TEXT,
            -- Creation time of the bcn session.
            created_at_ms INTEGER,
            -- Last update time of session state or metadata.
            updated_at_ms INTEGER,
            -- Last message or runtime activity time.
            last_activity_at_ms INTEGER,
            -- Time at which the session reached its stopped state.
            stopped_at_ms INTEGER,
            -- Non-sensitive session metadata encoded as JSON.
            metadata_json TEXT
        )
        """,
        """
        -- One agent runtime process/thread binding and process recovery state.
        CREATE TABLE runtime_sessions (
            -- Stable local identifier for one runtime process lifecycle.
            agent_runtime_session_id TEXT PRIMARY KEY,
            -- Application-managed association to a bcn session.
            bcn_session_id TEXT,
            -- Application-managed channel session association for correlation.
            channel_session_id TEXT,
            -- Selected agent runtime adapter slug.
            runtime_slug TEXT,
            -- Runtime adapter or protocol version used for this process.
            runtime_version TEXT,
            -- Provider-native runtime thread identifier when available.
            provider_thread_id TEXT,
            -- Application-managed process lifecycle state.
            process_state TEXT,
            -- Operating-system process identifier when the process is running.
            process_pid INTEGER,
            -- Last known process exit code.
            last_exit_code INTEGER,
            -- Creation time of the runtime session record.
            created_at_ms INTEGER,
            -- Last update time of runtime process state or metadata.
            updated_at_ms INTEGER,
            -- Process start time.
            started_at_ms INTEGER,
            -- Process stop time.
            stopped_at_ms INTEGER,
            -- Last time persisted state was reconciled with the process.
            last_reconciled_at_ms INTEGER,
            -- Stable application error category from the latest failure.
            last_error_kind TEXT,
            -- Redacted summary of the latest runtime failure.
            last_error_message TEXT,
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
            agent_runtime_session_id TEXT,
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
            bcn_session_id TEXT,
            -- Application-managed association to a channel session.
            channel_session_id TEXT,
            -- Channel adapter slug that normalized the message.
            channel_slug TEXT,
            -- Provider-native message identifier used for application-level deduplication.
            provider_message_id TEXT,
            -- Provider timestamp, if supplied.
            provider_time_ms INTEGER,
            -- Local receipt time.
            received_at_ms INTEGER,
            -- Stable provider sender identifier.
            sender_id TEXT,
            -- Display name captured at receipt time.
            sender_display_name TEXT,
            -- Normalized sender or event type.
            message_type TEXT,
            -- Canonical target used by reply commands.
            canonical_target TEXT,
            -- Provider-native thread identifier when available.
            provider_thread_id TEXT,
            -- Provider-native identifier of the message being replied to.
            reply_to_provider_message_id TEXT,
            -- Normalized message body.
            body TEXT,
            -- Controlled reference to retained provider payload data.
            provider_payload_ref TEXT,
            -- Non-sensitive normalized metadata encoded as JSON.
            metadata_json TEXT
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
            bcn_session_id TEXT,
            -- Application-managed association to a channel session.
            channel_session_id TEXT,
            -- Canonical target supplied to the send command.
            target TEXT,
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
            bcn_session_id TEXT PRIMARY KEY,
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
            -- Channel adapter slug associated with the event.
            channel_slug TEXT,
            -- Runtime adapter slug associated with the event.
            runtime_slug TEXT,
            -- Channel session correlation identifier.
            channel_session_id TEXT,
            -- Bcn session correlation identifier.
            bcn_session_id TEXT,
            -- Agent runtime session correlation identifier.
            agent_runtime_session_id TEXT,
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
            ON inbound_messages (bcn_session_id, seq)
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
            ON outbound_messages (bcn_session_id, created_at_ms)
        """,
        """
        CREATE INDEX idx_outbound_state_created
            ON outbound_messages (state, created_at_ms)
        """,
        """
        CREATE INDEX idx_runtime_sessions_state
            ON runtime_sessions (process_state, updated_at_ms)
        """,
        """
        CREATE INDEX idx_runtime_turns_session_state
            ON runtime_turns (agent_runtime_session_id, state, started_at_ms)
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
                channel_slug,
                provider_conversation_key,
                provider_thread_key
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

MIGRATIONS: tuple[Migration, ...] = (
    SCHEMA_MIGRATION,
    SESSION_MAPPING_INDEX_MIGRATION,
)
