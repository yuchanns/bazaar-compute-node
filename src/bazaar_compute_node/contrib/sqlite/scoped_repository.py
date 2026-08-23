from __future__ import annotations

import asyncio
from dataclasses import replace
from types import TracebackType
from typing import Self, cast
from uuid import NAMESPACE_URL, uuid5, uuid7

import aiosqlite

from ...core.inbox import InboxTargetPage
from ...core.models import (
    BcnSession,
    ChannelSession,
    ChannelTargetKind,
    ConsumerCursor,
    InboundAttachment,
    InboundMessage,
    InboxTargetSummary,
    OutboundMessage,
    Reminder,
    ReminderOccurrence,
    ReminderState,
    RuntimeAttempt,
    SenderIdentity,
)
from ...core.reminder import canonical_id_reference
from ...core.storage import InboxTargetResolutionError
from .codec import (
    bcn_session_from_row,
    channel_session_from_row,
    consumer_cursor_from_row,
    encode_metadata,
    inbound_message_from_row,
    outbound_message_from_row,
    runtime_attempt_from_row,
    validate_inbound_message_input,
)
from .reminder_codec import reminder_from_row, reminder_occurrence_from_row
from .reminder_repository import (
    _INBOUND_COLUMNS,
    _OCCURRENCE_COLUMNS,
    _REMINDER_COLUMNS,
)
from .reminder_repository import ReminderTransaction as _ReminderTransaction

_INBOX_TARGET_CATALOG_CTE = """
WITH latest_inbound_ranked AS (
    SELECT
        session_id,
        message_id,
        canonical_target,
        sender,
        provider_time_ms,
        received_at_ms,
        ROW_NUMBER() OVER (
            PARTITION BY session_id
            ORDER BY seq DESC, message_id DESC
        ) AS inbound_rank
    FROM inbound_messages
    WHERE agent_id = bcn_agent_id()
),
latest_inbound AS (
    SELECT
        session_id,
        message_id,
        canonical_target,
        sender,
        provider_time_ms,
        received_at_ms
    FROM latest_inbound_ranked
    WHERE inbound_rank = 1
),
pending_inbound AS (
    SELECT message.session_id, COUNT(*) AS pending_count
    FROM inbound_messages AS message
    LEFT JOIN consumer_cursors AS cursor
        ON cursor.session_id = message.session_id
    WHERE message.agent_id = bcn_agent_id()
      AND message.notifies_runtime = 1
      AND message.seq > COALESCE(cursor.delivered_through_seq, 0)
    GROUP BY message.session_id
),
target_catalog AS (
    SELECT
        bcn.id AS session_id,
        COALESCE(
            latest.canonical_target,
            channel.target_kind || ':' || channel.id
        ) AS target,
        channel.target_kind AS target_kind,
        COALESCE(pending.pending_count, 0) AS pending_count,
        COALESCE(
            bcn.last_activity_at_ms,
            latest.received_at_ms,
            channel.last_inbound_at_ms,
            channel.last_outbound_at_ms,
            bcn.updated_at_ms,
            channel.updated_at_ms,
            bcn.created_at_ms,
            channel.created_at_ms,
            0
        ) AS last_activity_at_ms,
        latest.message_id AS latest_message_id,
        latest.sender AS latest_sender,
        latest.provider_time_ms AS latest_provider_time_ms,
        latest.received_at_ms AS latest_received_at_ms
    FROM bcn_sessions AS bcn
    JOIN channel_sessions AS channel
        ON channel.agent_id = bcn_agent_id()
       AND channel.id = bcn.channel_session_id
    LEFT JOIN latest_inbound AS latest
        ON latest.session_id = bcn.id
    LEFT JOIN pending_inbound AS pending
        ON pending.session_id = bcn.id
    WHERE bcn.agent_id = bcn_agent_id()
)
"""


def _inbox_target_summary_from_row(row: aiosqlite.Row) -> InboxTargetSummary:
    target = cast(str, row["target"])
    session_id = cast(str, row["session_id"])
    target_kind = ChannelTargetKind(cast(str, row["target_kind"]))
    pending_count = cast(int, row["pending_count"])
    last_activity_at_ms = cast(int, row["last_activity_at_ms"])

    latest_message_id_value = row["latest_message_id"]
    latest_message_id: str | None = None
    if latest_message_id_value is not None:
        latest_message_id = cast(str, latest_message_id_value)

    latest_sender_value = row["latest_sender"]
    latest_sender: SenderIdentity | None = None
    if latest_sender_value is not None:
        latest_sender_name = cast(str, latest_sender_value)
        latest_sender = SenderIdentity(name=latest_sender_name)

    latest_provider_time_value = row["latest_provider_time_ms"]
    latest_provider_time_ms = (
        None
        if latest_provider_time_value is None
        else cast(int, latest_provider_time_value)
    )
    latest_received_value = row["latest_received_at_ms"]
    latest_received_at_ms = (
        None if latest_received_value is None else cast(int, latest_received_value)
    )
    return InboxTargetSummary(
        target=target,
        session_id=session_id,
        target_kind=target_kind,
        current=False,
        pending_count=pending_count,
        last_activity_at_ms=last_activity_at_ms,
        latest_message_id=latest_message_id,
        latest_sender=latest_sender,
        latest_provider_time_ms=latest_provider_time_ms,
        latest_received_at_ms=latest_received_at_ms,
    )


class ReminderTransaction(_ReminderTransaction):
    """One serialized SQLite transaction bound to one configured Agent."""

    def __init__(self, database, *, agent_id: str, agent_name: str) -> None:
        super().__init__(database)
        if not isinstance(agent_id, str) or not agent_id:
            raise ValueError("agent_id must be a non-empty string")
        if not isinstance(agent_name, str) or not agent_name:
            raise ValueError("agent_name must be a non-empty string")
        self.agent_id = agent_id
        self.agent_name = agent_name

    async def __aenter__(self) -> Self:
        await self._database._transaction_lock.acquire()
        try:
            connection = self._database._require_connection()
            self._database._bind_agent_scope(self.agent_id, self.agent_name)
            await connection.execute("BEGIN IMMEDIATE")
        except BaseException:
            self._database._clear_agent_scope()
            self._database._transaction_lock.release()
            raise
        self._connection = connection
        self._active = True
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        del exc_value, traceback
        connection = self._connection
        if not self._active or connection is None:
            return False
        try:
            await connection.execute("ROLLBACK" if exc_type is not None else "COMMIT")
        except (Exception, asyncio.CancelledError) as error:
            if exc_type is None:
                try:
                    await connection.execute("ROLLBACK")
                except (Exception, asyncio.CancelledError) as rollback_error:
                    raise error from rollback_error
            raise
        finally:
            self._active = False
            self._connection = None
            self._database._clear_agent_scope()
            self._database._transaction_lock.release()
        return False

    async def find_channel_session(
        self,
        *,
        channel: str,
        provider_thread_id: str,
    ) -> ChannelSession | None:
        row = await self._fetch_one_or_conflict(
            "SELECT id, channel, provider_thread_id, target_kind, following, "
            "created_at_ms, updated_at_ms, last_inbound_at_ms, last_outbound_at_ms, "
            "provider_identity_ref_json FROM channel_sessions "
            "WHERE agent_id = bcn_agent_id() AND channel = ? "
            "AND provider_thread_id = ? ORDER BY rowid",
            (channel, provider_thread_id),
            "channel provider identity",
        )
        return channel_session_from_row(row) if row is not None else None

    async def get_channel_session(self, session_id: str) -> ChannelSession | None:
        row = await self.fetchone(
            "SELECT id, channel, provider_thread_id, target_kind, following, "
            "created_at_ms, updated_at_ms, last_inbound_at_ms, last_outbound_at_ms, "
            "provider_identity_ref_json FROM channel_sessions "
            "WHERE agent_id = bcn_agent_id() AND id = ?",
            (session_id,),
        )
        return channel_session_from_row(row) if row is not None else None

    async def get_bcn_session(self, session_id: str) -> BcnSession | None:
        row = await self.fetchone(
            "SELECT id, channel_session_id, workspace_id, created_at_ms, updated_at_ms, "
            "last_activity_at_ms, metadata_json FROM bcn_sessions "
            "WHERE agent_id = bcn_agent_id() AND id = ?",
            (session_id,),
        )
        return bcn_session_from_row(row) if row is not None else None

    async def find_bcn_session(self, channel_session_id: str) -> BcnSession | None:
        row = await self._fetch_one_or_conflict(
            "SELECT id, channel_session_id, workspace_id, created_at_ms, updated_at_ms, "
            "last_activity_at_ms, metadata_json FROM bcn_sessions "
            "WHERE agent_id = bcn_agent_id() AND channel_session_id = ? ORDER BY rowid",
            (channel_session_id,),
            "channel-to-bcn session binding",
        )
        return bcn_session_from_row(row) if row is not None else None

    async def get_runtime_attempt(self, turn_id: str) -> RuntimeAttempt | None:
        row = await self.fetchone(
            "SELECT turn_id, session_id, client_user_message_id, started_at_ms "
            "FROM runtime_attempts WHERE agent_id = bcn_agent_id() AND turn_id = ?",
            (turn_id,),
        )
        return runtime_attempt_from_row(row) if row is not None else None

    async def get_consumer_cursor(self, session_id: str) -> ConsumerCursor | None:
        if await self.get_bcn_session(session_id) is None:
            return None
        row = await self.fetchone(
            "SELECT session_id, delivered_through_seq, inbox_snapshot_seq, "
            "inbox_snapshot_source, inbox_snapshot_at_ms, last_check_at_ms, "
            "last_read_at_ms, updated_at_ms FROM consumer_cursors WHERE session_id = ?",
            (session_id,),
        )
        return consumer_cursor_from_row(row) if row is not None else None

    async def get_latest_inbound_seq(self, session_id: str) -> int:
        row = await self.fetchone(
            "SELECT COALESCE(MAX(seq), 0) AS latest_seq FROM inbound_messages "
            "WHERE agent_id = bcn_agent_id() AND session_id = ?",
            (session_id,),
        )
        if row is None:
            raise RuntimeError("SQLite latest inbound sequence query returned no row")
        return cast(int, row["latest_seq"])

    async def list_inbox_targets(
        self, *, limit: int = 100, offset: int = 0
    ) -> InboxTargetPage:
        total_row = await self.fetchone(
            _INBOX_TARGET_CATALOG_CTE + "SELECT COUNT(*) AS total FROM target_catalog"
        )
        if total_row is None:
            raise RuntimeError("SQLite inbox target count query returned no row")
        total = cast(int, total_row["total"])

        rows = await self.fetchall(
            _INBOX_TARGET_CATALOG_CTE
            + "SELECT target, session_id, target_kind, pending_count, "
            "last_activity_at_ms, latest_message_id, latest_sender, "
            "latest_provider_time_ms, latest_received_at_ms "
            "FROM target_catalog "
            "ORDER BY last_activity_at_ms DESC, session_id "
            "LIMIT ? OFFSET ?",
            (limit, offset),
        )
        targets = tuple(_inbox_target_summary_from_row(row) for row in rows)
        return InboxTargetPage(
            targets=targets,
            total=total,
            offset=offset,
        )

    async def resolve_inbox_target(self, target: str) -> BcnSession:
        rows = await self.fetchall(
            "SELECT bcn.id, bcn.channel_session_id, bcn.workspace_id, "
            "bcn.created_at_ms, bcn.updated_at_ms, bcn.last_activity_at_ms, "
            "bcn.metadata_json "
            "FROM bcn_sessions AS bcn "
            "JOIN channel_sessions AS channel "
            "ON channel.agent_id = bcn_agent_id() "
            "AND channel.id = bcn.channel_session_id "
            "WHERE bcn.agent_id = bcn_agent_id() "
            "AND ("
            "channel.target_kind || ':' || channel.id = ? "
            "OR EXISTS ("
            "SELECT 1 FROM inbound_messages AS message "
            "WHERE message.agent_id = bcn_agent_id() "
            "AND message.session_id = bcn.id "
            "AND message.canonical_target = ?"
            ")"
            ") ORDER BY bcn.id",
            (target, target),
        )
        if len(rows) != 1:
            raise InboxTargetResolutionError(
                "inbox target does not resolve to exactly one owned session"
            )
        return bcn_session_from_row(rows[0])

    async def find_inbound_message(
        self,
        channel: str,
        provider_thread_id: str,
        provider_message_id: str,
    ) -> InboundMessage | None:
        row = await self._fetch_one_or_conflict(
            f"SELECT {_INBOUND_COLUMNS} FROM inbound_messages "
            "WHERE agent_id = bcn_agent_id() AND channel = ? "
            "AND provider_thread_id = ? AND provider_message_id = ? ORDER BY seq",
            (channel, provider_thread_id, provider_message_id),
            "provider inbound identity",
        )
        if row is None:
            return None
        return inbound_message_from_row(
            row,
            await self._attachments(row["message_id"]),
        )

    async def list_ready_attachment_paths(self) -> tuple[str, ...]:
        rows = await self.fetchall(
            "SELECT attachment.relative_path FROM inbound_attachments AS attachment "
            "JOIN inbound_messages AS message ON message.message_id = attachment.message_id "
            "WHERE message.agent_id = bcn_agent_id() AND attachment.state = 'ready' "
            "ORDER BY attachment.attachment_id"
        )
        return tuple(str(row["relative_path"]) for row in rows)

    async def list_inbound_messages(
        self,
        session_id: str,
        *,
        after_seq: int | None = None,
        target: str | None = None,
        around_message_id: str | None = None,
        notifying_only: bool = False,
        limit: int = 100,
    ) -> tuple[InboundMessage, ...]:
        predicates = ["agent_id = bcn_agent_id()", "session_id = ?"]
        parameters: list[object] = [session_id]
        if after_seq is not None:
            predicates.append("seq > ?")
            parameters.append(after_seq)
        if target is not None:
            predicates.append("canonical_target = ?")
            parameters.append(target)
        if notifying_only:
            predicates.append("notifies_runtime = 1")
        where_clause = " AND ".join(predicates)
        if around_message_id is None:
            rows = await self.fetchall(
                f"SELECT {_INBOUND_COLUMNS} FROM inbound_messages "
                f"WHERE {where_clause} ORDER BY seq LIMIT ?",
                (*parameters, limit),
            )
            messages: list[InboundMessage] = []
            for row in rows:
                messages.append(
                    inbound_message_from_row(
                        row,
                        await self._attachments(row["message_id"]),
                    )
                )
            return tuple(messages)
        anchor = await self.fetchone(
            f"SELECT seq FROM inbound_messages WHERE {where_clause} AND message_id = ?",
            (*parameters, around_message_id),
        )
        if anchor is None:
            raise ValueError(
                f"message not found in requested history: {around_message_id}"
            )
        anchor_seq = cast(int, anchor["seq"])
        count_row = await self.fetchone(
            f"SELECT COUNT(*) AS message_count FROM inbound_messages WHERE {where_clause}",
            parameters,
        )
        if count_row is None:
            raise RuntimeError("SQLite inbound history count query returned no row")
        message_count = cast(int, count_row["message_count"])
        position_row = await self.fetchone(
            f"SELECT COUNT(*) AS anchor_position FROM inbound_messages "
            f"WHERE {where_clause} AND seq <= ?",
            (*parameters, anchor_seq),
        )
        if position_row is None:
            raise RuntimeError("SQLite inbound anchor position query returned no row")
        anchor_position = cast(int, position_row["anchor_position"])
        start_position = max(anchor_position - limit // 2, 1)
        start_position = min(start_position, max(message_count - limit + 1, 1))
        end_position = start_position + limit - 1
        rows = await self.fetchall(
            "WITH filtered AS (SELECT "
            + _INBOUND_COLUMNS
            + ", ROW_NUMBER() OVER (ORDER BY seq) AS row_number FROM inbound_messages "
            + f"WHERE {where_clause}) SELECT {_INBOUND_COLUMNS} FROM filtered "
            "WHERE row_number BETWEEN ? AND ? ORDER BY row_number",
            (*parameters, start_position, end_position),
        )
        messages = []
        for row in rows:
            messages.append(
                inbound_message_from_row(
                    row,
                    await self._attachments(row["message_id"]),
                )
            )
        return tuple(messages)

    async def append_inbound_message(self, message: InboundMessage) -> InboundMessage:
        validate_inbound_message_input(message)
        bcn_session = await self.get_bcn_session(message.session_id)
        if bcn_session is None:
            raise ValueError(f"unknown bcn session: {message.session_id}")
        channel_session = await self.get_channel_session(bcn_session.channel_session_id)
        if channel_session is None:
            raise ValueError(
                f"unknown channel session: {bcn_session.channel_session_id}"
            )
        if (
            message.channel_session_id != channel_session.id
            or message.channel != channel_session.channel
            or message.provider_thread_id != channel_session.provider_thread_id
        ):
            raise ValueError("inbound message binding does not match channel session")
        existing_row = await self._fetch_one_or_conflict(
            f"SELECT {_INBOUND_COLUMNS} FROM inbound_messages "
            "WHERE agent_id = bcn_agent_id() AND channel = ? "
            "AND provider_thread_id = ? AND provider_message_id = ? ORDER BY seq",
            (
                message.channel,
                message.provider_thread_id,
                message.provider_message_id,
            ),
            "provider inbound identity",
        )
        if existing_row is not None:
            return inbound_message_from_row(
                existing_row,
                await self._attachments(existing_row["message_id"]),
            )

        message_id = message.message_id
        message_id_row = await self.fetchone(
            "SELECT agent_id FROM inbound_messages WHERE message_id = ?",
            (message_id,),
        )
        if message_id_row is not None:
            if message_id_row["agent_id"] == self.agent_id:
                raise ValueError(
                    "message id is already bound to another inbound message"
                )
            message_id = self._agent_local_id("message", message_id)
            if (
                await self.fetchone(
                    "SELECT 1 FROM inbound_messages WHERE message_id = ?",
                    (message_id,),
                )
                is not None
            ):
                raise ValueError("Agent-scoped message id is already in use")

        sequence_row = await self.fetchone(
            "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM inbound_messages"
        )
        if sequence_row is None:
            raise RuntimeError("SQLite inbound sequence query returned no row")
        canonical = replace(
            message,
            message_id=message_id,
            seq=cast(int, sequence_row["next_seq"]),
        )
        if canonical.reply_to_message_id is not None:
            referenced_id = canonical.reply_to_message_id
            referenced = await self.fetchone(
                "SELECT message_id, session_id, seq FROM inbound_messages "
                "WHERE agent_id = bcn_agent_id() AND message_id = ?",
                (referenced_id,),
            )
            if referenced is None:
                referenced_id = self._agent_local_id("message", referenced_id)
                referenced = await self.fetchone(
                    "SELECT message_id, session_id, seq FROM inbound_messages "
                    "WHERE agent_id = bcn_agent_id() AND message_id = ?",
                    (referenced_id,),
                )
            if referenced is None:
                raise ValueError("reply_to_message_id does not reference a message")
            if referenced["session_id"] != canonical.session_id:
                raise ValueError("reply_to_message_id must belong to the same session")
            referenced_seq = cast(int, referenced["seq"])
            if referenced_seq >= canonical.seq:
                raise ValueError(
                    "reply_to_message_id must reference an earlier message"
                )
            canonical = replace(
                canonical,
                reply_to_message_id=str(referenced["message_id"]),
            )

        canonical_attachments: list[InboundAttachment] = []
        for attachment in canonical.attachments:
            attachment_id = attachment.attachment_id
            attachment_row = await self.fetchone(
                "SELECT message.agent_id FROM inbound_attachments AS attachment "
                "JOIN inbound_messages AS message ON message.message_id = attachment.message_id "
                "WHERE attachment.attachment_id = ?",
                (attachment_id,),
            )
            if attachment_row is not None:
                if attachment_row["agent_id"] == self.agent_id:
                    raise ValueError("attachment id is already in use by this Agent")
                attachment_id = self._agent_local_id("attachment", attachment_id)
                if (
                    await self.fetchone(
                        "SELECT 1 FROM inbound_attachments WHERE attachment_id = ?",
                        (attachment_id,),
                    )
                    is not None
                ):
                    raise ValueError("Agent-scoped attachment id is already in use")
            canonical_attachments.append(
                replace(attachment, attachment_id=attachment_id)
            )
        canonical = replace(canonical, attachments=tuple(canonical_attachments))

        await self.execute(
            "INSERT INTO inbound_messages ("
            "message_id, seq, session_id, channel_session_id, channel, "
            "provider_thread_id, provider_message_id, provider_time_ms, "
            "received_at_ms, sender, message_type, canonical_target, target_kind, "
            "reply_to_message_id, body, mentions_agent, notifies_runtime, "
            "provider_payload_ref, metadata_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                canonical.message_id,
                canonical.seq,
                canonical.session_id,
                canonical.channel_session_id,
                canonical.channel,
                canonical.provider_thread_id,
                canonical.provider_message_id,
                canonical.provider_time_ms,
                canonical.received_at_ms,
                (
                    canonical.sender.display_name
                    if canonical.sender is not None
                    else None
                ),
                canonical.message_type,
                canonical.canonical_target,
                canonical.target_kind.value,
                canonical.reply_to_message_id,
                canonical.body,
                int(canonical.mentions_agent),
                int(canonical.notifies_runtime),
                canonical.provider_payload_ref,
                encode_metadata(canonical.metadata),
            ),
        )
        for ordinal, attachment in enumerate(canonical.attachments):
            await self.execute(
                "INSERT INTO inbound_attachments ("
                "attachment_id, message_id, ordinal, name, kind, state, media_type, "
                "relative_path, size_bytes, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    attachment.attachment_id,
                    canonical.message_id,
                    ordinal,
                    attachment.name,
                    attachment.kind,
                    attachment.state,
                    attachment.media_type,
                    attachment.relative_path,
                    attachment.size_bytes,
                    attachment.error,
                ),
            )
        return canonical

    async def get_outbound_message(
        self,
        outbound_message_id: str,
    ) -> OutboundMessage | None:
        row = await self.fetchone(
            "SELECT outbound_message_id, command_id, session_id, channel_session_id, "
            "target, reply_to_message_id, body, attachments_json, state, "
            "fresh_check_state, snapshot_seq, current_inbound_seq, provider_message_id, "
            "provider_receipt_ref, created_at_ms, provider_attempted_at_ms, "
            "completed_at_ms, draft_saved_at_ms, error_kind, error_message, "
            "next_action, metadata_json FROM outbound_messages "
            "WHERE agent_id = bcn_agent_id() AND outbound_message_id = ?",
            (outbound_message_id,),
        )
        return outbound_message_from_row(row) if row is not None else None

    async def resolve_inbound_message(
        self,
        session_id: str,
        message_id: str,
    ) -> InboundMessage | None:
        reference = canonical_id_reference(message_id)
        row = await self.fetchone(
            f"SELECT {_INBOUND_COLUMNS} FROM inbound_messages "
            "WHERE agent_id = bcn_agent_id() AND session_id = ? AND message_id = ?",
            (session_id, reference),
        )
        if row is None:
            return None
        return inbound_message_from_row(
            row,
            await self._attachments(row["message_id"]),
        )

    async def get_reminder(
        self,
        owner_session_id: str,
        reminder_id: str,
    ) -> Reminder | None:
        reference = canonical_id_reference(reminder_id)
        row = await self.fetchone(
            f"SELECT {_REMINDER_COLUMNS} FROM reminders "
            "WHERE agent_id = bcn_agent_id() AND owner_session_id = ? "
            "AND reminder_id = ?",
            (owner_session_id, reference),
        )
        return reminder_from_row(row) if row is not None else None

    async def list_reminders(
        self,
        owner_session_id: str,
        statuses: frozenset[ReminderState],
    ) -> tuple[Reminder, ...]:
        if not isinstance(statuses, frozenset) or not statuses:
            raise ValueError("statuses must be a non-empty frozenset")
        if not all(isinstance(status, ReminderState) for status in statuses):
            raise TypeError("statuses must contain ReminderState values")
        ordered = tuple(sorted(status.value for status in statuses))
        placeholders = ", ".join("?" for _ in ordered)
        rows = await self.fetchall(
            f"SELECT {_REMINDER_COLUMNS} FROM reminders "
            f"WHERE agent_id = bcn_agent_id() AND owner_session_id = ? "
            f"AND state IN ({placeholders}) ORDER BY updated_at_ms DESC, reminder_id",
            (owner_session_id, *ordered),
        )
        return tuple(reminder_from_row(row) for row in rows)

    async def get_next_scheduled_reminder(self) -> Reminder | None:
        row = await self.fetchone(
            f"SELECT {_REMINDER_COLUMNS} FROM reminders "
            "WHERE agent_id = bcn_agent_id() AND state = ? "
            "ORDER BY next_fire_at_ms, reminder_id LIMIT 1",
            (ReminderState.SCHEDULED.value,),
        )
        return reminder_from_row(row) if row is not None else None

    async def list_due_reminders(
        self,
        now_ms: int,
        *,
        limit: int,
    ) -> tuple[Reminder, ...]:
        rows = await self.fetchall(
            f"SELECT {_REMINDER_COLUMNS} FROM reminders "
            "WHERE agent_id = bcn_agent_id() AND state = ? AND next_fire_at_ms <= ? "
            "ORDER BY next_fire_at_ms, reminder_id LIMIT ?",
            (ReminderState.SCHEDULED.value, now_ms, limit),
        )
        return tuple(reminder_from_row(row) for row in rows)

    async def save_fired_occurrence(
        self,
        expected_revision: int,
        reminder: object,
        occurrence: object,
    ) -> ReminderOccurrence:
        if not isinstance(reminder, Reminder):
            raise TypeError("reminder must be a Reminder")
        if not isinstance(occurrence, ReminderOccurrence):
            raise TypeError("occurrence must be a ReminderOccurrence")
        existing = await self.get_reminder(
            reminder.owner_session_id,
            reminder.reminder_id,
        )
        if existing is None:
            raise ValueError("reminder not found")
        self._validate_expected_revision(existing, expected_revision)
        self._validate_reminder_identity(existing, reminder)
        if existing.state is not ReminderState.SCHEDULED:
            raise ValueError("only a scheduled reminder can fire")
        if reminder.revision != expected_revision + 1:
            raise ValueError("reminder revision must advance by exactly one")
        if reminder.last_occurrence_no != existing.last_occurrence_no + 1:
            raise ValueError("reminder occurrence number must advance by exactly one")
        if reminder.last_fired_at_ms != occurrence.fired_at_ms:
            raise ValueError("reminder fire time does not match occurrence")
        if reminder.updated_at_ms != occurrence.fired_at_ms:
            raise ValueError("reminder update time does not match occurrence fire")
        if reminder.title != existing.title:
            raise ValueError("fire cannot change reminder title")
        if reminder.repeat_rule != existing.repeat_rule:
            raise ValueError("fire cannot change reminder cadence")
        if occurrence.reminder_id != existing.reminder_id:
            raise ValueError("occurrence reminder binding does not match")
        if occurrence.owner_session_id != existing.owner_session_id:
            raise ValueError("occurrence owner binding does not match")
        if occurrence.anchor_message_id != existing.anchor_message_id:
            raise ValueError("occurrence anchor binding does not match")
        if occurrence.occurrence_no != reminder.last_occurrence_no:
            raise ValueError("occurrence number does not match reminder history")
        if occurrence.scheduled_for_ms != existing.next_fire_at_ms:
            raise ValueError("occurrence scheduled slot does not match reminder")
        if occurrence.next_fire_at_ms != reminder.next_fire_at_ms:
            raise ValueError("occurrence next fire does not match reminder")
        if occurrence.overdue != (occurrence.fired_at_ms > occurrence.scheduled_for_ms):
            raise ValueError("occurrence overdue flag does not match fire time")
        if occurrence.read_at_ms is not None:
            raise ValueError("a new occurrence must be pending")
        duplicate = await self.fetchone(
            "SELECT 1 FROM reminder_occurrences "
            "WHERE agent_id = bcn_agent_id() AND reminder_id = ? AND occurrence_no = ?",
            (existing.reminder_id, occurrence.occurrence_no),
        )
        if duplicate is not None:
            raise ValueError("reminder occurrence number is already persisted")
        canonical = replace(occurrence, occurrence_id=str(uuid7()))
        await self.execute(
            "INSERT INTO reminder_occurrences ("
            "occurrence_id, reminder_id, owner_session_id, occurrence_no, "
            "anchor_message_id, scheduled_for_ms, fired_at_ms, next_fire_at_ms, "
            "overdue, read_at_ms, created_at_ms) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                canonical.occurrence_id,
                canonical.reminder_id,
                canonical.owner_session_id,
                canonical.occurrence_no,
                canonical.anchor_message_id,
                canonical.scheduled_for_ms,
                canonical.fired_at_ms,
                canonical.next_fire_at_ms,
                int(canonical.overdue),
                canonical.read_at_ms,
                canonical.created_at_ms,
            ),
        )
        await self._update_reminder(reminder)
        return canonical

    async def list_pending_reminder_occurrences(
        self,
        owner_session_id: str,
        *,
        limit: int,
    ) -> tuple[ReminderOccurrence, ...]:
        rows = await self.fetchall(
            f"SELECT {_OCCURRENCE_COLUMNS} FROM reminder_occurrences "
            "WHERE agent_id = bcn_agent_id() AND owner_session_id = ? "
            "AND read_at_ms IS NULL ORDER BY fired_at_ms, occurrence_id LIMIT ?",
            (owner_session_id, limit),
        )
        return tuple(reminder_occurrence_from_row(row) for row in rows)

    async def count_pending_reminder_occurrences(self, owner_session_id: str) -> int:
        row = await self.fetchone(
            "SELECT COUNT(*) AS pending_count FROM reminder_occurrences "
            "WHERE agent_id = bcn_agent_id() AND owner_session_id = ? "
            "AND read_at_ms IS NULL",
            (owner_session_id,),
        )
        if row is None:
            raise RuntimeError("SQLite reminder pending count returned no row")
        return cast(int, row["pending_count"])

    async def mark_reminder_occurrences_read(
        self,
        owner_session_id: str,
        occurrence_ids: object,
        *,
        read_at_ms: int,
    ) -> tuple[ReminderOccurrence, ...]:
        if not isinstance(occurrence_ids, tuple):
            raise TypeError("occurrence_ids must be a tuple")
        occurrence_ids = cast(tuple[str, ...], occurrence_ids)
        if not occurrence_ids:
            return ()
        if len(set(occurrence_ids)) != len(occurrence_ids):
            raise ValueError("occurrence_ids cannot contain duplicates")
        marked: list[ReminderOccurrence] = []
        for occurrence_id in occurrence_ids:
            reference = canonical_id_reference(occurrence_id)
            row = await self.fetchone(
                f"SELECT {_OCCURRENCE_COLUMNS} FROM reminder_occurrences "
                "WHERE agent_id = bcn_agent_id() AND occurrence_id = ? "
                "AND owner_session_id = ?",
                (reference, owner_session_id),
            )
            if row is None:
                raise ValueError("reminder occurrence does not belong to owner")
            occurrence = reminder_occurrence_from_row(row)
            if not occurrence.pending:
                raise ValueError("reminder occurrence was already read")
            updated = occurrence.mark_read(at_ms=read_at_ms)
            await self.execute(
                "UPDATE reminder_occurrences SET read_at_ms = ? "
                "WHERE agent_id = bcn_agent_id() AND occurrence_id = ? "
                "AND owner_session_id = ? AND read_at_ms IS NULL",
                (read_at_ms, reference, owner_session_id),
            )
            marked.append(updated)
        return tuple(marked)

    async def list_sessions_with_pending_reminders(self) -> tuple[str, ...]:
        rows = await self.fetchall(
            "SELECT DISTINCT owner_session_id FROM reminder_occurrences "
            "WHERE agent_id = bcn_agent_id() AND read_at_ms IS NULL "
            "ORDER BY owner_session_id"
        )
        return tuple(str(row["owner_session_id"]) for row in rows)

    async def _update_reminder(self, reminder: Reminder) -> None:
        await self.execute(
            "UPDATE reminders SET title = ?, state = ?, next_fire_at_ms = ?, "
            "repeat_rule = ?, timezone = ?, revision = ?, last_occurrence_no = ?, "
            "updated_at_ms = ?, last_fired_at_ms = ?, canceled_at_ms = ? "
            "WHERE agent_id = bcn_agent_id() AND reminder_id = ? AND owner_session_id = ?",
            (
                reminder.title,
                reminder.state.value,
                reminder.next_fire_at_ms,
                reminder.repeat_rule,
                reminder.timezone,
                reminder.revision,
                reminder.last_occurrence_no,
                reminder.updated_at_ms,
                reminder.last_fired_at_ms,
                reminder.canceled_at_ms,
                reminder.reminder_id,
                reminder.owner_session_id,
            ),
        )

    def _require_workspace(self, workspace_id: str) -> None:
        if workspace_id != self.agent_id:
            raise ValueError("session workspace does not match the Agent workspace")

    def _agent_local_id(self, kind: str, local_id: str) -> str:
        if not isinstance(local_id, str) or not local_id:
            raise ValueError(f"{kind} id must be non-empty")
        return str(
            uuid5(
                NAMESPACE_URL,
                f"bcn:{self.agent_id}:{kind}:{local_id}",
            )
        )


__all__ = ["ReminderTransaction"]
