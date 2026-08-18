from __future__ import annotations

import asyncio
from dataclasses import replace
from types import TracebackType
from typing import Self
from uuid import NAMESPACE_URL, uuid5, uuid7

from ...core.models import (
    BcnSession,
    ChannelSession,
    ConsumerCursor,
    InboundAttachment,
    InboundMessage,
    OutboundMessage,
    Reminder,
    ReminderOccurrence,
    ReminderState,
    RuntimeAttempt,
)
from ...core.reminder import canonical_id_reference
from .codec import (
    _bcn_session_from_row,
    _channel_session_from_row,
    _consumer_cursor_from_row,
    _encode_metadata,
    _inbound_message_from_row,
    _outbound_message_from_row,
    _required_non_negative_int,
    _required_positive_int,
    _runtime_attempt_from_row,
    _validate_inbound_message_input,
    _validate_non_empty_text,
    _validate_non_negative_int,
    _validate_positive_int,
)
from .reminder_codec import reminder_from_row, reminder_occurrence_from_row
from .reminder_repository import (
    _INBOUND_COLUMNS,
    _OCCURRENCE_COLUMNS,
    _REMINDER_COLUMNS,
)
from .reminder_repository import ReminderTransaction as _ReminderTransaction


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
        return _channel_session_from_row(row) if row is not None else None

    async def get_channel_session(self, session_id: str) -> ChannelSession | None:
        row = await self.fetchone(
            "SELECT id, channel, provider_thread_id, target_kind, following, "
            "created_at_ms, updated_at_ms, last_inbound_at_ms, last_outbound_at_ms, "
            "provider_identity_ref_json FROM channel_sessions "
            "WHERE agent_id = bcn_agent_id() AND id = ?",
            (session_id,),
        )
        return _channel_session_from_row(row) if row is not None else None

    async def get_bcn_session(self, session_id: str) -> BcnSession | None:
        row = await self.fetchone(
            "SELECT id, channel_session_id, workspace_id, created_at_ms, updated_at_ms, "
            "last_activity_at_ms, metadata_json FROM bcn_sessions "
            "WHERE agent_id = bcn_agent_id() AND id = ?",
            (session_id,),
        )
        return _bcn_session_from_row(row) if row is not None else None

    async def find_bcn_session(self, channel_session_id: str) -> BcnSession | None:
        row = await self._fetch_one_or_conflict(
            "SELECT id, channel_session_id, workspace_id, created_at_ms, updated_at_ms, "
            "last_activity_at_ms, metadata_json FROM bcn_sessions "
            "WHERE agent_id = bcn_agent_id() AND channel_session_id = ? ORDER BY rowid",
            (channel_session_id,),
            "channel-to-bcn session binding",
        )
        return _bcn_session_from_row(row) if row is not None else None

    async def get_runtime_attempt(self, turn_id: str) -> RuntimeAttempt | None:
        row = await self.fetchone(
            "SELECT turn_id, session_id, client_user_message_id, started_at_ms "
            "FROM runtime_attempts WHERE agent_id = bcn_agent_id() AND turn_id = ?",
            (turn_id,),
        )
        return _runtime_attempt_from_row(row) if row is not None else None

    async def get_consumer_cursor(self, session_id: str) -> ConsumerCursor | None:
        if await self.get_bcn_session(session_id) is None:
            return None
        row = await self.fetchone(
            "SELECT session_id, delivered_through_seq, inbox_snapshot_seq, "
            "inbox_snapshot_source, inbox_snapshot_at_ms, last_check_at_ms, "
            "last_read_at_ms, updated_at_ms FROM consumer_cursors WHERE session_id = ?",
            (session_id,),
        )
        return _consumer_cursor_from_row(row) if row is not None else None

    async def get_latest_inbound_seq(self, session_id: str) -> int:
        row = await self.fetchone(
            "SELECT COALESCE(MAX(seq), 0) AS latest_seq FROM inbound_messages "
            "WHERE agent_id = bcn_agent_id() AND session_id = ?",
            (session_id,),
        )
        if row is None:
            raise RuntimeError("SQLite latest inbound sequence query returned no row")
        return _required_non_negative_int(row["latest_seq"], "latest_inbound_seq")

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
        return _inbound_message_from_row(
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
        _validate_non_empty_text(session_id, "session_id")
        if after_seq is not None:
            _validate_non_negative_int(after_seq, "after_seq")
        if target is not None:
            _validate_non_empty_text(target, "target")
        if around_message_id is not None:
            _validate_non_empty_text(around_message_id, "around_message_id")
        _validate_positive_int(limit, "limit")
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
                    _inbound_message_from_row(
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
        anchor_seq = _required_non_negative_int(anchor["seq"], "anchor_seq")
        count_row = await self.fetchone(
            f"SELECT COUNT(*) AS message_count FROM inbound_messages WHERE {where_clause}",
            parameters,
        )
        if count_row is None:
            raise RuntimeError("SQLite inbound history count query returned no row")
        message_count = _required_non_negative_int(
            count_row["message_count"], "message_count"
        )
        position_row = await self.fetchone(
            f"SELECT COUNT(*) AS anchor_position FROM inbound_messages "
            f"WHERE {where_clause} AND seq <= ?",
            (*parameters, anchor_seq),
        )
        if position_row is None:
            raise RuntimeError("SQLite inbound anchor position query returned no row")
        anchor_position = _required_positive_int(
            position_row["anchor_position"], "anchor_position"
        )
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
                _inbound_message_from_row(
                    row,
                    await self._attachments(row["message_id"]),
                )
            )
        return tuple(messages)

    async def append_inbound_message(self, message: InboundMessage) -> InboundMessage:
        _validate_inbound_message_input(message)
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
            return _inbound_message_from_row(
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
            seq=_required_positive_int(sequence_row["next_seq"], "next_seq"),
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
            referenced_seq = _required_positive_int(
                referenced["seq"], "reply_to_message_seq"
            )
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
                canonical.sender,
                canonical.message_type,
                canonical.canonical_target,
                canonical.target_kind.value,
                canonical.reply_to_message_id,
                canonical.body,
                int(canonical.mentions_agent),
                int(canonical.notifies_runtime),
                canonical.provider_payload_ref,
                _encode_metadata(canonical.metadata),
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
        return _outbound_message_from_row(row) if row is not None else None

    async def resolve_inbound_message(
        self,
        session_id: str,
        message_id_or_prefix: str,
    ) -> InboundMessage | None:
        _validate_non_empty_text(session_id, "session_id")
        reference = canonical_id_reference(message_id_or_prefix)
        if len(reference) == 8:
            rows = await self.fetchall(
                f"SELECT {_INBOUND_COLUMNS} FROM inbound_messages "
                "WHERE agent_id = bcn_agent_id() AND session_id = ? "
                "AND message_id LIKE ? ORDER BY seq",
                (session_id, f"{reference}%"),
            )
            if len(rows) > 1:
                raise ValueError("inbound message id prefix is ambiguous")
            row = rows[0] if rows else None
        else:
            row = await self.fetchone(
                f"SELECT {_INBOUND_COLUMNS} FROM inbound_messages "
                "WHERE agent_id = bcn_agent_id() AND session_id = ? AND message_id = ?",
                (session_id, reference),
            )
        if row is None:
            return None
        return _inbound_message_from_row(
            row,
            await self._attachments(row["message_id"]),
        )

    async def get_reminder(
        self,
        owner_session_id: str,
        reminder_id_or_prefix: str,
    ) -> Reminder | None:
        _validate_non_empty_text(owner_session_id, "owner_session_id")
        reference = canonical_id_reference(reminder_id_or_prefix)
        if len(reference) == 8:
            rows = await self.fetchall(
                f"SELECT {_REMINDER_COLUMNS} FROM reminders "
                "WHERE agent_id = bcn_agent_id() AND owner_session_id = ? "
                "AND reminder_id LIKE ? ORDER BY reminder_id",
                (owner_session_id, f"{reference}%"),
            )
            if len(rows) > 1:
                raise ValueError("reminder id prefix is ambiguous")
            row = rows[0] if rows else None
        else:
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
        _validate_non_empty_text(owner_session_id, "owner_session_id")
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
        _validate_non_negative_int(now_ms, "now_ms")
        _validate_positive_int(limit, "limit")
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
        reminder: Reminder,
        occurrence: ReminderOccurrence,
    ) -> ReminderOccurrence:
        _validate_positive_int(expected_revision, "expected_revision")
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
        _validate_non_empty_text(owner_session_id, "owner_session_id")
        _validate_positive_int(limit, "limit")
        rows = await self.fetchall(
            f"SELECT {_OCCURRENCE_COLUMNS} FROM reminder_occurrences "
            "WHERE agent_id = bcn_agent_id() AND owner_session_id = ? "
            "AND read_at_ms IS NULL ORDER BY fired_at_ms, occurrence_id LIMIT ?",
            (owner_session_id, limit),
        )
        return tuple(reminder_occurrence_from_row(row) for row in rows)

    async def count_pending_reminder_occurrences(self, owner_session_id: str) -> int:
        _validate_non_empty_text(owner_session_id, "owner_session_id")
        row = await self.fetchone(
            "SELECT COUNT(*) AS pending_count FROM reminder_occurrences "
            "WHERE agent_id = bcn_agent_id() AND owner_session_id = ? "
            "AND read_at_ms IS NULL",
            (owner_session_id,),
        )
        if row is None:
            raise RuntimeError("SQLite reminder pending count returned no row")
        return _required_non_negative_int(row["pending_count"], "pending_count")

    async def mark_reminder_occurrences_read(
        self,
        owner_session_id: str,
        occurrence_ids: tuple[str, ...],
        *,
        read_at_ms: int,
    ) -> tuple[ReminderOccurrence, ...]:
        _validate_non_empty_text(owner_session_id, "owner_session_id")
        _validate_non_negative_int(read_at_ms, "read_at_ms")
        if not isinstance(occurrence_ids, tuple):
            raise TypeError("occurrence_ids must be a tuple")
        if not occurrence_ids:
            return ()
        if len(set(occurrence_ids)) != len(occurrence_ids):
            raise ValueError("occurrence_ids cannot contain duplicates")
        marked: list[ReminderOccurrence] = []
        for occurrence_id in occurrence_ids:
            reference = canonical_id_reference(occurrence_id)
            if len(reference) != 36:
                raise ValueError("occurrence ids must be full UUIDs")
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
