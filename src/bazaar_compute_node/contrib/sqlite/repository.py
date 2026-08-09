from __future__ import annotations

import asyncio
from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import replace
from types import TracebackType
from typing import TYPE_CHECKING, Self
from uuid import uuid7

import aiosqlite

from ...core.models import (
    BcnSession,
    ChannelSession,
    ConsumerCursor,
    InboundMessage,
    OutboundMessage,
    RuntimeEvent,
    RuntimeSession,
    RuntimeTurn,
    RuntimeTurnState,
)
from .codec import (
    _bcn_session_from_row,
    _channel_session_from_row,
    _consumer_cursor_from_row,
    _encode_metadata,
    _inbound_attachment_from_row,
    _inbound_message_from_row,
    _outbound_message_from_row,
    _required_non_negative_int,
    _required_positive_int,
    _runtime_event_from_row,
    _runtime_session_from_row,
    _runtime_turn_from_row,
    _same_inbound_payload,
    _same_runtime_event_payload,
    _validate_bcn_session_input,
    _validate_bcn_session_update,
    _validate_channel_session_input,
    _validate_channel_session_update,
    _validate_consumer_cursor_input,
    _validate_consumer_cursor_update,
    _validate_cursor_bounds,
    _validate_inbound_message_input,
    _validate_non_empty_text,
    _validate_non_negative_int,
    _validate_outbound_insert,
    _validate_outbound_message_input,
    _validate_outbound_update,
    _validate_positive_int,
    _validate_runtime_event_input,
    _validate_runtime_session_input,
    _validate_runtime_session_update,
    _validate_runtime_turn_input,
    _validate_runtime_turn_update,
)

if TYPE_CHECKING:
    from .database import SqliteDatabase


class SqliteTransaction(AbstractAsyncContextManager["SqliteTransaction"]):
    """An explicit IMMEDIATE transaction on the database's long-lived connection."""

    def __init__(self, database: SqliteDatabase) -> None:
        self._database = database
        self._connection: aiosqlite.Connection | None = None
        self._active = False

    async def __aenter__(self) -> Self:
        await self._database._transaction_lock.acquire()
        try:
            connection = self._database._require_connection()
            await connection.execute("BEGIN IMMEDIATE")
        except BaseException:
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
            self._database._transaction_lock.release()
        return False

    async def execute(
        self,
        statement: str,
        parameters: Sequence[object] = (),
    ) -> aiosqlite.Cursor:
        connection = self._require_active_connection()
        return await connection.execute(statement, parameters)

    async def fetchone(
        self,
        statement: str,
        parameters: Sequence[object] = (),
    ) -> aiosqlite.Row | None:
        cursor = await self.execute(statement, parameters)
        try:
            return await cursor.fetchone()
        finally:
            await cursor.close()

    async def fetchall(
        self,
        statement: str,
        parameters: Sequence[object] = (),
    ) -> list[aiosqlite.Row]:
        cursor = await self.execute(statement, parameters)
        try:
            return list(await cursor.fetchall())
        finally:
            await cursor.close()

    async def find_channel_session(
        self,
        *,
        channel: str,
        provider_conversation_key: str,
        provider_thread_key: str,
    ) -> ChannelSession | None:
        row = await self._fetch_one_or_conflict(
            "SELECT id, channel, "
            "provider_conversation_key, provider_thread_key, target_kind, following, state, "
            "created_at_ms, updated_at_ms, last_inbound_at_ms, last_outbound_at_ms, "
            "provider_identity_ref_json "
            "FROM channel_sessions "
            "WHERE channel = ? AND provider_conversation_key = ? "
            "AND provider_thread_key = ? ORDER BY rowid",
            (channel, provider_conversation_key, provider_thread_key),
            "channel provider identity",
        )
        return _channel_session_from_row(row) if row is not None else None

    async def get_channel_session(self, session_id: str) -> ChannelSession | None:
        row = await self.fetchone(
            "SELECT id, channel, "
            "provider_conversation_key, provider_thread_key, target_kind, following, state, "
            "created_at_ms, updated_at_ms, last_inbound_at_ms, last_outbound_at_ms, "
            "provider_identity_ref_json "
            "FROM channel_sessions WHERE id = ?",
            (session_id,),
        )
        return _channel_session_from_row(row) if row is not None else None

    async def get_bcn_session(self, session_id: str) -> BcnSession | None:
        row = await self.fetchone(
            "SELECT id, channel_session_id, workspace_id, state, "
            "created_at_ms, updated_at_ms, last_activity_at_ms, stopped_at_ms, "
            "metadata_json FROM bcn_sessions WHERE id = ?",
            (session_id,),
        )
        return _bcn_session_from_row(row) if row is not None else None

    async def find_bcn_session(self, channel_session_id: str) -> BcnSession | None:
        row = await self._fetch_one_or_conflict(
            "SELECT id, channel_session_id, workspace_id, state, "
            "created_at_ms, updated_at_ms, last_activity_at_ms, stopped_at_ms, "
            "metadata_json FROM bcn_sessions "
            "WHERE channel_session_id = ? ORDER BY rowid",
            (channel_session_id,),
            "channel-to-bcn session binding",
        )
        return _bcn_session_from_row(row) if row is not None else None

    async def get_runtime_session(self, session_id: str) -> RuntimeSession | None:
        row = await self.fetchone(
            "SELECT runtime_sessions.id, "
            "runtime_sessions.bcn_session_id, runtime_sessions.channel_session_id, "
            "runtime_sessions.runtime, bcn_sessions.workspace_id AS workspace_id, "
            "runtime_sessions.process_state, runtime_sessions.provider_thread_id, "
            "runtime_sessions.process_pid, runtime_sessions.created_at_ms, "
            "runtime_sessions.updated_at_ms, runtime_sessions.started_at_ms, "
            "runtime_sessions.stopped_at_ms, runtime_sessions.last_reconciled_at_ms, "
            "runtime_sessions.last_error_kind, runtime_sessions.last_error_message, "
            "runtime_sessions.metadata_json "
            "FROM runtime_sessions LEFT JOIN bcn_sessions "
            "ON bcn_sessions.id = runtime_sessions.bcn_session_id "
            "WHERE runtime_sessions.id = ?",
            (session_id,),
        )
        return _runtime_session_from_row(row) if row is not None else None

    async def find_runtime_session(self, session_id: str) -> RuntimeSession | None:
        row = await self._fetch_one_or_conflict(
            "SELECT runtime_sessions.id, "
            "runtime_sessions.bcn_session_id, runtime_sessions.channel_session_id, "
            "runtime_sessions.runtime, bcn_sessions.workspace_id AS workspace_id, "
            "runtime_sessions.process_state, runtime_sessions.provider_thread_id, "
            "runtime_sessions.process_pid, runtime_sessions.created_at_ms, "
            "runtime_sessions.updated_at_ms, runtime_sessions.started_at_ms, "
            "runtime_sessions.stopped_at_ms, runtime_sessions.last_reconciled_at_ms, "
            "runtime_sessions.last_error_kind, runtime_sessions.last_error_message, "
            "runtime_sessions.metadata_json FROM runtime_sessions "
            "LEFT JOIN bcn_sessions ON bcn_sessions.id = "
            "runtime_sessions.bcn_session_id "
            "WHERE runtime_sessions.bcn_session_id = ? "
            "ORDER BY runtime_sessions.rowid",
            (session_id,),
            "bcn-to-runtime session binding",
        )
        return _runtime_session_from_row(row) if row is not None else None

    async def get_runtime_turn(self, turn_id: str) -> RuntimeTurn | None:
        row = await self.fetchone(
            "SELECT turn_id, session_id, provider_turn_id, "
            "client_user_message_id, state, started_at_ms, completed_at_ms, "
            "last_event_name, error_kind, error_message, metadata_json "
            "FROM runtime_turns WHERE turn_id = ?",
            (turn_id,),
        )
        return _runtime_turn_from_row(row) if row is not None else None

    async def get_consumer_cursor(self, session_id: str) -> ConsumerCursor | None:
        row = await self.fetchone(
            "SELECT session_id, delivered_through_seq, inbox_snapshot_seq, "
            "inbox_snapshot_source, inbox_snapshot_at_ms, last_check_at_ms, "
            "last_read_at_ms, updated_at_ms FROM consumer_cursors "
            "WHERE session_id = ?",
            (session_id,),
        )
        return _consumer_cursor_from_row(row) if row is not None else None

    async def get_latest_inbound_seq(self, session_id: str) -> int:
        row = await self.fetchone(
            "SELECT COALESCE(MAX(seq), 0) AS latest_seq FROM inbound_messages "
            "WHERE session_id = ?",
            (session_id,),
        )
        if row is None:
            raise RuntimeError("SQLite latest inbound sequence query returned no row")
        return _required_non_negative_int(row["latest_seq"], "latest_inbound_seq")

    async def inbound_message_exists(
        self, channel: str, provider_message_id: str
    ) -> bool:
        row = await self.fetchone(
            "SELECT 1 FROM inbound_messages WHERE channel = ? "
            "AND provider_message_id = ? LIMIT 1",
            (channel, provider_message_id),
        )
        return row is not None

    async def find_inbound_message(
        self, channel: str, provider_message_id: str
    ) -> InboundMessage | None:
        row = await self._fetch_one_or_conflict(
            "SELECT seq, message_id, session_id, channel_session_id, "
            "channel, provider_message_id, provider_time_ms, "
            "received_at_ms, sender_id, sender_display_name, message_type, "
            "canonical_target, target_kind, provider_thread_id, "
            "reply_to_provider_message_id, body, mentions_agent, "
            "notifies_runtime, provider_payload_ref, metadata_json "
            "FROM inbound_messages WHERE channel = ? "
            "AND provider_message_id = ? ORDER BY seq",
            (channel, provider_message_id),
            "provider inbound identity",
        )
        if row is None:
            return None
        return _inbound_message_from_row(
            row, await self._attachments(row["message_id"])
        )

    async def list_ready_attachment_paths(self) -> tuple[str, ...]:
        rows = await self.fetchall(
            "SELECT relative_path FROM inbound_attachments "
            "WHERE state = 'ready' ORDER BY attachment_id"
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

        predicates = ["session_id = ?"]
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
                "SELECT seq, message_id, session_id, channel_session_id, "
                "channel, provider_message_id, provider_time_ms, "
                "received_at_ms, sender_id, sender_display_name, message_type, "
                "canonical_target, target_kind, provider_thread_id, "
                "reply_to_provider_message_id, body, mentions_agent, notifies_runtime, provider_payload_ref, "
                "metadata_json FROM inbound_messages "
                f"WHERE {where_clause} ORDER BY seq LIMIT ?",
                (*parameters, limit),
            )
            messages = []
            for row in rows:
                messages.append(
                    _inbound_message_from_row(
                        row, await self._attachments(row["message_id"])
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
            "SELECT COUNT(*) AS message_count FROM inbound_messages "
            f"WHERE {where_clause}",
            parameters,
        )
        if count_row is None:
            raise RuntimeError("SQLite inbound history count query returned no row")
        message_count = _required_non_negative_int(
            count_row["message_count"], "message_count"
        )
        position_row = await self.fetchone(
            "SELECT COUNT(*) AS anchor_position FROM inbound_messages "
            f"WHERE {where_clause} AND seq <= ?",
            (*parameters, anchor_seq),
        )
        if position_row is None:
            raise RuntimeError("SQLite inbound anchor position query returned no row")
        anchor_position = _required_positive_int(
            position_row["anchor_position"], "anchor_position"
        )
        before_count = limit // 2
        start_position = max(anchor_position - before_count, 1)
        start_position = min(
            start_position,
            max(message_count - limit + 1, 1),
        )
        end_position = start_position + limit - 1

        filtered_query = (
            "SELECT seq, message_id, session_id, channel_session_id, "
            "channel, provider_message_id, provider_time_ms, "
            "received_at_ms, sender_id, sender_display_name, message_type, "
            "canonical_target, target_kind, provider_thread_id, "
            "reply_to_provider_message_id, body, mentions_agent, notifies_runtime, provider_payload_ref, "
            "metadata_json, ROW_NUMBER() OVER (ORDER BY seq) AS row_number "
            "FROM inbound_messages "
            f"WHERE {where_clause}"
        )
        rows = await self.fetchall(
            "WITH filtered AS ("
            + filtered_query
            + ") SELECT seq, message_id, session_id, channel_session_id, "
            "channel, provider_message_id, provider_time_ms, "
            "received_at_ms, sender_id, sender_display_name, message_type, "
            "canonical_target, target_kind, provider_thread_id, "
            "reply_to_provider_message_id, body, mentions_agent, notifies_runtime, provider_payload_ref, "
            "metadata_json FROM filtered WHERE row_number BETWEEN ? AND ? "
            "ORDER BY row_number",
            (*parameters, start_position, end_position),
        )
        messages = []
        for row in rows:
            messages.append(
                _inbound_message_from_row(
                    row, await self._attachments(row["message_id"])
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
        ):
            raise ValueError("inbound message binding does not match channel session")

        existing_row = await self._fetch_one_or_conflict(
            "SELECT seq, message_id, session_id, channel_session_id, "
            "channel, provider_message_id, provider_time_ms, "
            "received_at_ms, sender_id, sender_display_name, message_type, "
            "canonical_target, target_kind, provider_thread_id, "
            "reply_to_provider_message_id, body, mentions_agent, notifies_runtime, provider_payload_ref, "
            "metadata_json FROM inbound_messages "
            "WHERE channel = ? AND provider_message_id = ? ORDER BY seq",
            (message.channel, message.provider_message_id),
            "provider inbound identity",
        )
        if existing_row is not None:
            existing = _inbound_message_from_row(
                existing_row, await self._attachments(existing_row["message_id"])
            )
            if _same_inbound_payload(existing, message):
                return existing
            raise ValueError(
                "provider message id is already bound to different inbound content"
            )

        sequence_row = await self.fetchone(
            "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM inbound_messages"
        )
        if sequence_row is None:
            raise RuntimeError("SQLite inbound sequence query returned no row")
        next_seq = _required_positive_int(sequence_row["next_seq"], "next_seq")
        canonical = replace(message, seq=next_seq, message_id=str(uuid7()))
        await self.execute(
            "INSERT INTO inbound_messages ("
            "message_id, seq, session_id, channel_session_id, channel, "
            "provider_message_id, provider_time_ms, received_at_ms, sender_id, "
            "sender_display_name, message_type, canonical_target, target_kind, "
            "provider_thread_id, reply_to_provider_message_id, body, "
            "mentions_agent, notifies_runtime, provider_payload_ref, metadata_json"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                canonical.message_id,
                canonical.seq,
                canonical.session_id,
                canonical.channel_session_id,
                canonical.channel,
                canonical.provider_message_id,
                canonical.provider_time_ms,
                canonical.received_at_ms,
                canonical.sender_id,
                canonical.sender_display_name,
                canonical.message_type,
                canonical.canonical_target,
                canonical.target_kind.value,
                canonical.provider_thread_id,
                canonical.reply_to_provider_message_id,
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

    async def _attachments(self, message_id: str):
        rows = await self.fetchall(
            "SELECT attachment_id, name, kind, state, media_type, relative_path, "
            "size_bytes, error FROM inbound_attachments WHERE message_id = ? "
            "ORDER BY ordinal",
            (message_id,),
        )
        return tuple(_inbound_attachment_from_row(row) for row in rows)

    async def save_consumer_cursor(self, cursor: ConsumerCursor) -> None:
        _validate_consumer_cursor_input(cursor)
        if await self.get_bcn_session(cursor.session_id) is None:
            raise ValueError(f"unknown bcn session: {cursor.session_id}")
        latest_seq = await self.get_latest_inbound_seq(cursor.session_id)
        _validate_cursor_bounds(cursor, latest_seq)
        existing = await self.get_consumer_cursor(cursor.session_id)
        if existing is not None:
            _validate_consumer_cursor_update(existing, cursor)
            await self.execute(
                "UPDATE consumer_cursors SET delivered_through_seq = ?, "
                "inbox_snapshot_seq = ?, inbox_snapshot_source = ?, "
                "inbox_snapshot_at_ms = ?, last_check_at_ms = ?, "
                "last_read_at_ms = ?, updated_at_ms = ? WHERE session_id = ?",
                (
                    cursor.delivered_through_seq,
                    cursor.inbox_snapshot_seq,
                    cursor.inbox_snapshot_source,
                    cursor.inbox_snapshot_at_ms,
                    cursor.last_check_at_ms,
                    cursor.last_read_at_ms,
                    cursor.updated_at_ms,
                    cursor.session_id,
                ),
            )
            return
        await self.execute(
            "INSERT INTO consumer_cursors ("
            "session_id, delivered_through_seq, inbox_snapshot_seq, "
            "inbox_snapshot_source, inbox_snapshot_at_ms, last_check_at_ms, "
            "last_read_at_ms, updated_at_ms"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                cursor.session_id,
                cursor.delivered_through_seq,
                cursor.inbox_snapshot_seq,
                cursor.inbox_snapshot_source,
                cursor.inbox_snapshot_at_ms,
                cursor.last_check_at_ms,
                cursor.last_read_at_ms,
                cursor.updated_at_ms,
            ),
        )

    async def get_outbound_message(
        self, outbound_message_id: str
    ) -> OutboundMessage | None:
        row = await self.fetchone(
            "SELECT outbound_message_id, command_id, session_id, "
            "channel_session_id, target, body, state, fresh_check_state, "
            "snapshot_seq, current_inbound_seq, provider_message_id, "
            "provider_receipt_ref, created_at_ms, provider_attempted_at_ms, "
            "completed_at_ms, draft_saved_at_ms, error_kind, error_message, "
            "next_action, metadata_json FROM outbound_messages "
            "WHERE outbound_message_id = ?",
            (outbound_message_id,),
        )
        return _outbound_message_from_row(row) if row is not None else None

    async def save_outbound_message(self, message: OutboundMessage) -> OutboundMessage:
        _validate_outbound_message_input(message)
        bcn_session = await self.get_bcn_session(message.session_id)
        if bcn_session is None:
            raise ValueError(f"unknown bcn session: {message.session_id}")
        channel_session = await self.get_channel_session(message.channel_session_id)
        if channel_session is None:
            raise ValueError(f"unknown channel session: {message.channel_session_id}")
        if bcn_session.channel_session_id != message.channel_session_id:
            raise ValueError("outbound message binding does not match bcn session")

        existing = await self.get_outbound_message(message.outbound_message_id)
        if existing is None:
            canonical = replace(message, outbound_message_id=str(uuid7()))
            _validate_outbound_insert(canonical)
            await self.execute(
                "INSERT INTO outbound_messages ("
                "outbound_message_id, command_id, session_id, "
                "channel_session_id, target, body, state, fresh_check_state, "
                "snapshot_seq, current_inbound_seq, provider_message_id, "
                "provider_receipt_ref, created_at_ms, provider_attempted_at_ms, "
                "completed_at_ms, draft_saved_at_ms, error_kind, error_message, "
                "next_action, metadata_json"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    canonical.outbound_message_id,
                    canonical.command_id,
                    canonical.session_id,
                    canonical.channel_session_id,
                    canonical.target,
                    canonical.body,
                    canonical.state.value,
                    canonical.fresh_check_state.value,
                    canonical.snapshot_seq,
                    canonical.current_inbound_seq,
                    canonical.provider_message_id,
                    canonical.provider_receipt_ref,
                    canonical.created_at_ms,
                    canonical.provider_attempted_at_ms,
                    canonical.completed_at_ms,
                    canonical.draft_saved_at_ms,
                    canonical.error_kind,
                    canonical.error_message,
                    canonical.next_action,
                    _encode_metadata(canonical.metadata),
                ),
            )
            return canonical

        if (
            existing.command_id != message.command_id
            or existing.session_id != message.session_id
            or existing.channel_session_id != message.channel_session_id
            or existing.target != message.target
            or existing.body != message.body
            or existing.created_at_ms != message.created_at_ms
        ):
            raise ValueError("outbound message identity cannot change")
        canonical = _validate_outbound_update(existing, message)
        await self.execute(
            "UPDATE outbound_messages SET state = ?, fresh_check_state = ?, "
            "snapshot_seq = ?, current_inbound_seq = ?, provider_message_id = ?, "
            "provider_receipt_ref = ?, provider_attempted_at_ms = ?, "
            "completed_at_ms = ?, draft_saved_at_ms = ?, error_kind = ?, "
            "error_message = ?, next_action = ?, metadata_json = ? "
            "WHERE outbound_message_id = ?",
            (
                canonical.state.value,
                canonical.fresh_check_state.value,
                canonical.snapshot_seq,
                canonical.current_inbound_seq,
                canonical.provider_message_id,
                canonical.provider_receipt_ref,
                canonical.provider_attempted_at_ms,
                canonical.completed_at_ms,
                canonical.draft_saved_at_ms,
                canonical.error_kind,
                canonical.error_message,
                canonical.next_action,
                _encode_metadata(canonical.metadata),
                canonical.outbound_message_id,
            ),
        )
        return canonical

    async def append_runtime_event(self, event: RuntimeEvent) -> RuntimeEvent:
        _validate_runtime_event_input(event)
        existing_row = await self._fetch_one_or_conflict(
            "SELECT event_seq, event_id, created_at_ms, level, event_name, state, "
            "duration_ms, node_id, channel, runtime, "
            "channel_session_id, bcn_session_id, runtime_session_id, "
            "turn_id, request_id, command_id, inbound_seq, outbound_message_id, "
            "error_kind, error_type, error_message, traceback_ref, metadata_json "
            "FROM runtime_events WHERE event_id = ? ORDER BY event_seq",
            (event.event_id,),
            "runtime event identity",
        )
        if existing_row is not None:
            existing = _runtime_event_from_row(existing_row)
            if _same_runtime_event_payload(existing, event):
                return existing
            raise ValueError(
                "runtime event id is already bound to different event content"
            )

        await self._validate_runtime_event_references(event)
        sequence_row = await self.fetchone(
            "SELECT COALESCE(MAX(event_seq), 0) + 1 AS next_event_seq "
            "FROM runtime_events"
        )
        if sequence_row is None:
            raise RuntimeError("SQLite runtime event sequence query returned no row")
        next_event_seq = _required_positive_int(
            sequence_row["next_event_seq"], "next_event_seq"
        )
        canonical = replace(event, event_seq=next_event_seq)
        await self.execute(
            "INSERT INTO runtime_events ("
            "event_seq, event_id, created_at_ms, level, event_name, state, "
            "duration_ms, node_id, channel, runtime, "
            "channel_session_id, bcn_session_id, runtime_session_id, "
            "turn_id, request_id, command_id, inbound_seq, outbound_message_id, "
            "error_kind, error_type, error_message, traceback_ref, metadata_json"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                canonical.event_seq,
                canonical.event_id,
                canonical.created_at_ms,
                canonical.level,
                canonical.event_name,
                canonical.state.value,
                canonical.duration_ms,
                canonical.node_id,
                canonical.channel,
                canonical.runtime,
                canonical.channel_session_id,
                canonical.bcn_session_id,
                canonical.runtime_session_id,
                canonical.turn_id,
                canonical.request_id,
                canonical.command_id,
                canonical.inbound_seq,
                canonical.outbound_message_id,
                canonical.error_kind,
                canonical.error_type,
                canonical.error_message,
                canonical.traceback_ref,
                _encode_metadata(canonical.metadata),
            ),
        )
        return canonical

    async def _validate_runtime_event_references(self, event: RuntimeEvent) -> None:
        channel_session = None
        if event.channel_session_id is not None:
            channel_session = await self.get_channel_session(event.channel_session_id)
            if channel_session is None:
                raise ValueError(f"unknown channel session: {event.channel_session_id}")
            if event.channel is not None and event.channel != channel_session.channel:
                raise ValueError("runtime event channel binding does not match")

        bcn_session = None
        if event.bcn_session_id is not None:
            bcn_session = await self.get_bcn_session(event.bcn_session_id)
            if bcn_session is None:
                raise ValueError(f"unknown bcn session: {event.bcn_session_id}")
            if (
                event.channel_session_id is not None
                and bcn_session.channel_session_id != event.channel_session_id
            ):
                raise ValueError("runtime event bcn/channel binding does not match")
            if channel_session is None:
                channel_session = await self.get_channel_session(
                    bcn_session.channel_session_id
                )
            if (
                event.channel is not None
                and channel_session is not None
                and event.channel != channel_session.channel
            ):
                raise ValueError("runtime event channel binding does not match")

        runtime_session = None
        if event.runtime_session_id is not None:
            runtime_session = await self.get_runtime_session(event.runtime_session_id)
            if runtime_session is None:
                raise ValueError(f"unknown runtime session: {event.runtime_session_id}")
            if (
                event.bcn_session_id is not None
                and runtime_session.bcn_session_id != event.bcn_session_id
            ):
                raise ValueError("runtime event runtime/bcn binding does not match")
            if (
                event.channel_session_id is not None
                and runtime_session.channel_session_id != event.channel_session_id
            ):
                raise ValueError("runtime event runtime/channel binding does not match")
            if event.runtime is not None and runtime_session.runtime != event.runtime:
                raise ValueError("runtime event runtime name does not match")
            if event.channel is not None:
                runtime_channel = await self.get_channel_session(
                    runtime_session.channel_session_id
                )
                if (
                    runtime_channel is not None
                    and runtime_channel.channel != event.channel
                ):
                    raise ValueError("runtime event channel binding does not match")

        if event.turn_id is not None:
            turn = await self.get_runtime_turn(event.turn_id)
            if turn is None:
                raise ValueError(f"unknown runtime turn: {event.turn_id}")
            if (
                event.runtime_session_id is not None
                and turn.session_id != event.runtime_session_id
            ):
                raise ValueError("runtime event turn/runtime binding does not match")
            if runtime_session is None:
                runtime_session = await self.get_runtime_session(turn.session_id)
            if (
                event.bcn_session_id is not None
                and runtime_session is not None
                and runtime_session.bcn_session_id != event.bcn_session_id
            ):
                raise ValueError("runtime event turn/bcn binding does not match")
            if runtime_session is not None:
                if (
                    event.channel_session_id is not None
                    and runtime_session.channel_session_id != event.channel_session_id
                ):
                    raise ValueError(
                        "runtime event turn/channel binding does not match"
                    )
                if (
                    event.runtime is not None
                    and runtime_session.runtime != event.runtime
                ):
                    raise ValueError("runtime event turn/runtime name does not match")
                if event.channel is not None:
                    turn_channel = await self.get_channel_session(
                        runtime_session.channel_session_id
                    )
                    if (
                        turn_channel is not None
                        and turn_channel.channel != event.channel
                    ):
                        raise ValueError(
                            "runtime event turn/channel binding does not match"
                        )

        if event.outbound_message_id is not None:
            outbound = await self.get_outbound_message(event.outbound_message_id)
            if outbound is None:
                raise ValueError(
                    f"unknown outbound message: {event.outbound_message_id}"
                )
            if (
                event.bcn_session_id is not None
                and outbound.session_id != event.bcn_session_id
            ):
                raise ValueError("runtime event outbound/bcn binding does not match")
            if (
                event.channel_session_id is not None
                and outbound.channel_session_id != event.channel_session_id
            ):
                raise ValueError(
                    "runtime event outbound/channel binding does not match"
                )
            if event.channel is not None:
                outbound_channel = await self.get_channel_session(
                    outbound.channel_session_id
                )
                if (
                    outbound_channel is not None
                    and outbound_channel.channel != event.channel
                ):
                    raise ValueError(
                        "runtime event outbound/channel binding does not match"
                    )

        if event.inbound_seq is not None and event.bcn_session_id is not None:
            row = await self.fetchone(
                "SELECT 1 FROM inbound_messages WHERE session_id = ? AND seq = ?",
                (event.bcn_session_id, event.inbound_seq),
            )
            if row is None:
                raise ValueError(
                    f"unknown inbound sequence for bcn session: {event.inbound_seq}"
                )

    async def save_channel_session(self, session: ChannelSession) -> None:
        _validate_channel_session_input(session)
        existing = await self.get_channel_session(session.id)
        if existing is None:
            duplicate = await self.find_channel_session(
                channel=session.channel,
                provider_conversation_key=session.provider_conversation_key,
                provider_thread_key=session.provider_thread_key,
            )
            if duplicate is not None:
                raise ValueError(
                    f"channel provider identity is already bound to {duplicate.id}"
                )
            await self.execute(
                "INSERT INTO channel_sessions ("
                "id, channel, provider_conversation_key, "
                "provider_thread_key, target_kind, following, state, "
                "provider_identity_ref_json, created_at_ms, updated_at_ms, "
                "last_inbound_at_ms, last_outbound_at_ms"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session.id,
                    session.channel,
                    session.provider_conversation_key,
                    session.provider_thread_key,
                    session.target_kind.value,
                    int(session.following),
                    session.state.value,
                    _encode_metadata(session.metadata),
                    session.created_at_ms,
                    session.updated_at_ms,
                    session.last_inbound_at_ms,
                    session.last_outbound_at_ms,
                ),
            )
            return

        session = _validate_channel_session_update(existing, session)
        await self.execute(
            "UPDATE channel_sessions SET target_kind = ?, following = ?, state = ?, "
            "updated_at_ms = ?, last_inbound_at_ms = ?, last_outbound_at_ms = ?, "
            "provider_identity_ref_json = ? WHERE id = ?",
            (
                session.target_kind.value,
                int(session.following),
                session.state.value,
                session.updated_at_ms,
                session.last_inbound_at_ms,
                session.last_outbound_at_ms,
                _encode_metadata(session.metadata),
                session.id,
            ),
        )

    async def save_bcn_session(self, session: BcnSession) -> None:
        _validate_bcn_session_input(session)
        self._require_workspace(session.workspace_id)
        channel_session = await self.get_channel_session(session.channel_session_id)
        if channel_session is None:
            raise ValueError(f"unknown channel session: {session.channel_session_id}")

        existing = await self.get_bcn_session(session.id)
        if existing is None:
            duplicate = await self.find_bcn_session(session.channel_session_id)
            if duplicate is not None:
                raise ValueError(f"channel session is already bound to {duplicate.id}")
            await self.execute(
                "INSERT INTO bcn_sessions ("
                "id, channel_session_id, workspace_id, state, "
                "created_at_ms, updated_at_ms, last_activity_at_ms, stopped_at_ms, "
                "metadata_json"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session.id,
                    session.channel_session_id,
                    session.workspace_id,
                    session.state.value,
                    session.created_at_ms,
                    session.updated_at_ms,
                    session.last_activity_at_ms,
                    session.stopped_at_ms,
                    _encode_metadata(session.metadata),
                ),
            )
            return

        session = _validate_bcn_session_update(existing, session)
        await self.execute(
            "UPDATE bcn_sessions SET state = ?, updated_at_ms = ?, "
            "last_activity_at_ms = ?, stopped_at_ms = ?, metadata_json = ? "
            "WHERE id = ?",
            (
                session.state.value,
                session.updated_at_ms,
                session.last_activity_at_ms,
                session.stopped_at_ms,
                _encode_metadata(session.metadata),
                session.id,
            ),
        )

    async def save_runtime_session(self, session: RuntimeSession) -> None:
        _validate_runtime_session_input(session)
        self._require_workspace(session.workspace_id)
        bcn_session = await self.get_bcn_session(session.bcn_session_id)
        if bcn_session is None:
            raise ValueError(f"unknown bcn session: {session.bcn_session_id}")
        if await self.get_channel_session(bcn_session.channel_session_id) is None:
            raise ValueError(
                f"unknown channel session: {bcn_session.channel_session_id}"
            )
        if (
            bcn_session.channel_session_id != session.channel_session_id
            or bcn_session.workspace_id != session.workspace_id
        ):
            raise ValueError("runtime session binding does not match bcn session")

        existing = await self.get_runtime_session(session.id)
        if existing is None:
            duplicate = await self.find_runtime_session(session.bcn_session_id)
            if duplicate is not None:
                raise ValueError(f"bcn session is already bound to {duplicate.id}")
            await self.execute(
                "INSERT INTO runtime_sessions ("
                "id, bcn_session_id, channel_session_id, "
                "runtime, runtime_version, provider_thread_id, process_state, "
                "process_pid, last_exit_code, created_at_ms, updated_at_ms, "
                "started_at_ms, stopped_at_ms, last_reconciled_at_ms, "
                "last_error_kind, last_error_message, metadata_json"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session.id,
                    session.bcn_session_id,
                    session.channel_session_id,
                    session.runtime,
                    None,
                    session.provider_thread_id,
                    session.process_state.value,
                    session.process_id,
                    None,
                    session.created_at_ms,
                    session.updated_at_ms,
                    session.started_at_ms,
                    session.stopped_at_ms,
                    session.last_reconciled_at_ms,
                    session.last_error_kind,
                    session.last_error_message,
                    _encode_metadata(session.metadata),
                ),
            )
            return

        session = _validate_runtime_session_update(existing, session)
        await self.execute(
            "UPDATE runtime_sessions SET provider_thread_id = ?, "
            "process_state = ?, process_pid = ?, updated_at_ms = ?, "
            "started_at_ms = ?, stopped_at_ms = ?, last_reconciled_at_ms = ?, "
            "last_error_kind = ?, last_error_message = ?, metadata_json = ? "
            "WHERE id = ?",
            (
                session.provider_thread_id,
                session.process_state.value,
                session.process_id,
                session.updated_at_ms,
                session.started_at_ms,
                session.stopped_at_ms,
                session.last_reconciled_at_ms,
                session.last_error_kind,
                session.last_error_message,
                _encode_metadata(session.metadata),
                session.id,
            ),
        )

    async def save_runtime_turn(self, turn: RuntimeTurn) -> None:
        _validate_runtime_turn_input(turn)
        if await self.get_runtime_session(turn.session_id) is None:
            raise ValueError(f"unknown runtime session: {turn.session_id}")

        existing = await self.get_runtime_turn(turn.turn_id)
        if existing is None:
            if turn.state is not RuntimeTurnState.STARTING:
                raise ValueError("a new runtime turn must start in starting state")
            await self._validate_active_runtime_turn(turn)
            await self.execute(
                "INSERT INTO runtime_turns ("
                "turn_id, session_id, provider_turn_id, "
                "client_user_message_id, state, started_at_ms, completed_at_ms, "
                "last_event_name, error_kind, error_message, metadata_json"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    turn.turn_id,
                    turn.session_id,
                    turn.provider_turn_id,
                    turn.client_user_message_id,
                    turn.state.value,
                    turn.started_at_ms,
                    turn.completed_at_ms,
                    turn.latest_event_name,
                    turn.error_kind,
                    turn.error_message,
                    _encode_metadata(turn.metadata),
                ),
            )
            return

        if existing.session_id != turn.session_id:
            raise ValueError("runtime turn binding cannot change")
        if existing.started_at_ms != turn.started_at_ms:
            raise ValueError("runtime turn start time cannot change")
        canonical = _validate_runtime_turn_update(existing, turn)
        await self._validate_active_runtime_turn(canonical)
        await self.execute(
            "UPDATE runtime_turns SET provider_turn_id = ?, "
            "client_user_message_id = ?, state = ?, completed_at_ms = ?, "
            "last_event_name = ?, error_kind = ?, error_message = ?, "
            "metadata_json = ? WHERE turn_id = ?",
            (
                canonical.provider_turn_id,
                canonical.client_user_message_id,
                canonical.state.value,
                canonical.completed_at_ms,
                canonical.latest_event_name,
                canonical.error_kind,
                canonical.error_message,
                _encode_metadata(canonical.metadata),
                canonical.turn_id,
            ),
        )

    async def _validate_active_runtime_turn(self, turn: RuntimeTurn) -> None:
        active_states = tuple(
            state.value
            for state in (
                RuntimeTurnState.STARTING,
                RuntimeTurnState.RUNNING,
                RuntimeTurnState.UNKNOWN,
                RuntimeTurnState.RECONCILING,
            )
        )
        placeholders = ", ".join("?" for _ in active_states)
        rows = await self.fetchall(
            "SELECT turn_id FROM runtime_turns "
            "WHERE session_id = ? AND state IN ("
            + placeholders
            + ") AND turn_id <> ? LIMIT 1",
            (turn.session_id, *active_states, turn.turn_id),
        )
        if rows:
            raise ValueError(
                f"runtime session already has an active turn: {rows[0]['turn_id']}"
            )

    async def _fetch_one_or_conflict(
        self,
        statement: str,
        parameters: Sequence[object],
        binding_name: str,
    ) -> aiosqlite.Row | None:
        rows = await self.fetchall(statement, parameters)
        if len(rows) > 1:
            raise ValueError(f"multiple rows violate {binding_name}")
        return rows[0] if rows else None

    def _require_workspace(self, workspace_id: str) -> None:
        if workspace_id != self._database.workspace_id:
            raise ValueError(
                "session workspace does not match the persisted node workspace"
            )

    def _require_active_connection(self) -> aiosqlite.Connection:
        if not self._active or self._connection is None:
            raise RuntimeError("SQLite transaction is not active")
        return self._connection
