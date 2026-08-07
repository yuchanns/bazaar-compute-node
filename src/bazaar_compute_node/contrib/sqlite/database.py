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
    InboundMessage,
    RuntimeProcessState,
    RuntimeSession,
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
