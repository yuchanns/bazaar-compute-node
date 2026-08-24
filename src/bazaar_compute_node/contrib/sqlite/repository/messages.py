from __future__ import annotations

import json
from dataclasses import replace
from typing import cast
from uuid import uuid7

import aiosqlite

from ....core.inbox import InboxTargetPage
from ....core.models import (
    BcnSession,
    ChannelTargetKind,
    InboundAttachment,
    InboxTargetSummary,
    Message,
    OutboundAttachment,
    SenderIdentity,
)
from ....core.reminder import canonical_id_reference
from ....core.storage import InboxTargetResolutionError
from ..codec import (
    bcn_session_from_row,
    encode_metadata,
    inbound_attachment_from_row,
    inbound_message_from_row,
    outbound_message_from_row,
    validate_inbound_message_input,
    validate_outbound_insert,
    validate_outbound_message_input,
    validate_outbound_update,
)
from .base import RepositoryBase

_INBOUND_COLUMNS = (
    "seq, message_id, session_id, channel_session_id, channel, "
    "provider_thread_id, provider_message_id, provider_time_ms, "
    "received_at_ms, sender, message_type, canonical_target, target_kind, "
    "reply_to_message_id, body, mentions_agent, notifies_runtime, "
    "provider_payload_ref, metadata_json"
)


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
    WHERE agent_id = /*agent_id*/?
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
    WHERE message.agent_id = /*agent_id*/?
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
        ON channel.agent_id = /*agent_id*/?
       AND channel.id = bcn.channel_session_id
    LEFT JOIN latest_inbound AS latest
        ON latest.session_id = bcn.id
    LEFT JOIN pending_inbound AS pending
        ON pending.session_id = bcn.id
    WHERE bcn.agent_id = /*agent_id*/?
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


class MessageOperations(RepositoryBase):
    async def get_latest_inbound_seq(self, session_id: str) -> int:
        row = await self.fetchone(
            "SELECT COALESCE(MAX(seq), 0) AS latest_seq FROM inbound_messages "
            "WHERE agent_id = /*agent_id*/? AND session_id = ?",
            (session_id,),
        )
        if row is None:
            raise RuntimeError("SQLite latest inbound sequence query returned no row")
        return cast(int, row["latest_seq"])

    async def count_inbound_messages(
        self,
        session_id: str,
        *,
        after_seq: int | None = None,
        target: str | None = None,
    ) -> int:
        predicates = ["agent_id = /*agent_id*/?", "session_id = ?"]
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
            "ON channel.agent_id = /*agent_id*/? "
            "AND channel.id = bcn.channel_session_id "
            "WHERE bcn.agent_id = /*agent_id*/? "
            "AND ("
            "channel.target_kind || ':' || channel.id = ? "
            "OR EXISTS ("
            "SELECT 1 FROM inbound_messages AS message "
            "WHERE message.agent_id = /*agent_id*/? "
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
    ) -> Message | None:
        row = await self._fetch_one_or_conflict(
            f"SELECT {_INBOUND_COLUMNS} FROM inbound_messages "
            "WHERE agent_id = /*agent_id*/? AND channel = ? "
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
            "WHERE message.agent_id = /*agent_id*/? AND attachment.state = 'ready' "
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
        latest: bool = False,
        limit: int = 100,
    ) -> tuple[Message, ...]:
        predicates = ["agent_id = /*agent_id*/?", "session_id = ?"]
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
                f"SELECT {_INBOUND_COLUMNS} FROM inbound_messages "
                f"WHERE {where_clause} ORDER BY seq {order} LIMIT ?",
                (*parameters, limit),
            )
            if latest:
                rows.reverse()
            messages: list[Message] = []
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

    async def append_inbound_message(
        self,
        message: Message[InboundAttachment],
    ) -> Message[InboundAttachment]:
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
            "WHERE agent_id = /*agent_id*/? AND channel = ? "
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
                "WHERE agent_id = /*agent_id*/? AND message_id = ?",
                (referenced_id,),
            )
            if referenced is None:
                referenced_id = self._agent_local_id("message", referenced_id)
                referenced = await self.fetchone(
                    "SELECT message_id, session_id, seq FROM inbound_messages "
                    "WHERE agent_id = /*agent_id*/? AND message_id = ?",
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
            "agent_id, message_id, seq, session_id, channel_session_id, channel, "
            "provider_thread_id, provider_message_id, provider_time_ms, "
            "received_at_ms, sender, message_type, canonical_target, target_kind, "
            "reply_to_message_id, body, mentions_agent, notifies_runtime, "
            "provider_payload_ref, metadata_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                self.agent_id,
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
                canonical.target,
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
    ) -> Message[OutboundAttachment] | None:
        row = await self.fetchone(
            "SELECT outbound_message_id, command_id, session_id, channel_session_id, "
            "target, reply_to_message_id, body, attachments_json, state, "
            "snapshot_seq, current_inbound_seq, provider_message_id, "
            "provider_receipt_ref, created_at_ms, provider_attempted_at_ms, "
            "completed_at_ms, error_kind, error_message, metadata_json "
            "FROM outbound_messages "
            "WHERE agent_id = /*agent_id*/? AND outbound_message_id = ?",
            (outbound_message_id,),
        )
        return outbound_message_from_row(row) if row is not None else None

    async def resolve_inbound_message(
        self,
        session_id: str,
        message_id: str,
    ) -> Message | None:
        reference = canonical_id_reference(message_id)
        row = await self.fetchone(
            f"SELECT {_INBOUND_COLUMNS} FROM inbound_messages "
            "WHERE agent_id = /*agent_id*/? AND session_id = ? AND message_id = ?",
            (session_id, reference),
        )
        if row is None:
            return None
        return inbound_message_from_row(
            row,
            await self._attachments(row["message_id"]),
        )

    async def get_latest_inbound_message(
        self,
        session_id: str,
    ) -> Message | None:
        agent_predicate = self._agent_predicate()
        row = await self.fetchone(
            f"SELECT {_INBOUND_COLUMNS} FROM inbound_messages "
            f"WHERE {agent_predicate}session_id = ? ORDER BY seq DESC LIMIT 1",
            (session_id,),
        )
        if row is None:
            return None
        return inbound_message_from_row(
            row,
            await self._attachments(row["message_id"]),
        )

    async def _attachments(self, message_id: str):
        rows = await self.fetchall(
            "SELECT attachment_id, name, kind, state, media_type, relative_path, "
            "size_bytes, error FROM inbound_attachments WHERE message_id = ? "
            "ORDER BY ordinal",
            (message_id,),
        )
        return tuple(inbound_attachment_from_row(row) for row in rows)

    async def save_outbound_message(
        self,
        message: Message[OutboundAttachment],
    ) -> Message[OutboundAttachment]:
        validate_outbound_message_input(message)
        bcn_session = await self.get_bcn_session(message.session_id)
        if bcn_session is None:
            raise ValueError(f"unknown bcn session: {message.session_id}")
        channel_session = await self.get_channel_session(message.channel_session_id)
        if channel_session is None:
            raise ValueError(f"unknown channel session: {message.channel_session_id}")
        if bcn_session.channel_session_id != message.channel_session_id:
            raise ValueError("outbound message binding does not match bcn session")

        existing = await self.get_outbound_message(message.message_id)
        if existing is None:
            canonical = replace(message, message_id=str(uuid7()))
            validate_outbound_insert(canonical)
            delivery_state = canonical.delivery_state
            if delivery_state is None:
                raise RuntimeError("outbound message has no delivery state")
            await self.execute(
                "INSERT INTO outbound_messages ("
                "agent_id, agent_name, outbound_message_id, command_id, session_id, "
                "channel_session_id, target, reply_to_message_id, body, "
                "attachments_json, "
                "state, snapshot_seq, current_inbound_seq, provider_message_id, "
                "provider_receipt_ref, created_at_ms, provider_attempted_at_ms, "
                "completed_at_ms, error_kind, error_message, metadata_json"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    self._require_agent_id(),
                    self._require_agent_name(),
                    canonical.message_id,
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
                    delivery_state.value,
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
        delivery_state = canonical.delivery_state
        if delivery_state is None:
            raise RuntimeError("outbound message has no delivery state")
        await self.execute(
            "UPDATE outbound_messages SET state = ?, snapshot_seq = ?, "
            "current_inbound_seq = ?, provider_message_id = ?, "
            "provider_receipt_ref = ?, provider_attempted_at_ms = ?, "
            "completed_at_ms = ?, error_kind = ?, error_message = ?, "
            "metadata_json = ? "
            "WHERE outbound_message_id = ?",
            (
                delivery_state.value,
                canonical.snapshot_seq,
                canonical.current_inbound_seq,
                canonical.provider_message_id,
                canonical.provider_receipt_ref,
                canonical.provider_attempted_at_ms,
                canonical.completed_at_ms,
                canonical.error_kind,
                canonical.error_message,
                encode_metadata(canonical.metadata),
                canonical.message_id,
            ),
        )
        return canonical
