from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, replace
from pathlib import Path
from time import monotonic_ns, time_ns
from types import TracebackType
from typing import Self
from uuid import uuid7

import aiosqlite

from ...core.models import (
    BcnSession,
    BcnSessionState,
    ChannelSession,
    ChannelSessionState,
    ConsumerCursor,
    FreshCheckState,
    InboundMessage,
    OutboundDeliveryState,
    OutboundMessage,
    RuntimeEvent,
    RuntimeEventState,
    RuntimeProcessState,
    RuntimeSession,
    RuntimeTurn,
    RuntimeTurnState,
)
from ...core.paths import resolve_data_dir
from ...core.storage import NodeIdentity
from .migrations import MIGRATIONS

DATABASE_FILENAME = "bcn.sqlite3"
NODE_STATE_KEY = 1
DEFAULT_BUSY_TIMEOUT_MS = 5_000


class MigrationError(RuntimeError):
    """The database cannot be safely brought to the application schema."""


class MigrationChecksumError(MigrationError):
    """A migration ledger entry no longer matches the application migration."""


class NodeIdentityError(MigrationError):
    """The persistent node identity does not match the requested identity."""


@dataclass(frozen=True, slots=True)
class NodeState:
    node_id: str
    schema_version: int
    workspace_id: str
    created_at_ms: int
    updated_at_ms: int
    metadata_json: str


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
        channel_slug: str,
        provider_conversation_key: str,
        provider_thread_key: str,
    ) -> ChannelSession | None:
        row = await self._fetch_one_or_conflict(
            "SELECT channel_session_id, channel_slug, "
            "provider_conversation_key, provider_thread_key, following, state, "
            "created_at_ms, updated_at_ms, last_inbound_at_ms, last_outbound_at_ms, "
            "provider_identity_ref_json "
            "FROM channel_sessions "
            "WHERE channel_slug = ? AND provider_conversation_key = ? "
            "AND provider_thread_key = ? ORDER BY rowid",
            (channel_slug, provider_conversation_key, provider_thread_key),
            "channel provider identity",
        )
        return _channel_session_from_row(row) if row is not None else None

    async def get_channel_session(
        self, channel_session_id: str
    ) -> ChannelSession | None:
        row = await self.fetchone(
            "SELECT channel_session_id, channel_slug, "
            "provider_conversation_key, provider_thread_key, following, state, "
            "created_at_ms, updated_at_ms, last_inbound_at_ms, last_outbound_at_ms, "
            "provider_identity_ref_json "
            "FROM channel_sessions WHERE channel_session_id = ?",
            (channel_session_id,),
        )
        return _channel_session_from_row(row) if row is not None else None

    async def get_bcn_session(self, bcn_session_id: str) -> BcnSession | None:
        row = await self.fetchone(
            "SELECT bcn_session_id, channel_session_id, workspace_id, state, "
            "created_at_ms, updated_at_ms, last_activity_at_ms, stopped_at_ms, "
            "metadata_json FROM bcn_sessions WHERE bcn_session_id = ?",
            (bcn_session_id,),
        )
        return _bcn_session_from_row(row) if row is not None else None

    async def find_bcn_session(self, channel_session_id: str) -> BcnSession | None:
        row = await self._fetch_one_or_conflict(
            "SELECT bcn_session_id, channel_session_id, workspace_id, state, "
            "created_at_ms, updated_at_ms, last_activity_at_ms, stopped_at_ms, "
            "metadata_json FROM bcn_sessions "
            "WHERE channel_session_id = ? ORDER BY rowid",
            (channel_session_id,),
            "channel-to-bcn session binding",
        )
        return _bcn_session_from_row(row) if row is not None else None

    async def get_runtime_session(
        self, agent_runtime_session_id: str
    ) -> RuntimeSession | None:
        row = await self.fetchone(
            "SELECT runtime_sessions.agent_runtime_session_id, "
            "runtime_sessions.bcn_session_id, runtime_sessions.channel_session_id, "
            "runtime_sessions.runtime_slug, bcn_sessions.workspace_id AS workspace_id, "
            "runtime_sessions.process_state, runtime_sessions.provider_thread_id, "
            "runtime_sessions.process_pid, runtime_sessions.created_at_ms, "
            "runtime_sessions.updated_at_ms, runtime_sessions.started_at_ms, "
            "runtime_sessions.stopped_at_ms, runtime_sessions.last_reconciled_at_ms, "
            "runtime_sessions.last_error_kind, runtime_sessions.last_error_message, "
            "runtime_sessions.metadata_json "
            "FROM runtime_sessions LEFT JOIN bcn_sessions "
            "ON bcn_sessions.bcn_session_id = runtime_sessions.bcn_session_id "
            "WHERE runtime_sessions.agent_runtime_session_id = ?",
            (agent_runtime_session_id,),
        )
        return _runtime_session_from_row(row) if row is not None else None

    async def find_runtime_session(self, bcn_session_id: str) -> RuntimeSession | None:
        row = await self._fetch_one_or_conflict(
            "SELECT runtime_sessions.agent_runtime_session_id, "
            "runtime_sessions.bcn_session_id, runtime_sessions.channel_session_id, "
            "runtime_sessions.runtime_slug, bcn_sessions.workspace_id AS workspace_id, "
            "runtime_sessions.process_state, runtime_sessions.provider_thread_id, "
            "runtime_sessions.process_pid, runtime_sessions.created_at_ms, "
            "runtime_sessions.updated_at_ms, runtime_sessions.started_at_ms, "
            "runtime_sessions.stopped_at_ms, runtime_sessions.last_reconciled_at_ms, "
            "runtime_sessions.last_error_kind, runtime_sessions.last_error_message, "
            "runtime_sessions.metadata_json FROM runtime_sessions "
            "LEFT JOIN bcn_sessions ON bcn_sessions.bcn_session_id = "
            "runtime_sessions.bcn_session_id "
            "WHERE runtime_sessions.bcn_session_id = ? "
            "ORDER BY runtime_sessions.rowid",
            (bcn_session_id,),
            "bcn-to-runtime session binding",
        )
        return _runtime_session_from_row(row) if row is not None else None

    async def get_runtime_turn(self, turn_id: str) -> RuntimeTurn | None:
        row = await self.fetchone(
            "SELECT turn_id, agent_runtime_session_id, provider_turn_id, "
            "client_user_message_id, state, started_at_ms, completed_at_ms, "
            "last_event_name, error_kind, error_message, metadata_json "
            "FROM runtime_turns WHERE turn_id = ?",
            (turn_id,),
        )
        return _runtime_turn_from_row(row) if row is not None else None

    async def get_consumer_cursor(self, bcn_session_id: str) -> ConsumerCursor | None:
        row = await self.fetchone(
            "SELECT bcn_session_id, delivered_through_seq, inbox_snapshot_seq, "
            "inbox_snapshot_source, inbox_snapshot_at_ms, last_check_at_ms, "
            "last_read_at_ms, updated_at_ms FROM consumer_cursors "
            "WHERE bcn_session_id = ?",
            (bcn_session_id,),
        )
        return _consumer_cursor_from_row(row) if row is not None else None

    async def get_latest_inbound_seq(self, bcn_session_id: str) -> int:
        row = await self.fetchone(
            "SELECT COALESCE(MAX(seq), 0) AS latest_seq FROM inbound_messages "
            "WHERE bcn_session_id = ?",
            (bcn_session_id,),
        )
        if row is None:
            raise RuntimeError("SQLite latest inbound sequence query returned no row")
        return _required_non_negative_int(row["latest_seq"], "latest_inbound_seq")

    async def list_inbound_messages(
        self,
        bcn_session_id: str,
        *,
        after_seq: int | None = None,
        target: str | None = None,
        around_message_id: str | None = None,
        limit: int = 100,
    ) -> tuple[InboundMessage, ...]:
        _validate_non_empty_text(bcn_session_id, "bcn_session_id")
        if after_seq is not None:
            _validate_non_negative_int(after_seq, "after_seq")
        if target is not None:
            _validate_non_empty_text(target, "target")
        if around_message_id is not None:
            _validate_non_empty_text(around_message_id, "around_message_id")
        _validate_positive_int(limit, "limit")

        predicates = ["bcn_session_id = ?"]
        parameters: list[object] = [bcn_session_id]
        if after_seq is not None:
            predicates.append("seq > ?")
            parameters.append(after_seq)
        if target is not None:
            predicates.append("canonical_target = ?")
            parameters.append(target)
        where_clause = " AND ".join(predicates)

        if around_message_id is None:
            rows = await self.fetchall(
                "SELECT seq, message_id, bcn_session_id, channel_session_id, "
                "channel_slug, provider_message_id, provider_time_ms, "
                "received_at_ms, sender_id, sender_display_name, message_type, "
                "canonical_target, provider_thread_id, "
                "reply_to_provider_message_id, body, provider_payload_ref, "
                "metadata_json FROM inbound_messages "
                f"WHERE {where_clause} ORDER BY seq LIMIT ?",
                (*parameters, limit),
            )
            return tuple(_inbound_message_from_row(row) for row in rows)

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
            "SELECT seq, message_id, bcn_session_id, channel_session_id, "
            "channel_slug, provider_message_id, provider_time_ms, "
            "received_at_ms, sender_id, sender_display_name, message_type, "
            "canonical_target, provider_thread_id, "
            "reply_to_provider_message_id, body, provider_payload_ref, "
            "metadata_json, ROW_NUMBER() OVER (ORDER BY seq) AS row_number "
            "FROM inbound_messages "
            f"WHERE {where_clause}"
        )
        rows = await self.fetchall(
            "WITH filtered AS ("
            + filtered_query
            + ") SELECT seq, message_id, bcn_session_id, channel_session_id, "
            "channel_slug, provider_message_id, provider_time_ms, "
            "received_at_ms, sender_id, sender_display_name, message_type, "
            "canonical_target, provider_thread_id, "
            "reply_to_provider_message_id, body, provider_payload_ref, "
            "metadata_json FROM filtered WHERE row_number BETWEEN ? AND ? "
            "ORDER BY row_number",
            (*parameters, start_position, end_position),
        )
        return tuple(_inbound_message_from_row(row) for row in rows)

    async def append_inbound_message(self, message: InboundMessage) -> InboundMessage:
        _validate_inbound_message_input(message)
        bcn_session = await self.get_bcn_session(message.bcn_session_id)
        if bcn_session is None:
            raise ValueError(f"unknown bcn session: {message.bcn_session_id}")
        channel_session = await self.get_channel_session(bcn_session.channel_session_id)
        if channel_session is None:
            raise ValueError(
                f"unknown channel session: {bcn_session.channel_session_id}"
            )
        if (
            message.channel_session_id != channel_session.channel_session_id
            or message.channel_slug != channel_session.channel_slug
        ):
            raise ValueError("inbound message binding does not match channel session")

        existing_row = await self._fetch_one_or_conflict(
            "SELECT seq, message_id, bcn_session_id, channel_session_id, "
            "channel_slug, provider_message_id, provider_time_ms, "
            "received_at_ms, sender_id, sender_display_name, message_type, "
            "canonical_target, provider_thread_id, "
            "reply_to_provider_message_id, body, provider_payload_ref, "
            "metadata_json FROM inbound_messages "
            "WHERE channel_slug = ? AND provider_message_id = ? ORDER BY seq",
            (message.channel_slug, message.provider_message_id),
            "provider inbound identity",
        )
        if existing_row is not None:
            existing = _inbound_message_from_row(existing_row)
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
            "message_id, seq, bcn_session_id, channel_session_id, channel_slug, "
            "provider_message_id, provider_time_ms, received_at_ms, sender_id, "
            "sender_display_name, message_type, canonical_target, "
            "provider_thread_id, reply_to_provider_message_id, body, "
            "provider_payload_ref, metadata_json"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                canonical.message_id,
                canonical.seq,
                canonical.bcn_session_id,
                canonical.channel_session_id,
                canonical.channel_slug,
                canonical.provider_message_id,
                canonical.provider_time_ms,
                canonical.received_at_ms,
                canonical.sender_id,
                canonical.sender_display_name,
                canonical.message_type,
                canonical.canonical_target,
                canonical.provider_thread_id,
                canonical.reply_to_provider_message_id,
                canonical.body,
                canonical.provider_payload_ref,
                _encode_metadata(canonical.metadata),
            ),
        )
        return canonical

    async def save_consumer_cursor(self, cursor: ConsumerCursor) -> None:
        _validate_consumer_cursor_input(cursor)
        if await self.get_bcn_session(cursor.bcn_session_id) is None:
            raise ValueError(f"unknown bcn session: {cursor.bcn_session_id}")
        latest_seq = await self.get_latest_inbound_seq(cursor.bcn_session_id)
        _validate_cursor_bounds(cursor, latest_seq)
        existing = await self.get_consumer_cursor(cursor.bcn_session_id)
        if existing is not None:
            _validate_consumer_cursor_update(existing, cursor)
            await self.execute(
                "UPDATE consumer_cursors SET delivered_through_seq = ?, "
                "inbox_snapshot_seq = ?, inbox_snapshot_source = ?, "
                "inbox_snapshot_at_ms = ?, last_check_at_ms = ?, "
                "last_read_at_ms = ?, updated_at_ms = ? WHERE bcn_session_id = ?",
                (
                    cursor.delivered_through_seq,
                    cursor.inbox_snapshot_seq,
                    cursor.inbox_snapshot_source,
                    cursor.inbox_snapshot_at_ms,
                    cursor.last_check_at_ms,
                    cursor.last_read_at_ms,
                    cursor.updated_at_ms,
                    cursor.bcn_session_id,
                ),
            )
            return
        await self.execute(
            "INSERT INTO consumer_cursors ("
            "bcn_session_id, delivered_through_seq, inbox_snapshot_seq, "
            "inbox_snapshot_source, inbox_snapshot_at_ms, last_check_at_ms, "
            "last_read_at_ms, updated_at_ms"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                cursor.bcn_session_id,
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
            "SELECT outbound_message_id, command_id, bcn_session_id, "
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
        bcn_session = await self.get_bcn_session(message.bcn_session_id)
        if bcn_session is None:
            raise ValueError(f"unknown bcn session: {message.bcn_session_id}")
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
                "outbound_message_id, command_id, bcn_session_id, "
                "channel_session_id, target, body, state, fresh_check_state, "
                "snapshot_seq, current_inbound_seq, provider_message_id, "
                "provider_receipt_ref, created_at_ms, provider_attempted_at_ms, "
                "completed_at_ms, draft_saved_at_ms, error_kind, error_message, "
                "next_action, metadata_json"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    canonical.outbound_message_id,
                    canonical.command_id,
                    canonical.bcn_session_id,
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
            or existing.bcn_session_id != message.bcn_session_id
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
            "duration_ms, node_id, channel_slug, runtime_slug, "
            "channel_session_id, bcn_session_id, agent_runtime_session_id, "
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
            "duration_ms, node_id, channel_slug, runtime_slug, "
            "channel_session_id, bcn_session_id, agent_runtime_session_id, "
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
                canonical.channel_slug,
                canonical.runtime_slug,
                canonical.channel_session_id,
                canonical.bcn_session_id,
                canonical.agent_runtime_session_id,
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
            if (
                event.channel_slug is not None
                and event.channel_slug != channel_session.channel_slug
            ):
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
                event.channel_slug is not None
                and channel_session is not None
                and event.channel_slug != channel_session.channel_slug
            ):
                raise ValueError("runtime event channel binding does not match")

        runtime_session = None
        if event.agent_runtime_session_id is not None:
            runtime_session = await self.get_runtime_session(
                event.agent_runtime_session_id
            )
            if runtime_session is None:
                raise ValueError(
                    f"unknown runtime session: {event.agent_runtime_session_id}"
                )
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
            if (
                event.runtime_slug is not None
                and runtime_session.runtime_slug != event.runtime_slug
            ):
                raise ValueError("runtime event runtime slug does not match")
            if event.channel_slug is not None:
                runtime_channel = await self.get_channel_session(
                    runtime_session.channel_session_id
                )
                if (
                    runtime_channel is not None
                    and runtime_channel.channel_slug != event.channel_slug
                ):
                    raise ValueError("runtime event channel binding does not match")

        if event.turn_id is not None:
            turn = await self.get_runtime_turn(event.turn_id)
            if turn is None:
                raise ValueError(f"unknown runtime turn: {event.turn_id}")
            if (
                event.agent_runtime_session_id is not None
                and turn.agent_runtime_session_id != event.agent_runtime_session_id
            ):
                raise ValueError("runtime event turn/runtime binding does not match")
            if runtime_session is None:
                runtime_session = await self.get_runtime_session(
                    turn.agent_runtime_session_id
                )
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
                    event.runtime_slug is not None
                    and runtime_session.runtime_slug != event.runtime_slug
                ):
                    raise ValueError("runtime event turn/runtime slug does not match")
                if event.channel_slug is not None:
                    turn_channel = await self.get_channel_session(
                        runtime_session.channel_session_id
                    )
                    if (
                        turn_channel is not None
                        and turn_channel.channel_slug != event.channel_slug
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
                and outbound.bcn_session_id != event.bcn_session_id
            ):
                raise ValueError("runtime event outbound/bcn binding does not match")
            if (
                event.channel_session_id is not None
                and outbound.channel_session_id != event.channel_session_id
            ):
                raise ValueError(
                    "runtime event outbound/channel binding does not match"
                )
            if event.channel_slug is not None:
                outbound_channel = await self.get_channel_session(
                    outbound.channel_session_id
                )
                if (
                    outbound_channel is not None
                    and outbound_channel.channel_slug != event.channel_slug
                ):
                    raise ValueError(
                        "runtime event outbound/channel binding does not match"
                    )

        if event.inbound_seq is not None and event.bcn_session_id is not None:
            row = await self.fetchone(
                "SELECT 1 FROM inbound_messages WHERE bcn_session_id = ? AND seq = ?",
                (event.bcn_session_id, event.inbound_seq),
            )
            if row is None:
                raise ValueError(
                    f"unknown inbound sequence for bcn session: {event.inbound_seq}"
                )

    async def save_channel_session(self, session: ChannelSession) -> None:
        _validate_channel_session_input(session)
        existing = await self.get_channel_session(session.channel_session_id)
        if existing is None:
            duplicate = await self.find_channel_session(
                channel_slug=session.channel_slug,
                provider_conversation_key=session.provider_conversation_key,
                provider_thread_key=session.provider_thread_key,
            )
            if duplicate is not None:
                raise ValueError(
                    "channel provider identity is already bound to "
                    f"{duplicate.channel_session_id}"
                )
            await self.execute(
                "INSERT INTO channel_sessions ("
                "channel_session_id, channel_slug, provider_conversation_key, "
                "provider_thread_key, target_kind, following, state, "
                "provider_identity_ref_json, created_at_ms, updated_at_ms, "
                "last_inbound_at_ms, last_outbound_at_ms"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session.channel_session_id,
                    session.channel_slug,
                    session.provider_conversation_key,
                    session.provider_thread_key,
                    None,
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
            "UPDATE channel_sessions SET following = ?, state = ?, "
            "updated_at_ms = ?, last_inbound_at_ms = ?, last_outbound_at_ms = ?, "
            "provider_identity_ref_json = ? WHERE channel_session_id = ?",
            (
                int(session.following),
                session.state.value,
                session.updated_at_ms,
                session.last_inbound_at_ms,
                session.last_outbound_at_ms,
                _encode_metadata(session.metadata),
                session.channel_session_id,
            ),
        )

    async def save_bcn_session(self, session: BcnSession) -> None:
        _validate_bcn_session_input(session)
        self._require_workspace(session.workspace_id)
        channel_session = await self.get_channel_session(session.channel_session_id)
        if channel_session is None:
            raise ValueError(f"unknown channel session: {session.channel_session_id}")

        existing = await self.get_bcn_session(session.bcn_session_id)
        if existing is None:
            duplicate = await self.find_bcn_session(session.channel_session_id)
            if duplicate is not None:
                raise ValueError(
                    f"channel session is already bound to {duplicate.bcn_session_id}"
                )
            await self.execute(
                "INSERT INTO bcn_sessions ("
                "bcn_session_id, channel_session_id, workspace_id, state, "
                "created_at_ms, updated_at_ms, last_activity_at_ms, stopped_at_ms, "
                "metadata_json"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session.bcn_session_id,
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
            "WHERE bcn_session_id = ?",
            (
                session.state.value,
                session.updated_at_ms,
                session.last_activity_at_ms,
                session.stopped_at_ms,
                _encode_metadata(session.metadata),
                session.bcn_session_id,
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

        existing = await self.get_runtime_session(session.agent_runtime_session_id)
        if existing is None:
            duplicate = await self.find_runtime_session(session.bcn_session_id)
            if duplicate is not None:
                raise ValueError(
                    "bcn session is already bound to "
                    f"{duplicate.agent_runtime_session_id}"
                )
            await self.execute(
                "INSERT INTO runtime_sessions ("
                "agent_runtime_session_id, bcn_session_id, channel_session_id, "
                "runtime_slug, runtime_version, provider_thread_id, process_state, "
                "process_pid, last_exit_code, created_at_ms, updated_at_ms, "
                "started_at_ms, stopped_at_ms, last_reconciled_at_ms, "
                "last_error_kind, last_error_message, metadata_json"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session.agent_runtime_session_id,
                    session.bcn_session_id,
                    session.channel_session_id,
                    session.runtime_slug,
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
            "WHERE agent_runtime_session_id = ?",
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
                session.agent_runtime_session_id,
            ),
        )

    async def save_runtime_turn(self, turn: RuntimeTurn) -> None:
        _validate_runtime_turn_input(turn)
        if await self.get_runtime_session(turn.agent_runtime_session_id) is None:
            raise ValueError(
                f"unknown runtime session: {turn.agent_runtime_session_id}"
            )

        existing = await self.get_runtime_turn(turn.turn_id)
        if existing is None:
            if turn.state is not RuntimeTurnState.STARTING:
                raise ValueError("a new runtime turn must start in starting state")
            await self._validate_active_runtime_turn(turn)
            await self.execute(
                "INSERT INTO runtime_turns ("
                "turn_id, agent_runtime_session_id, provider_turn_id, "
                "client_user_message_id, state, started_at_ms, completed_at_ms, "
                "last_event_name, error_kind, error_message, metadata_json"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    turn.turn_id,
                    turn.agent_runtime_session_id,
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

        if existing.agent_runtime_session_id != turn.agent_runtime_session_id:
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
            "WHERE agent_runtime_session_id = ? AND state IN ("
            + placeholders
            + ") AND turn_id <> ? LIMIT 1",
            (turn.agent_runtime_session_id, *active_states, turn.turn_id),
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


class SqliteDatabase:
    """Persistent SQLite foundation used by the storage repository adapter."""

    def __init__(
        self,
        data_dir: Path | str | None = None,
        *,
        busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    ) -> None:
        if (
            isinstance(busy_timeout_ms, bool)
            or not isinstance(busy_timeout_ms, int)
            or busy_timeout_ms <= 0
        ):
            raise ValueError("busy_timeout_ms must be a positive integer")
        self.data_dir = resolve_data_dir(data_dir)
        self.database_path = self.data_dir / DATABASE_FILENAME
        self._busy_timeout_ms = busy_timeout_ms
        self._connection: aiosqlite.Connection | None = None
        self._node_state: NodeState | None = None
        self._schema_version: int | None = None
        self._lifecycle_lock = asyncio.Lock()
        self._transaction_lock = asyncio.Lock()

    @property
    def node_state(self) -> NodeState:
        if self._node_state is None:
            raise RuntimeError("SQLite node identity has not been initialized")
        return self._node_state

    @property
    def node_id(self) -> str:
        return self.node_state.node_id

    @property
    def workspace_id(self) -> str:
        return self.node_state.workspace_id

    @property
    def is_started(self) -> bool:
        return self._connection is not None

    async def start(self, *, timeout: float) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        async with self._lifecycle_lock:
            if self._connection is not None:
                return
            connection: aiosqlite.Connection | None = None
            try:
                async with asyncio.timeout(timeout):
                    self.data_dir.mkdir(
                        parents=True,
                        exist_ok=True,
                        mode=0o700,
                    )
                    _restrict_permissions(self.data_dir, 0o700)
                    connection = await aiosqlite.connect(
                        self.database_path,
                        timeout=self._busy_timeout_ms / 1000,
                        isolation_level=None,
                    )
                    connection.row_factory = aiosqlite.Row
                    await connection.execute("PRAGMA journal_mode = WAL")
                    await connection.execute("PRAGMA synchronous = NORMAL")
                    await connection.execute("PRAGMA foreign_keys = ON")
                    await connection.execute(
                        f"PRAGMA busy_timeout = {self._busy_timeout_ms}"
                    )
                    _restrict_permissions(self.database_path, 0o600)
                    self._connection = connection
                    self._schema_version = await self._bootstrap()
            except BaseException:
                self._connection = None
                self._node_state = None
                self._schema_version = None
                if connection is not None:
                    await connection.close()
                raise

    async def stop(self, *, timeout: float) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        async with self._lifecycle_lock:
            connection = self._connection
            if connection is None:
                return
            try:
                async with asyncio.timeout(timeout):
                    async with self._transaction_lock:
                        await connection.close()
            finally:
                self._connection = None
                self._node_state = None
                self._schema_version = None

    async def initialize(
        self,
        *,
        node_id: str | None = None,
        workspace_id: str | None = None,
    ) -> NodeIdentity:
        if node_id is not None and (not isinstance(node_id, str) or not node_id):
            raise ValueError("node_id must be a non-empty string")
        if workspace_id is not None and (
            not isinstance(workspace_id, str) or not workspace_id
        ):
            raise ValueError("workspace_id must be a non-empty string")
        async with self._lifecycle_lock:
            self._require_connection()
            schema_version = self._schema_version
            if schema_version is None:
                raise RuntimeError("SQLite schema has not been initialized")
            state: NodeState | None = None
            async with SqliteTransaction(self) as transaction:
                state = await self._ensure_node_state(
                    transaction,
                    schema_version,
                    requested_node_id=node_id,
                    requested_workspace_id=workspace_id,
                )
            if state is None:
                raise RuntimeError("SQLite node initialization did not create state")
            self._node_state = state
            return NodeIdentity(
                node_id=state.node_id,
                workspace_id=state.workspace_id,
            )

    def transaction(self) -> AbstractAsyncContextManager[SqliteTransaction]:
        return SqliteTransaction(self)

    def _require_connection(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise RuntimeError("SQLite database has not been started")
        return self._connection

    async def _bootstrap(self) -> int:
        schema_version: int | None = None
        async with SqliteTransaction(self) as transaction:
            schema_version = await self._apply_migrations(transaction)
        if schema_version is None:
            raise RuntimeError("SQLite bootstrap did not produce a schema version")
        return schema_version

    async def _apply_migrations(self, transaction: SqliteTransaction) -> int:
        ledger_exists = (
            await transaction.fetchone(
                "SELECT 1 FROM sqlite_master "
                "WHERE type = 'table' AND name = 'schema_migrations'"
            )
            is not None
        )
        applied_rows: list[aiosqlite.Row] = []
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
                    _current_time_ms(),
                    (monotonic_ns() - started_at_ns) // 1_000_000,
                ),
            )
            latest_version = migration.version

        return latest_version

    async def _ensure_node_state(
        self,
        transaction: SqliteTransaction,
        schema_version: int,
        *,
        requested_node_id: str | None,
        requested_workspace_id: str | None,
    ) -> NodeState:
        row = await transaction.fetchone(
            "SELECT node_id, schema_version, workspace_id, created_at_ms, "
            "updated_at_ms, metadata_json FROM node_state "
            "WHERE singleton_key = ?",
            (NODE_STATE_KEY,),
        )
        now_ms = _current_time_ms()
        if row is None:
            node_id = requested_node_id or f"bcn-node-{uuid7()}"
            workspace_id = requested_workspace_id or str(uuid7())
            metadata_json = "{}"
            await transaction.execute(
                "INSERT INTO node_state "
                "(singleton_key, node_id, schema_version, workspace_id, "
                "created_at_ms, updated_at_ms, metadata_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    NODE_STATE_KEY,
                    node_id,
                    schema_version,
                    workspace_id,
                    now_ms,
                    now_ms,
                    metadata_json,
                ),
            )
            return NodeState(
                node_id=node_id,
                schema_version=schema_version,
                workspace_id=workspace_id,
                created_at_ms=now_ms,
                updated_at_ms=now_ms,
                metadata_json=metadata_json,
            )

        node_id = row["node_id"]
        workspace_id = row["workspace_id"]
        if not isinstance(node_id, str) or not node_id:
            raise NodeIdentityError("persistent node_id is missing")
        if not isinstance(workspace_id, str) or not workspace_id:
            raise NodeIdentityError("persistent workspace_id is missing")
        if requested_node_id is not None and node_id != requested_node_id:
            raise NodeIdentityError(
                f"requested node_id does not match persisted node_id: {node_id}"
            )
        if (
            requested_workspace_id is not None
            and workspace_id != requested_workspace_id
        ):
            raise NodeIdentityError(
                "requested workspace_id does not match the persisted workspace_id"
            )
        if row["schema_version"] != schema_version:
            await transaction.execute(
                "UPDATE node_state SET schema_version = ?, updated_at_ms = ? "
                "WHERE singleton_key = ?",
                (schema_version, now_ms, NODE_STATE_KEY),
            )
            updated_at_ms = now_ms
        else:
            updated_at_ms = int(row["updated_at_ms"])
        return NodeState(
            node_id=node_id,
            schema_version=schema_version,
            workspace_id=workspace_id,
            created_at_ms=int(row["created_at_ms"]),
            updated_at_ms=updated_at_ms,
            metadata_json=row["metadata_json"] or "{}",
        )


def _channel_session_from_row(row: aiosqlite.Row) -> ChannelSession:
    following = row["following"]
    if (
        isinstance(following, bool)
        or not isinstance(following, int)
        or following not in (0, 1)
    ):
        raise ValueError("channel session following value is invalid")
    return ChannelSession(
        channel_session_id=_required_text(
            row["channel_session_id"], "channel_session_id"
        ),
        channel_slug=_required_text(row["channel_slug"], "channel_slug"),
        provider_conversation_key=_required_text(
            row["provider_conversation_key"], "provider_conversation_key"
        ),
        provider_thread_key=_string_value(
            row["provider_thread_key"], "provider_thread_key", allow_empty=True
        ),
        state=ChannelSessionState(
            _required_text(row["state"], "channel_session.state")
        ),
        created_at_ms=_required_non_negative_int(row["created_at_ms"], "created_at_ms"),
        updated_at_ms=_required_non_negative_int(row["updated_at_ms"], "updated_at_ms"),
        following=bool(following),
        last_inbound_at_ms=_optional_non_negative_int(
            row["last_inbound_at_ms"], "last_inbound_at_ms"
        ),
        last_outbound_at_ms=_optional_non_negative_int(
            row["last_outbound_at_ms"], "last_outbound_at_ms"
        ),
        metadata=_decode_metadata(
            row["provider_identity_ref_json"], "provider_identity_ref_json"
        ),
    )


def _bcn_session_from_row(row: aiosqlite.Row) -> BcnSession:
    return BcnSession(
        bcn_session_id=_required_text(row["bcn_session_id"], "bcn_session_id"),
        channel_session_id=_required_text(
            row["channel_session_id"], "channel_session_id"
        ),
        workspace_id=_required_text(row["workspace_id"], "workspace_id"),
        state=BcnSessionState(_required_text(row["state"], "bcn_session.state")),
        created_at_ms=_required_non_negative_int(row["created_at_ms"], "created_at_ms"),
        updated_at_ms=_required_non_negative_int(row["updated_at_ms"], "updated_at_ms"),
        last_activity_at_ms=_optional_non_negative_int(
            row["last_activity_at_ms"], "last_activity_at_ms"
        ),
        stopped_at_ms=_optional_non_negative_int(row["stopped_at_ms"], "stopped_at_ms"),
        metadata=_decode_metadata(row["metadata_json"], "metadata_json"),
    )


def _runtime_session_from_row(row: aiosqlite.Row) -> RuntimeSession:
    return RuntimeSession(
        agent_runtime_session_id=_required_text(
            row["agent_runtime_session_id"], "agent_runtime_session_id"
        ),
        bcn_session_id=_required_text(row["bcn_session_id"], "bcn_session_id"),
        channel_session_id=_required_text(
            row["channel_session_id"], "channel_session_id"
        ),
        runtime_slug=_required_text(row["runtime_slug"], "runtime_slug"),
        workspace_id=_required_text(row["workspace_id"], "workspace_id"),
        process_state=RuntimeProcessState(
            _required_text(row["process_state"], "runtime_session.process_state")
        ),
        created_at_ms=_required_non_negative_int(row["created_at_ms"], "created_at_ms"),
        updated_at_ms=_required_non_negative_int(row["updated_at_ms"], "updated_at_ms"),
        provider_thread_id=_optional_text(
            row["provider_thread_id"], "provider_thread_id"
        ),
        process_id=_optional_non_negative_int(row["process_pid"], "process_pid"),
        started_at_ms=_optional_non_negative_int(row["started_at_ms"], "started_at_ms"),
        stopped_at_ms=_optional_non_negative_int(row["stopped_at_ms"], "stopped_at_ms"),
        last_reconciled_at_ms=_optional_non_negative_int(
            row["last_reconciled_at_ms"], "last_reconciled_at_ms"
        ),
        last_error_kind=_optional_text(row["last_error_kind"], "last_error_kind"),
        last_error_message=_optional_text(
            row["last_error_message"], "last_error_message"
        ),
        metadata=_decode_metadata(row["metadata_json"], "metadata_json"),
    )


def _runtime_turn_from_row(row: aiosqlite.Row) -> RuntimeTurn:
    return RuntimeTurn(
        turn_id=_required_text(row["turn_id"], "turn_id"),
        agent_runtime_session_id=_required_text(
            row["agent_runtime_session_id"], "agent_runtime_session_id"
        ),
        state=RuntimeTurnState(_required_text(row["state"], "runtime_turn.state")),
        started_at_ms=_required_non_negative_int(row["started_at_ms"], "started_at_ms"),
        provider_turn_id=_optional_text(row["provider_turn_id"], "provider_turn_id"),
        client_user_message_id=_optional_text(
            row["client_user_message_id"], "client_user_message_id"
        ),
        completed_at_ms=_optional_non_negative_int(
            row["completed_at_ms"], "completed_at_ms"
        ),
        latest_event_name=_optional_text(row["last_event_name"], "last_event_name"),
        error_kind=_optional_text(row["error_kind"], "error_kind"),
        error_message=_optional_text(row["error_message"], "error_message"),
        metadata=_decode_metadata(row["metadata_json"], "metadata_json"),
    )


def _inbound_message_from_row(row: aiosqlite.Row) -> InboundMessage:
    return InboundMessage(
        seq=_required_non_negative_int(row["seq"], "seq"),
        message_id=_required_text(row["message_id"], "message_id"),
        bcn_session_id=_required_text(row["bcn_session_id"], "bcn_session_id"),
        channel_session_id=_required_text(
            row["channel_session_id"], "channel_session_id"
        ),
        channel_slug=_required_text(row["channel_slug"], "channel_slug"),
        provider_message_id=_required_text(
            row["provider_message_id"], "provider_message_id"
        ),
        received_at_ms=_required_non_negative_int(
            row["received_at_ms"], "received_at_ms"
        ),
        sender_id=_required_text(row["sender_id"], "sender_id"),
        sender_display_name=_required_text(
            row["sender_display_name"], "sender_display_name"
        ),
        message_type=_required_text(row["message_type"], "message_type"),
        canonical_target=_required_text(row["canonical_target"], "canonical_target"),
        body=_string_value(row["body"], "body", allow_empty=True),
        provider_time_ms=_optional_non_negative_int(
            row["provider_time_ms"], "provider_time_ms"
        ),
        provider_thread_id=_optional_string_value(
            row["provider_thread_id"], "provider_thread_id", allow_empty=True
        ),
        reply_to_provider_message_id=_optional_string_value(
            row["reply_to_provider_message_id"],
            "reply_to_provider_message_id",
            allow_empty=True,
        ),
        provider_payload_ref=_optional_string_value(
            row["provider_payload_ref"], "provider_payload_ref", allow_empty=False
        ),
        metadata=_decode_metadata(row["metadata_json"], "metadata_json"),
    )


def _outbound_message_from_row(row: aiosqlite.Row) -> OutboundMessage:
    return OutboundMessage(
        outbound_message_id=_required_text(
            row["outbound_message_id"], "outbound_message_id"
        ),
        command_id=_required_text(row["command_id"], "command_id"),
        bcn_session_id=_required_text(row["bcn_session_id"], "bcn_session_id"),
        channel_session_id=_required_text(
            row["channel_session_id"], "channel_session_id"
        ),
        target=_required_text(row["target"], "target"),
        body=_string_value(row["body"], "body", allow_empty=True),
        state=OutboundDeliveryState(
            _required_text(row["state"], "outbound_message.state")
        ),
        fresh_check_state=FreshCheckState(
            _required_text(
                row["fresh_check_state"], "outbound_message.fresh_check_state"
            )
        ),
        created_at_ms=_required_non_negative_int(row["created_at_ms"], "created_at_ms"),
        snapshot_seq=_optional_non_negative_int(row["snapshot_seq"], "snapshot_seq"),
        current_inbound_seq=_optional_non_negative_int(
            row["current_inbound_seq"], "current_inbound_seq"
        ),
        provider_message_id=_optional_text(
            row["provider_message_id"], "provider_message_id"
        ),
        provider_receipt_ref=_optional_text(
            row["provider_receipt_ref"], "provider_receipt_ref"
        ),
        provider_attempted_at_ms=_optional_non_negative_int(
            row["provider_attempted_at_ms"], "provider_attempted_at_ms"
        ),
        completed_at_ms=_optional_non_negative_int(
            row["completed_at_ms"], "completed_at_ms"
        ),
        draft_saved_at_ms=_optional_non_negative_int(
            row["draft_saved_at_ms"], "draft_saved_at_ms"
        ),
        error_kind=_optional_text(row["error_kind"], "error_kind"),
        error_message=_optional_text(row["error_message"], "error_message"),
        next_action=_optional_text(row["next_action"], "next_action"),
        metadata=_decode_metadata(row["metadata_json"], "metadata_json"),
    )


def _runtime_event_from_row(row: aiosqlite.Row) -> RuntimeEvent:
    return RuntimeEvent(
        event_seq=_required_non_negative_int(row["event_seq"], "event_seq"),
        event_id=_required_text(row["event_id"], "event_id"),
        created_at_ms=_required_non_negative_int(row["created_at_ms"], "created_at_ms"),
        level=_required_text(row["level"], "level"),
        event_name=_required_text(row["event_name"], "event_name"),
        state=RuntimeEventState(_required_text(row["state"], "runtime_event.state")),
        duration_ms=_optional_non_negative_int(row["duration_ms"], "duration_ms"),
        node_id=_optional_text(row["node_id"], "node_id"),
        channel_slug=_optional_text(row["channel_slug"], "channel_slug"),
        runtime_slug=_optional_text(row["runtime_slug"], "runtime_slug"),
        channel_session_id=_optional_text(
            row["channel_session_id"], "channel_session_id"
        ),
        bcn_session_id=_optional_text(row["bcn_session_id"], "bcn_session_id"),
        agent_runtime_session_id=_optional_text(
            row["agent_runtime_session_id"], "agent_runtime_session_id"
        ),
        turn_id=_optional_text(row["turn_id"], "turn_id"),
        request_id=_optional_text(row["request_id"], "request_id"),
        command_id=_optional_text(row["command_id"], "command_id"),
        inbound_seq=_optional_non_negative_int(row["inbound_seq"], "inbound_seq"),
        outbound_message_id=_optional_text(
            row["outbound_message_id"], "outbound_message_id"
        ),
        error_kind=_optional_text(row["error_kind"], "error_kind"),
        error_type=_optional_text(row["error_type"], "error_type"),
        error_message=_optional_text(row["error_message"], "error_message"),
        traceback_ref=_optional_text(row["traceback_ref"], "traceback_ref"),
        metadata=_decode_metadata(row["metadata_json"], "metadata_json"),
    )


def _consumer_cursor_from_row(row: aiosqlite.Row) -> ConsumerCursor:
    return ConsumerCursor(
        bcn_session_id=_required_text(row["bcn_session_id"], "bcn_session_id"),
        delivered_through_seq=_required_non_negative_int(
            row["delivered_through_seq"], "delivered_through_seq"
        ),
        inbox_snapshot_seq=_optional_non_negative_int(
            row["inbox_snapshot_seq"], "inbox_snapshot_seq"
        ),
        inbox_snapshot_source=_optional_string_value(
            row["inbox_snapshot_source"],
            "inbox_snapshot_source",
            allow_empty=False,
        ),
        inbox_snapshot_at_ms=_optional_non_negative_int(
            row["inbox_snapshot_at_ms"], "inbox_snapshot_at_ms"
        ),
        last_check_at_ms=_optional_non_negative_int(
            row["last_check_at_ms"], "last_check_at_ms"
        ),
        last_read_at_ms=_optional_non_negative_int(
            row["last_read_at_ms"], "last_read_at_ms"
        ),
        updated_at_ms=_required_non_negative_int(row["updated_at_ms"], "updated_at_ms"),
    )


def _validate_inbound_message_input(message: InboundMessage) -> None:
    if not isinstance(message, InboundMessage):
        raise TypeError("message must be an InboundMessage")


def _validate_runtime_turn_input(turn: RuntimeTurn) -> None:
    if not isinstance(turn, RuntimeTurn):
        raise TypeError("turn must be a RuntimeTurn")
    if not isinstance(turn.state, RuntimeTurnState):
        raise TypeError("runtime turn state is invalid")
    for value, field_name in (
        (turn.latest_event_name, "latest_event_name"),
        (turn.error_kind, "error_kind"),
        (turn.error_message, "error_message"),
    ):
        _validate_optional_input_text(value, field_name)
    terminal_states = {
        RuntimeTurnState.COMPLETED,
        RuntimeTurnState.FAILED,
        RuntimeTurnState.CANCELLED,
    }
    if turn.state in terminal_states:
        if turn.completed_at_ms is None:
            raise ValueError("terminal runtime turn requires completed_at_ms")
        if turn.completed_at_ms < turn.started_at_ms:
            raise ValueError("runtime turn completion cannot precede start")
    elif turn.completed_at_ms is not None:
        raise ValueError("non-terminal runtime turn cannot have completed_at_ms")


def _validate_runtime_turn_update(
    existing: RuntimeTurn,
    incoming: RuntimeTurn,
) -> RuntimeTurn:
    for existing_value, incoming_value, field_name in (
        (
            existing.provider_turn_id,
            incoming.provider_turn_id,
            "provider_turn_id",
        ),
        (
            existing.client_user_message_id,
            incoming.client_user_message_id,
            "client_user_message_id",
        ),
    ):
        if (
            existing_value is not None
            and incoming_value is not None
            and existing_value != incoming_value
        ):
            raise ValueError(f"runtime turn {field_name} cannot change")

    if existing.state is incoming.state:
        if existing.completed_at_ms != incoming.completed_at_ms:
            raise ValueError("runtime turn completion time cannot change")
        transitioned = existing
    else:
        at_ms = (
            incoming.completed_at_ms
            if incoming.completed_at_ms is not None
            else incoming.started_at_ms
        )
        transitioned = existing.transition_to(
            incoming.state,
            at_ms=at_ms,
            error_kind=incoming.error_kind,
            error_message=incoming.error_message,
            latest_event_name=incoming.latest_event_name,
        )
    return replace(
        transitioned,
        provider_turn_id=incoming.provider_turn_id or existing.provider_turn_id,
        client_user_message_id=incoming.client_user_message_id
        or existing.client_user_message_id,
        latest_event_name=incoming.latest_event_name or transitioned.latest_event_name,
        error_kind=incoming.error_kind or transitioned.error_kind,
        error_message=incoming.error_message or transitioned.error_message,
        metadata=incoming.metadata,
    )


def _validate_outbound_message_input(message: OutboundMessage) -> None:
    if not isinstance(message, OutboundMessage):
        raise TypeError("message must be an OutboundMessage")
    if not isinstance(message.state, OutboundDeliveryState):
        raise TypeError("outbound message state is invalid")
    if not isinstance(message.fresh_check_state, FreshCheckState):
        raise TypeError("outbound fresh-check state is invalid")
    if not isinstance(message.body, str):
        raise TypeError("outbound body must be a string")
    for value, field_name in (
        (message.provider_message_id, "provider_message_id"),
        (message.provider_receipt_ref, "provider_receipt_ref"),
        (message.error_kind, "error_kind"),
        (message.error_message, "error_message"),
        (message.next_action, "next_action"),
    ):
        _validate_optional_input_text(value, field_name)
    if message.fresh_check_state is FreshCheckState.REQUIRED and (
        message.snapshot_seq is not None or message.current_inbound_seq is not None
    ):
        raise ValueError("a required outbound fresh check cannot contain evidence")
    if message.fresh_check_state is FreshCheckState.PASSED:
        if message.snapshot_seq is None or message.current_inbound_seq is None:
            raise ValueError("a passed outbound fresh check requires sequence bounds")
        if message.current_inbound_seq > message.snapshot_seq:
            raise ValueError(
                "outbound current inbound sequence exceeds snapshot sequence"
            )
    if (
        message.state
        in {
            OutboundDeliveryState.PENDING,
            OutboundDeliveryState.QUEUED,
            OutboundDeliveryState.SENT,
            OutboundDeliveryState.FAILED,
            OutboundDeliveryState.UNKNOWN,
        }
        and message.fresh_check_state is not FreshCheckState.PASSED
    ):
        raise ValueError("outbound delivery state requires a passed fresh check")
    if (
        message.state is OutboundDeliveryState.REJECTED
        and message.fresh_check_state is FreshCheckState.PASSED
    ):
        raise ValueError("rejected outbound message cannot have a passed fresh check")
    for value, field_name in (
        (message.provider_attempted_at_ms, "provider_attempted_at_ms"),
        (message.completed_at_ms, "completed_at_ms"),
        (message.draft_saved_at_ms, "draft_saved_at_ms"),
    ):
        if value is not None and value < message.created_at_ms:
            raise ValueError(f"outbound {field_name} cannot precede creation")
    if message.state is OutboundDeliveryState.DRAFT and any(
        value is not None
        for value in (
            message.provider_message_id,
            message.provider_receipt_ref,
            message.provider_attempted_at_ms,
            message.completed_at_ms,
            message.draft_saved_at_ms,
        )
    ):
        raise ValueError("draft outbound message cannot contain delivery evidence")
    if message.state in {
        OutboundDeliveryState.PENDING,
        OutboundDeliveryState.QUEUED,
    } and (
        message.completed_at_ms is not None or message.draft_saved_at_ms is not None
    ):
        raise ValueError("non-terminal outbound message cannot be terminal")
    if message.state is OutboundDeliveryState.REJECTED and any(
        value is not None
        for value in (
            message.provider_message_id,
            message.provider_receipt_ref,
            message.provider_attempted_at_ms,
        )
    ):
        raise ValueError("rejected outbound message cannot contain provider evidence")
    if (
        message.state
        in {
            OutboundDeliveryState.SENT,
            OutboundDeliveryState.FAILED,
            OutboundDeliveryState.UNKNOWN,
            OutboundDeliveryState.REJECTED,
        }
        and message.completed_at_ms is None
    ):
        raise ValueError("terminal outbound message requires completed_at_ms")
    if (
        message.state is OutboundDeliveryState.REJECTED
        and message.draft_saved_at_ms is None
    ):
        raise ValueError("rejected outbound message requires draft_saved_at_ms")
    if (
        message.state is OutboundDeliveryState.SENT
        and message.provider_message_id is None
        and message.provider_receipt_ref is None
    ):
        raise ValueError("sent outbound message requires a provider receipt")


def _validate_outbound_insert(message: OutboundMessage) -> None:
    if message.state is not OutboundDeliveryState.DRAFT:
        raise ValueError("a new outbound message must start in draft state")
    if message.fresh_check_state is not FreshCheckState.REQUIRED:
        raise ValueError("a new outbound draft requires a required fresh check")
    if any(
        value is not None
        for value in (
            message.provider_message_id,
            message.provider_receipt_ref,
            message.provider_attempted_at_ms,
            message.completed_at_ms,
            message.draft_saved_at_ms,
        )
    ):
        raise ValueError("a new outbound draft cannot contain delivery timestamps")


def _validate_outbound_update(
    existing: OutboundMessage,
    incoming: OutboundMessage,
) -> OutboundMessage:
    candidate = existing
    sequence_changed = (
        incoming.snapshot_seq != existing.snapshot_seq
        or incoming.current_inbound_seq != existing.current_inbound_seq
    )
    fresh_state_changed = incoming.fresh_check_state is not existing.fresh_check_state
    if existing.fresh_check_state is FreshCheckState.REQUIRED:
        if fresh_state_changed or sequence_changed:
            candidate = existing.record_fresh_check(
                incoming.fresh_check_state,
                snapshot_seq=incoming.snapshot_seq,
                current_inbound_seq=incoming.current_inbound_seq,
            )
    elif fresh_state_changed or sequence_changed:
        raise ValueError("outbound fresh-check evidence cannot change")

    if candidate.state is incoming.state:
        transitioned = candidate
    else:
        transitioned = candidate.transition_to(
            incoming.state,
            at_ms=_outbound_transition_time(incoming),
            provider_message_id=incoming.provider_message_id,
            provider_receipt_ref=incoming.provider_receipt_ref,
            error_kind=incoming.error_kind,
            error_message=incoming.error_message,
            next_action=incoming.next_action,
        )

    if (
        transitioned.completed_at_ms is not None
        and incoming.completed_at_ms is not None
        and transitioned.completed_at_ms != incoming.completed_at_ms
    ):
        raise ValueError("outbound completion time cannot change")
    if (
        transitioned.draft_saved_at_ms is not None
        and incoming.draft_saved_at_ms is not None
        and transitioned.draft_saved_at_ms != incoming.draft_saved_at_ms
    ):
        raise ValueError("outbound draft time cannot change")
    return replace(
        transitioned,
        provider_message_id=_merge_optional_text(
            transitioned.provider_message_id,
            incoming.provider_message_id,
            "provider_message_id",
        ),
        provider_receipt_ref=_merge_optional_text(
            transitioned.provider_receipt_ref,
            incoming.provider_receipt_ref,
            "provider_receipt_ref",
        ),
        provider_attempted_at_ms=_merge_timestamp(
            transitioned.provider_attempted_at_ms,
            incoming.provider_attempted_at_ms,
            "provider_attempted_at_ms",
        ),
        completed_at_ms=transitioned.completed_at_ms
        if transitioned.completed_at_ms is not None
        else incoming.completed_at_ms,
        draft_saved_at_ms=transitioned.draft_saved_at_ms
        if transitioned.draft_saved_at_ms is not None
        else incoming.draft_saved_at_ms,
        error_kind=incoming.error_kind or transitioned.error_kind,
        error_message=incoming.error_message or transitioned.error_message,
        next_action=incoming.next_action or transitioned.next_action,
        metadata=incoming.metadata,
    )


def _outbound_transition_time(message: OutboundMessage) -> int:
    return (
        message.completed_at_ms
        or message.draft_saved_at_ms
        or message.provider_attempted_at_ms
        or message.created_at_ms
    )


def _merge_optional_text(
    existing: str | None,
    incoming: str | None,
    field_name: str,
) -> str | None:
    if existing is not None and incoming is not None and existing != incoming:
        raise ValueError(f"outbound {field_name} cannot change")
    return incoming or existing


def _merge_timestamp(
    existing: int | None,
    incoming: int | None,
    field_name: str,
) -> int | None:
    if existing is not None and incoming is not None and existing != incoming:
        raise ValueError(f"outbound {field_name} cannot change")
    return incoming if incoming is not None else existing


def _validate_runtime_event_input(event: RuntimeEvent) -> None:
    if not isinstance(event, RuntimeEvent):
        raise TypeError("event must be a RuntimeEvent")
    if not isinstance(event.state, RuntimeEventState):
        raise TypeError("runtime event state is invalid")
    for value, field_name in (
        (event.node_id, "node_id"),
        (event.channel_slug, "channel_slug"),
        (event.runtime_slug, "runtime_slug"),
        (event.channel_session_id, "channel_session_id"),
        (event.bcn_session_id, "bcn_session_id"),
        (event.agent_runtime_session_id, "agent_runtime_session_id"),
        (event.turn_id, "turn_id"),
        (event.request_id, "request_id"),
        (event.command_id, "command_id"),
        (event.outbound_message_id, "outbound_message_id"),
        (event.error_kind, "error_kind"),
        (event.error_type, "error_type"),
        (event.error_message, "error_message"),
        (event.traceback_ref, "traceback_ref"),
    ):
        _validate_optional_input_text(value, field_name)


def _same_runtime_event_payload(
    existing: RuntimeEvent,
    incoming: RuntimeEvent,
) -> bool:
    return replace(existing, event_seq=incoming.event_seq) == incoming


def _validate_optional_input_text(value: object, field_name: str) -> None:
    if value is not None and (not isinstance(value, str) or not value):
        raise ValueError(f"{field_name} must be a non-empty string when present")


def _same_inbound_payload(
    existing: InboundMessage,
    incoming: InboundMessage,
) -> bool:
    return (
        existing.bcn_session_id,
        existing.channel_session_id,
        existing.channel_slug,
        existing.provider_message_id,
        existing.provider_time_ms,
        existing.sender_id,
        existing.sender_display_name,
        existing.message_type,
        existing.canonical_target,
        existing.body,
        existing.provider_thread_id,
        existing.reply_to_provider_message_id,
        existing.provider_payload_ref,
        existing.metadata,
    ) == (
        incoming.bcn_session_id,
        incoming.channel_session_id,
        incoming.channel_slug,
        incoming.provider_message_id,
        incoming.provider_time_ms,
        incoming.sender_id,
        incoming.sender_display_name,
        incoming.message_type,
        incoming.canonical_target,
        incoming.body,
        incoming.provider_thread_id,
        incoming.reply_to_provider_message_id,
        incoming.provider_payload_ref,
        incoming.metadata,
    )


def _validate_consumer_cursor_input(cursor: ConsumerCursor) -> None:
    if not isinstance(cursor, ConsumerCursor):
        raise TypeError("cursor must be a ConsumerCursor")
    source = cursor.inbox_snapshot_source
    if source is not None and source not in {"check", "read"}:
        raise ValueError("inbox_snapshot_source must be 'check' or 'read'")
    if cursor.inbox_snapshot_seq is None:
        if cursor.inbox_snapshot_source is not None:
            raise ValueError("inbox snapshot source requires a snapshot sequence")
        if cursor.inbox_snapshot_at_ms is not None:
            raise ValueError("inbox snapshot time requires a snapshot sequence")
    elif cursor.inbox_snapshot_at_ms is None:
        raise ValueError("inbox snapshot sequence requires a snapshot time")


def _validate_cursor_bounds(cursor: ConsumerCursor, latest_seq: int) -> None:
    if cursor.delivered_through_seq > latest_seq:
        raise ValueError("delivered cursor cannot exceed the latest inbound sequence")
    if cursor.inbox_snapshot_seq is not None and cursor.inbox_snapshot_seq > latest_seq:
        raise ValueError("inbox snapshot cannot exceed the latest inbound sequence")


def _validate_consumer_cursor_update(
    existing: ConsumerCursor,
    incoming: ConsumerCursor,
) -> None:
    if incoming.updated_at_ms < existing.updated_at_ms:
        raise ValueError("consumer cursor updated_at_ms cannot move backwards")
    if incoming.delivered_through_seq < existing.delivered_through_seq:
        raise ValueError("delivered cursor cannot move backwards")
    if existing.inbox_snapshot_seq is not None and (
        incoming.inbox_snapshot_seq is None
        or incoming.inbox_snapshot_seq < existing.inbox_snapshot_seq
    ):
        raise ValueError("inbox snapshot cannot move backwards")
    if (
        incoming.inbox_snapshot_source == "read"
        and incoming.delivered_through_seq != existing.delivered_through_seq
    ):
        raise ValueError("read snapshot cannot advance the delivered cursor")
    for incoming_value, existing_value, field_name in (
        (
            incoming.inbox_snapshot_at_ms,
            existing.inbox_snapshot_at_ms,
            "inbox_snapshot_at_ms",
        ),
        (incoming.last_check_at_ms, existing.last_check_at_ms, "last_check_at_ms"),
        (incoming.last_read_at_ms, existing.last_read_at_ms, "last_read_at_ms"),
    ):
        if (
            incoming_value is not None
            and existing_value is not None
            and incoming_value < existing_value
        ):
            raise ValueError(f"{field_name} cannot move backwards")


def _validate_channel_session_input(session: ChannelSession) -> None:
    if not isinstance(session.state, ChannelSessionState):
        raise TypeError("channel session state is invalid")
    if not isinstance(session.following, bool):
        raise TypeError("channel session following must be a boolean")


def _validate_bcn_session_input(session: BcnSession) -> None:
    if not isinstance(session.state, BcnSessionState):
        raise TypeError("bcn session state is invalid")


def _validate_runtime_session_input(session: RuntimeSession) -> None:
    if not isinstance(session.process_state, RuntimeProcessState):
        raise TypeError("runtime session process state is invalid")


def _validate_channel_session_update(
    existing: ChannelSession,
    incoming: ChannelSession,
) -> ChannelSession:
    if (
        existing.channel_slug != incoming.channel_slug
        or existing.provider_conversation_key != incoming.provider_conversation_key
        or existing.provider_thread_key != incoming.provider_thread_key
        or existing.created_at_ms != incoming.created_at_ms
    ):
        raise ValueError("channel session identity cannot change")
    _validate_updated_at(existing.updated_at_ms, incoming.updated_at_ms)
    transitioned = existing.transition_to(
        incoming.state,
        updated_at_ms=incoming.updated_at_ms,
    )
    return replace(
        transitioned,
        updated_at_ms=incoming.updated_at_ms,
        following=incoming.following,
        last_inbound_at_ms=incoming.last_inbound_at_ms,
        last_outbound_at_ms=incoming.last_outbound_at_ms,
        metadata=incoming.metadata,
    )


def _validate_bcn_session_update(
    existing: BcnSession,
    incoming: BcnSession,
) -> BcnSession:
    if (
        existing.channel_session_id != incoming.channel_session_id
        or existing.workspace_id != incoming.workspace_id
        or existing.created_at_ms != incoming.created_at_ms
    ):
        raise ValueError("bcn session binding cannot change")
    _validate_updated_at(existing.updated_at_ms, incoming.updated_at_ms)
    transitioned = existing.transition_to(
        incoming.state,
        updated_at_ms=incoming.updated_at_ms,
    )
    return replace(
        transitioned,
        updated_at_ms=incoming.updated_at_ms,
        last_activity_at_ms=incoming.last_activity_at_ms,
        metadata=incoming.metadata,
    )


def _validate_runtime_session_update(
    existing: RuntimeSession,
    incoming: RuntimeSession,
) -> RuntimeSession:
    if (
        existing.bcn_session_id != incoming.bcn_session_id
        or existing.channel_session_id != incoming.channel_session_id
        or existing.runtime_slug != incoming.runtime_slug
        or existing.workspace_id != incoming.workspace_id
        or existing.created_at_ms != incoming.created_at_ms
    ):
        raise ValueError("runtime session binding cannot change")
    _validate_updated_at(existing.updated_at_ms, incoming.updated_at_ms)
    transitioned = existing.transition_process_to(
        incoming.process_state,
        updated_at_ms=incoming.updated_at_ms,
        error_kind=incoming.last_error_kind,
        error_message=incoming.last_error_message,
    )
    return replace(
        transitioned,
        updated_at_ms=incoming.updated_at_ms,
        provider_thread_id=incoming.provider_thread_id,
        process_id=incoming.process_id,
        last_error_kind=incoming.last_error_kind or transitioned.last_error_kind,
        last_error_message=incoming.last_error_message
        or transitioned.last_error_message,
        metadata=incoming.metadata,
    )


def _validate_updated_at(existing: int, incoming: int) -> None:
    if incoming < existing:
        raise ValueError("session updated_at_ms cannot move backwards")


def _required_text(value: object, field_name: str) -> str:
    return _string_value(value, field_name, allow_empty=False)


def _validate_non_empty_text(value: object, field_name: str) -> None:
    _required_text(value, field_name)


def _validate_non_negative_int(value: object, field_name: str) -> None:
    _required_non_negative_int(value, field_name)


def _validate_positive_int(value: object, field_name: str) -> None:
    _required_positive_int(value, field_name)


def _optional_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _string_value(value, field_name, allow_empty=False)


def _optional_string_value(
    value: object,
    field_name: str,
    *,
    allow_empty: bool,
) -> str | None:
    if value is None:
        return None
    return _string_value(value, field_name, allow_empty=allow_empty)


def _string_value(value: object, field_name: str, *, allow_empty: bool) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        requirement = "a string" if allow_empty else "a non-empty string"
        raise ValueError(f"{field_name} must be {requirement}")
    return value


def _required_non_negative_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value


def _required_positive_int(value: object, field_name: str) -> int:
    result = _required_non_negative_int(value, field_name)
    if result == 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return result


def _optional_non_negative_int(value: object, field_name: str) -> int | None:
    if value is None:
        return None
    return _required_non_negative_int(value, field_name)


def _encode_metadata(metadata: Mapping[str, object]) -> str:
    if not isinstance(metadata, Mapping):
        raise TypeError("metadata must be a mapping")
    if any(not isinstance(key, str) for key in metadata):
        raise ValueError("metadata keys must be strings")
    try:
        return json.dumps(
            dict(metadata),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as error:
        raise ValueError("metadata must be JSON serializable") from error


def _decode_metadata(value: object, field_name: str) -> dict[str, object]:
    if value is None:
        return {}
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must contain a JSON object")
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError(f"{field_name} contains invalid JSON") from error
    if not isinstance(decoded, dict):
        raise TypeError(f"{field_name} must contain a JSON object")
    return decoded


def _current_time_ms() -> int:
    return time_ns() // 1_000_000


def _restrict_permissions(path: Path, mode: int) -> None:
    if os.name != "nt":
        path.chmod(mode)


__all__ = [
    "DATABASE_FILENAME",
    "DEFAULT_BUSY_TIMEOUT_MS",
    "MigrationChecksumError",
    "MigrationError",
    "NodeIdentityError",
    "NodeState",
    "SqliteDatabase",
    "SqliteTransaction",
]
