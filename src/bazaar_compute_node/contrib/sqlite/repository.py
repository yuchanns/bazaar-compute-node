from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import replace
from types import TracebackType
from typing import TYPE_CHECKING, Self, cast
from uuid import uuid7

import aiosqlite

from ...core.inbox import InboxTargetPage
from ...core.models import (
    BcnSession,
    ChannelSession,
    ConsumerCursor,
    InboundMessage,
    OutboundMessage,
    RuntimeAttempt,
)
from .codec import (
    bcn_session_from_row,
    channel_session_from_row,
    consumer_cursor_from_row,
    encode_metadata,
    inbound_attachment_from_row,
    inbound_message_from_row,
    outbound_message_from_row,
    runtime_attempt_from_row,
    validate_bcn_session_update,
    validate_channel_session_input,
    validate_channel_session_update,
    validate_consumer_cursor_input,
    validate_consumer_cursor_update,
    validate_cursor_bounds,
    validate_inbound_message_input,
    validate_outbound_insert,
    validate_outbound_message_input,
    validate_outbound_update,
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
        provider_thread_id: str,
    ) -> ChannelSession | None:
        row = await self._fetch_one_or_conflict(
            "SELECT id, channel, "
            "provider_thread_id, target_kind, following, "
            "created_at_ms, updated_at_ms, last_inbound_at_ms, last_outbound_at_ms, "
            "provider_identity_ref_json "
            "FROM channel_sessions "
            "WHERE channel = ? AND provider_thread_id = ? ORDER BY rowid",
            (channel, provider_thread_id),
            "channel provider identity",
        )
        return channel_session_from_row(row) if row is not None else None

    async def get_channel_session(self, session_id: str) -> ChannelSession | None:
        row = await self.fetchone(
            "SELECT id, channel, "
            "provider_thread_id, target_kind, following, "
            "created_at_ms, updated_at_ms, last_inbound_at_ms, last_outbound_at_ms, "
            "provider_identity_ref_json "
            "FROM channel_sessions WHERE id = ?",
            (session_id,),
        )
        return channel_session_from_row(row) if row is not None else None

    async def get_bcn_session(self, session_id: str) -> BcnSession | None:
        row = await self.fetchone(
            "SELECT id, channel_session_id, workspace_id, "
            "created_at_ms, updated_at_ms, last_activity_at_ms, "
            "metadata_json FROM bcn_sessions WHERE id = ?",
            (session_id,),
        )
        return bcn_session_from_row(row) if row is not None else None

    async def find_bcn_session(self, channel_session_id: str) -> BcnSession | None:
        row = await self._fetch_one_or_conflict(
            "SELECT id, channel_session_id, workspace_id, "
            "created_at_ms, updated_at_ms, last_activity_at_ms, "
            "metadata_json FROM bcn_sessions "
            "WHERE channel_session_id = ? ORDER BY rowid",
            (channel_session_id,),
            "channel-to-bcn session binding",
        )
        return bcn_session_from_row(row) if row is not None else None

    async def get_runtime_attempt(self, turn_id: str) -> RuntimeAttempt | None:
        row = await self.fetchone(
            "SELECT turn_id, session_id, client_user_message_id, started_at_ms "
            "FROM runtime_attempts WHERE turn_id = ?",
            (turn_id,),
        )
        return runtime_attempt_from_row(row) if row is not None else None

    async def get_consumer_cursor(self, session_id: str) -> ConsumerCursor | None:
        row = await self.fetchone(
            "SELECT session_id, delivered_through_seq, inbox_snapshot_seq, "
            "inbox_snapshot_source, inbox_snapshot_at_ms, last_check_at_ms, "
            "last_read_at_ms, updated_at_ms FROM consumer_cursors "
            "WHERE session_id = ?",
            (session_id,),
        )
        return consumer_cursor_from_row(row) if row is not None else None

    async def get_latest_inbound_seq(self, session_id: str) -> int:
        row = await self.fetchone(
            "SELECT COALESCE(MAX(seq), 0) AS latest_seq FROM inbound_messages "
            "WHERE session_id = ?",
            (session_id,),
        )
        if row is None:
            raise RuntimeError("SQLite latest inbound sequence query returned no row")
        return cast(int, row["latest_seq"])

    async def get_latest_inbound_message(
        self,
        session_id: str,
    ) -> InboundMessage | None:
        row = await self.fetchone(
            "SELECT seq, message_id, session_id, channel_session_id, "
            "channel, provider_thread_id, provider_message_id, provider_time_ms, "
            "received_at_ms, sender, message_type, canonical_target, target_kind, "
            "reply_to_message_id, body, mentions_agent, notifies_runtime, "
            "provider_payload_ref, metadata_json FROM inbound_messages "
            "WHERE session_id = ? ORDER BY seq DESC LIMIT 1",
            (session_id,),
        )
        if row is None:
            return None
        return inbound_message_from_row(
            row,
            await self._attachments(row["message_id"]),
        )

    async def count_inbound_messages(
        self,
        session_id: str,
        *,
        after_seq: int | None = None,
        target: str | None = None,
    ) -> int:
        predicates = ["session_id = ?"]
        parameters: list[object] = [session_id]
        if after_seq is not None:
            predicates.append("seq > ?")
            parameters.append(after_seq)
        if target is not None:
            predicates.append("canonical_target = ?")
            parameters.append(target)
        row = await self.fetchone(
            "SELECT COUNT(*) AS message_count FROM inbound_messages WHERE "
            + " AND ".join(predicates),
            parameters,
        )
        if row is None:
            raise RuntimeError("SQLite inbound message count query returned no row")
        return cast(int, row["message_count"])

    async def list_inbox_targets(
        self, *, limit: int = 100, offset: int = 0
    ) -> InboxTargetPage:
        del limit, offset
        raise RuntimeError("an Agent-scoped storage transaction is required")

    async def resolve_inbox_target(self, target: str) -> BcnSession:
        del target
        raise RuntimeError("an Agent-scoped storage transaction is required")

    async def find_inbound_message(
        self,
        channel: str,
        provider_thread_id: str,
        provider_message_id: str,
    ) -> InboundMessage | None:
        row = await self._fetch_one_or_conflict(
            "SELECT seq, message_id, session_id, channel_session_id, "
            "channel, provider_thread_id, provider_message_id, provider_time_ms, "
            "received_at_ms, sender, message_type, "
            "canonical_target, target_kind, "
            "reply_to_message_id, body, mentions_agent, "
            "notifies_runtime, provider_payload_ref, metadata_json "
            "FROM inbound_messages WHERE channel = ? "
            "AND provider_thread_id = ? AND provider_message_id = ? ORDER BY seq",
            (channel, provider_thread_id, provider_message_id),
            "provider inbound identity",
        )
        if row is None:
            return None
        return inbound_message_from_row(row, await self._attachments(row["message_id"]))

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
        latest: bool = False,
        limit: int = 100,
    ) -> tuple[InboundMessage, ...]:
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
            order = "DESC" if latest else "ASC"
            rows = await self.fetchall(
                "SELECT seq, message_id, session_id, channel_session_id, "
                "channel, provider_thread_id, provider_message_id, provider_time_ms, "
                "received_at_ms, sender, message_type, "
                "canonical_target, target_kind, "
                "reply_to_message_id, body, mentions_agent, notifies_runtime, provider_payload_ref, "
                "metadata_json FROM inbound_messages "
                f"WHERE {where_clause} ORDER BY seq {order} LIMIT ?",
                (*parameters, limit),
            )
            if latest:
                rows.reverse()
            messages = []
            for row in rows:
                messages.append(
                    inbound_message_from_row(
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
        anchor_seq = cast(int, anchor["seq"])
        count_row = await self.fetchone(
            "SELECT COUNT(*) AS message_count FROM inbound_messages "
            f"WHERE {where_clause}",
            parameters,
        )
        if count_row is None:
            raise RuntimeError("SQLite inbound history count query returned no row")
        message_count = cast(int, count_row["message_count"])
        position_row = await self.fetchone(
            "SELECT COUNT(*) AS anchor_position FROM inbound_messages "
            f"WHERE {where_clause} AND seq <= ?",
            (*parameters, anchor_seq),
        )
        if position_row is None:
            raise RuntimeError("SQLite inbound anchor position query returned no row")
        anchor_position = cast(int, position_row["anchor_position"])
        before_count = limit // 2
        start_position = max(anchor_position - before_count, 1)
        start_position = min(
            start_position,
            max(message_count - limit + 1, 1),
        )
        end_position = start_position + limit - 1

        filtered_query = (
            "SELECT seq, message_id, session_id, channel_session_id, "
            "channel, provider_thread_id, provider_message_id, provider_time_ms, "
            "received_at_ms, sender, message_type, "
            "canonical_target, target_kind, "
            "reply_to_message_id, body, mentions_agent, notifies_runtime, provider_payload_ref, "
            "metadata_json, ROW_NUMBER() OVER (ORDER BY seq) AS row_number "
            "FROM inbound_messages "
            f"WHERE {where_clause}"
        )
        rows = await self.fetchall(
            "WITH filtered AS ("
            + filtered_query
            + ") SELECT seq, message_id, session_id, channel_session_id, "
            "channel, provider_thread_id, provider_message_id, provider_time_ms, "
            "received_at_ms, sender, message_type, "
            "canonical_target, target_kind, "
            "reply_to_message_id, body, mentions_agent, notifies_runtime, provider_payload_ref, "
            "metadata_json FROM filtered WHERE row_number BETWEEN ? AND ? "
            "ORDER BY row_number",
            (*parameters, start_position, end_position),
        )
        messages = []
        for row in rows:
            messages.append(
                inbound_message_from_row(
                    row, await self._attachments(row["message_id"])
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
            "SELECT seq, message_id, session_id, channel_session_id, "
            "channel, provider_thread_id, provider_message_id, provider_time_ms, "
            "received_at_ms, sender, message_type, "
            "canonical_target, target_kind, "
            "reply_to_message_id, body, mentions_agent, notifies_runtime, provider_payload_ref, "
            "metadata_json FROM inbound_messages "
            "WHERE channel = ? AND provider_thread_id = ? "
            "AND provider_message_id = ? ORDER BY seq",
            (
                message.channel,
                message.provider_thread_id,
                message.provider_message_id,
            ),
            "provider inbound identity",
        )
        if existing_row is not None:
            existing = inbound_message_from_row(
                existing_row, await self._attachments(existing_row["message_id"])
            )
            return existing

        message_id_row = await self.fetchone(
            "SELECT 1 FROM inbound_messages WHERE message_id = ?",
            (message.message_id,),
        )
        if message_id_row is not None:
            raise ValueError("message id is already bound to another inbound message")

        sequence_row = await self.fetchone(
            "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM inbound_messages"
        )
        if sequence_row is None:
            raise RuntimeError("SQLite inbound sequence query returned no row")
        next_seq = cast(int, sequence_row["next_seq"])
        canonical = replace(message, seq=next_seq)
        if canonical.reply_to_message_id is not None:
            referenced = await self.fetchone(
                "SELECT session_id, seq FROM inbound_messages WHERE message_id = ?",
                (canonical.reply_to_message_id,),
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
        await self.execute(
            "INSERT INTO inbound_messages ("
            "message_id, seq, session_id, channel_session_id, channel, "
            "provider_thread_id, provider_message_id, provider_time_ms, "
            "received_at_ms, sender, message_type, canonical_target, target_kind, "
            "reply_to_message_id, body, "
            "mentions_agent, notifies_runtime, provider_payload_ref, metadata_json"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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

    async def _attachments(self, message_id: str):
        rows = await self.fetchall(
            "SELECT attachment_id, name, kind, state, media_type, relative_path, "
            "size_bytes, error FROM inbound_attachments WHERE message_id = ? "
            "ORDER BY ordinal",
            (message_id,),
        )
        return tuple(inbound_attachment_from_row(row) for row in rows)

    async def save_consumer_cursor(self, cursor: ConsumerCursor) -> None:
        validate_consumer_cursor_input(cursor)
        if await self.get_bcn_session(cursor.session_id) is None:
            raise ValueError(f"unknown bcn session: {cursor.session_id}")
        latest_seq = await self.get_latest_inbound_seq(cursor.session_id)
        validate_cursor_bounds(cursor, latest_seq)
        existing = await self.get_consumer_cursor(cursor.session_id)
        if existing is not None:
            validate_consumer_cursor_update(existing, cursor)
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
            "channel_session_id, target, reply_to_message_id, body, "
            "attachments_json, "
            "state, snapshot_seq, current_inbound_seq, provider_message_id, "
            "provider_receipt_ref, created_at_ms, provider_attempted_at_ms, "
            "completed_at_ms, error_kind, error_message, metadata_json "
            "FROM outbound_messages "
            "WHERE outbound_message_id = ?",
            (outbound_message_id,),
        )
        return outbound_message_from_row(row) if row is not None else None

    async def save_outbound_message(self, message: OutboundMessage) -> OutboundMessage:
        validate_outbound_message_input(message)
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
            validate_outbound_insert(canonical)
            await self.execute(
                "INSERT INTO outbound_messages ("
                "outbound_message_id, command_id, session_id, "
                "channel_session_id, target, reply_to_message_id, body, "
                "attachments_json, "
                "state, snapshot_seq, current_inbound_seq, provider_message_id, "
                "provider_receipt_ref, created_at_ms, provider_attempted_at_ms, "
                "completed_at_ms, error_kind, error_message, metadata_json"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    canonical.outbound_message_id,
                    canonical.command_id,
                    canonical.session_id,
                    canonical.channel_session_id,
                    canonical.target,
                    canonical.reply_to_message_id,
                    canonical.body,
                    json.dumps(
                        [
                            {
                                "name": attachment.name,
                                "relative_path": attachment.relative_path,
                                "media_type": attachment.media_type,
                                "size_bytes": attachment.size_bytes,
                                "sha256": attachment.sha256,
                            }
                            for attachment in canonical.attachments
                        ],
                        separators=(",", ":"),
                    ),
                    canonical.state.value,
                    canonical.snapshot_seq,
                    canonical.current_inbound_seq,
                    canonical.provider_message_id,
                    canonical.provider_receipt_ref,
                    canonical.created_at_ms,
                    canonical.provider_attempted_at_ms,
                    canonical.completed_at_ms,
                    canonical.error_kind,
                    canonical.error_message,
                    encode_metadata(canonical.metadata),
                ),
            )
            return canonical

        if (
            existing.command_id != message.command_id
            or existing.session_id != message.session_id
            or existing.channel_session_id != message.channel_session_id
            or existing.target != message.target
            or existing.reply_to_message_id != message.reply_to_message_id
            or existing.body != message.body
            or existing.attachments != message.attachments
            or existing.created_at_ms != message.created_at_ms
        ):
            raise ValueError("outbound message identity cannot change")
        canonical = validate_outbound_update(existing, message)
        await self.execute(
            "UPDATE outbound_messages SET state = ?, snapshot_seq = ?, "
            "current_inbound_seq = ?, provider_message_id = ?, "
            "provider_receipt_ref = ?, provider_attempted_at_ms = ?, "
            "completed_at_ms = ?, error_kind = ?, error_message = ?, "
            "metadata_json = ? "
            "WHERE outbound_message_id = ?",
            (
                canonical.state.value,
                canonical.snapshot_seq,
                canonical.current_inbound_seq,
                canonical.provider_message_id,
                canonical.provider_receipt_ref,
                canonical.provider_attempted_at_ms,
                canonical.completed_at_ms,
                canonical.error_kind,
                canonical.error_message,
                encode_metadata(canonical.metadata),
                canonical.outbound_message_id,
            ),
        )
        return canonical

    async def save_channel_session(self, session: ChannelSession) -> None:
        validate_channel_session_input(session)
        existing = await self.get_channel_session(session.id)
        if existing is None:
            duplicate = await self.find_channel_session(
                channel=session.channel,
                provider_thread_id=session.provider_thread_id,
            )
            if duplicate is not None:
                raise ValueError(
                    f"channel provider identity is already bound to {duplicate.id}"
                )
            await self.execute(
                "INSERT INTO channel_sessions ("
                "id, channel, provider_thread_id, target_kind, following, "
                "provider_identity_ref_json, created_at_ms, updated_at_ms, "
                "last_inbound_at_ms, last_outbound_at_ms"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session.id,
                    session.channel,
                    session.provider_thread_id,
                    session.target_kind.value,
                    int(session.following),
                    encode_metadata(session.metadata),
                    session.created_at_ms,
                    session.updated_at_ms,
                    session.last_inbound_at_ms,
                    session.last_outbound_at_ms,
                ),
            )
            return

        session = validate_channel_session_update(existing, session)
        await self.execute(
            "UPDATE channel_sessions SET target_kind = ?, following = ?, "
            "updated_at_ms = ?, last_inbound_at_ms = ?, last_outbound_at_ms = ?, "
            "provider_identity_ref_json = ? WHERE id = ?",
            (
                session.target_kind.value,
                int(session.following),
                session.updated_at_ms,
                session.last_inbound_at_ms,
                session.last_outbound_at_ms,
                encode_metadata(session.metadata),
                session.id,
            ),
        )

    async def save_bcn_session(self, session: BcnSession) -> None:
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
                "id, channel_session_id, workspace_id, "
                "created_at_ms, updated_at_ms, last_activity_at_ms, "
                "metadata_json"
                ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    session.id,
                    session.channel_session_id,
                    session.workspace_id,
                    session.created_at_ms,
                    session.updated_at_ms,
                    session.last_activity_at_ms,
                    encode_metadata(session.metadata),
                ),
            )
            return

        session = validate_bcn_session_update(existing, session)
        await self.execute(
            "UPDATE bcn_sessions SET updated_at_ms = ?, "
            "last_activity_at_ms = ?, metadata_json = ? "
            "WHERE id = ?",
            (
                session.updated_at_ms,
                session.last_activity_at_ms,
                encode_metadata(session.metadata),
                session.id,
            ),
        )

    async def save_runtime_attempt(self, attempt: object) -> None:
        if not isinstance(attempt, RuntimeAttempt):
            raise TypeError("attempt must be a RuntimeAttempt")
        existing = await self.get_runtime_attempt(attempt.turn_id)
        if existing is not None:
            if existing != attempt:
                raise ValueError("runtime attempt is immutable")
            return
        await self.execute(
            "INSERT INTO runtime_attempts "
            "(turn_id, session_id, client_user_message_id, started_at_ms) "
            "VALUES (?, ?, ?, ?)",
            (
                attempt.turn_id,
                attempt.session_id,
                attempt.client_user_message_id,
                attempt.started_at_ms,
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

    def _require_workspace(self, _: str) -> None:
        raise RuntimeError("an Agent-scoped storage transaction is required")

    def _require_active_connection(self) -> aiosqlite.Connection:
        if not self._active or self._connection is None:
            raise RuntimeError("SQLite transaction is not active")
        return self._connection
