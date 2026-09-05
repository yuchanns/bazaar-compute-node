from __future__ import annotations

import json
from dataclasses import replace
from typing import cast
from uuid import uuid7

import aiosqlite

from ....core.inbox import InboxTargetPage
from ....core.models import (
    ChannelSession,
    ChannelTargetKind,
    InboundAttachment,
    InboxTargetSummary,
    Message,
    MessageDirection,
    OutboundAttachment,
    OutboundDeliveryState,
    SenderIdentity,
    SenderKind,
)
from ....core.storage import (
    InboxTargetResolutionError,
    ResolvedInboxTarget,
    UnreadMessageOwner,
)
from ..codec import (
    encode_metadata,
    inbound_attachment_from_row,
    message_from_row,
    thread_from_row,
    validate_inbound_message_input,
    validate_message_input,
    validate_outbound_insert,
    validate_outbound_message_input,
    validate_outbound_update,
)
from .base import RepositoryBase

_MESSAGE_COLUMNS = (
    "message_id, seq, direction, agent_id, thread_id, channel_session_id, "
    "channel, provider_thread_id, provider_message_id, provider_time_ms, "
    "received_at_ms, sender, sender_id, sender_display_name, "
    "message_type, target, target_kind, "
    "reply_to_message_id, body, mentions_agent, notifies_runtime, "
    "provider_payload_ref, command_id, delivery_state, provider_receipt_ref, created_at_ms, "
    "provider_attempted_at_ms, completed_at_ms, error_kind, error_message, "
    "metadata_json, attachments_json"
)

_INBOX_TARGET_CATALOG_CTE = """
WITH latest_message_ranked AS (
    SELECT
        thread_id,
        message_id,
        target,
        sender,
        sender_id,
        sender_display_name,
        provider_time_ms,
        COALESCE(received_at_ms, created_at_ms) AS activity_at_ms,
        ROW_NUMBER() OVER (
            PARTITION BY thread_id
            ORDER BY seq DESC, message_id DESC
        ) AS message_rank
    FROM messages
    WHERE agent_id = /*agent_id*/?
      AND (
          direction = 'inbound'
          OR (
              direction = 'outbound'
              AND delivery_state IN ('queued', 'sent')
          )
      )
),
latest_message AS (
    SELECT
        thread_id,
        message_id,
        target,
        sender,
        sender_id,
        sender_display_name,
        provider_time_ms,
        activity_at_ms
    FROM latest_message_ranked
    WHERE message_rank = 1
),
pending_message AS (
    SELECT message.thread_id, COUNT(*) AS pending_count
    FROM messages AS message
    LEFT JOIN consumer_cursors AS cursor
        ON cursor.thread_id = message.thread_id
    WHERE message.agent_id = /*agent_id*/?
      AND message.direction = 'inbound'
      AND message.notifies_runtime = 1
      AND message.seq > COALESCE(cursor.delivered_through_seq, 0)
    GROUP BY message.thread_id
),
target_catalog AS (
    SELECT
        thread.id AS thread_id,
        COALESCE(
            latest.target,
            channel.target_kind || ':' || channel.id
        ) AS target,
        channel.target_kind AS target_kind,
        COALESCE(pending.pending_count, 0) AS pending_count,
        MAX(
            COALESCE(thread.last_activity_at_ms, 0),
            COALESCE(latest.activity_at_ms, 0),
            COALESCE(channel.last_inbound_at_ms, 0),
            COALESCE(channel.last_outbound_at_ms, 0),
            COALESCE(thread.updated_at_ms, 0),
            COALESCE(channel.updated_at_ms, 0),
            COALESCE(thread.created_at_ms, 0),
            COALESCE(channel.created_at_ms, 0)
        ) AS last_activity_at_ms,
        latest.message_id AS latest_message_id,
        latest.sender AS latest_sender,
        latest.sender_id AS latest_sender_id,
        latest.sender_display_name AS latest_sender_display_name,
        latest.provider_time_ms AS latest_provider_time_ms,
        latest.activity_at_ms AS latest_received_at_ms
    FROM threads AS thread
    JOIN channel_sessions AS channel
        ON channel.agent_id = /*agent_id*/?
       AND channel.id = thread.channel_session_id
    LEFT JOIN latest_message AS latest
        ON latest.thread_id = thread.id
    LEFT JOIN pending_message AS pending
        ON pending.thread_id = thread.id
    WHERE thread.agent_id = /*agent_id*/?
)
"""


def _append_message_filters(
    predicates: list[str],
    parameters: list[object],
    *,
    direction: MessageDirection | None,
    delivery_states: frozenset[OutboundDeliveryState] | None,
) -> None:
    if direction is not None:
        predicates.append("direction = ?")
        parameters.append(direction.value)
    if delivery_states is None or direction is MessageDirection.INBOUND:
        return
    ordered_states = sorted(state.value for state in delivery_states)
    if direction is MessageDirection.OUTBOUND:
        if not ordered_states:
            predicates.append("0")
            return
        predicates.append(
            "delivery_state IN (" + ", ".join("?" for _ in ordered_states) + ")"
        )
    elif ordered_states:
        predicates.append(
            "(direction = 'inbound' OR (direction = 'outbound' "
            "AND delivery_state IN (" + ", ".join("?" for _ in ordered_states) + ")))"
        )
    else:
        predicates.append("direction = 'inbound'")
    parameters.extend(ordered_states)


def _inbox_target_summary_from_row(row: aiosqlite.Row) -> InboxTargetSummary:
    latest_message_id = cast(str | None, row["latest_message_id"])
    latest_sender_name = cast(str | None, row["latest_sender"])
    latest_sender_id = cast(str | None, row["latest_sender_id"])
    latest_sender_display_name = cast(str | None, row["latest_sender_display_name"])
    return InboxTargetSummary(
        target=cast(str, row["target"]),
        thread_id=cast(str, row["thread_id"]),
        target_kind=ChannelTargetKind(cast(str, row["target_kind"])),
        pending_count=cast(int, row["pending_count"]),
        last_activity_at_ms=cast(int, row["last_activity_at_ms"]),
        latest_message_id=latest_message_id,
        latest_sender=(
            SenderIdentity(
                id=latest_sender_id,
                name=latest_sender_name,
                display_name=latest_sender_display_name,
            )
            if latest_sender_name is not None or latest_sender_id is not None
            else None
        ),
        latest_provider_time_ms=cast(int | None, row["latest_provider_time_ms"]),
        latest_received_at_ms=cast(int | None, row["latest_received_at_ms"]),
    )


class MessageOperations(RepositoryBase):
    async def save_message(self, message: Message) -> Message:
        validate_message_input(message)
        if message.direction is MessageDirection.INBOUND:
            if message.sender_kind is SenderKind.SYSTEM:
                return await self._save_system_message_for_agent(
                    cast(Message[InboundAttachment], message),
                    self._require_agent_id(),
                )
            return await self._save_inbound_message(
                cast(Message[InboundAttachment], message)
            )
        return await self._save_outbound_message(
            cast(Message[OutboundAttachment], message)
        )

    async def get_latest_message_seq(
        self,
        thread_id: str,
        *,
        direction: MessageDirection | None = None,
        delivery_states: frozenset[OutboundDeliveryState] | None = None,
    ) -> int:
        predicates = ["agent_id = /*agent_id*/?", "thread_id = ?"]
        parameters: list[object] = [thread_id]
        _append_message_filters(
            predicates,
            parameters,
            direction=direction,
            delivery_states=delivery_states,
        )
        row = await self.fetchone(
            "SELECT COALESCE(MAX(seq), 0) AS latest_seq FROM messages WHERE "
            + " AND ".join(predicates),
            parameters,
        )
        if row is None:
            raise RuntimeError("SQLite latest message sequence query returned no row")
        return cast(int, row["latest_seq"])

    async def count_messages(
        self,
        thread_id: str,
        *,
        after_seq: int | None = None,
        target: str | None = None,
        direction: MessageDirection | None = None,
        delivery_states: frozenset[OutboundDeliveryState] | None = None,
        notifying_only: bool = False,
    ) -> int:
        predicates = ["agent_id = /*agent_id*/?", "thread_id = ?"]
        parameters: list[object] = [thread_id]
        if after_seq is not None:
            predicates.append("seq > ?")
            parameters.append(after_seq)
        if target is not None:
            predicates.append("target = ?")
            parameters.append(target)
        if notifying_only:
            predicates.append("notifies_runtime = 1")
        _append_message_filters(
            predicates,
            parameters,
            direction=direction,
            delivery_states=delivery_states,
        )
        row = await self.fetchone(
            "SELECT COUNT(*) AS message_count FROM messages WHERE "
            + " AND ".join(predicates),
            parameters,
        )
        if row is None:
            raise RuntimeError("SQLite message count query returned no row")
        return cast(int, row["message_count"])

    async def list_inbox_targets(
        self, *, limit: int = 100, offset: int = 0
    ) -> InboxTargetPage:
        total_row = await self.fetchone(
            _INBOX_TARGET_CATALOG_CTE + "SELECT COUNT(*) AS total FROM target_catalog"
        )
        if total_row is None:
            raise RuntimeError("SQLite inbox target count query returned no row")
        rows = await self.fetchall(
            _INBOX_TARGET_CATALOG_CTE
            + "SELECT target, thread_id, target_kind, pending_count, "
            "last_activity_at_ms, latest_message_id, latest_sender, latest_sender_id, "
            "latest_sender_display_name, "
            "latest_provider_time_ms, latest_received_at_ms "
            "FROM target_catalog "
            "ORDER BY last_activity_at_ms DESC, thread_id "
            "LIMIT ? OFFSET ?",
            (limit, offset),
        )
        return InboxTargetPage(
            targets=tuple(_inbox_target_summary_from_row(row) for row in rows),
            total=cast(int, total_row["total"]),
            offset=offset,
        )

    async def resolve_inbox_target(self, raw_target: str) -> ResolvedInboxTarget:
        parameters: tuple[object, ...]
        if raw_target.startswith("#"):
            label, separator, channel_session_id = raw_target.rpartition(":")
            if separator and len(label) > 1 and channel_session_id:
                predicate = "channel.target_kind = 'group' AND channel.id = ?"
                parameters = (channel_session_id,)
            else:
                predicate = "0"
                parameters = ()
        elif raw_target.startswith("dm:@") and len(raw_target) > 4:
            predicate = "channel.target_kind = 'dm' AND channel.target_handle_key = ?"
            parameters = (raw_target[4:].casefold(),)
        else:
            target_kind, separator, channel_session_id = raw_target.partition(":")
            if separator and target_kind in {"dm", "group"} and channel_session_id:
                predicate = "channel.target_kind = ? AND channel.id = ?"
                parameters = (target_kind, channel_session_id)
            else:
                predicate = "0"
                parameters = ()
        rows = await self.fetchall(
            "SELECT thread.id, thread.channel_session_id, thread.workspace_id, "
            "thread.created_at_ms, thread.updated_at_ms, thread.last_activity_at_ms, "
            "thread.metadata_json "
            "FROM threads AS thread "
            "JOIN channel_sessions AS channel "
            "ON channel.agent_id = /*agent_id*/? "
            "AND channel.id = thread.channel_session_id "
            "WHERE thread.agent_id = /*agent_id*/? "
            f"AND ({predicate}) ORDER BY thread.id",
            parameters,
        )
        if len(rows) != 1:
            raise InboxTargetResolutionError(
                "inbox target does not resolve to exactly one owned session"
            )
        target = thread_from_row(rows[0])
        channel_session = cast(
            ChannelSession,
            await self.get_channel_session(target.channel_session_id),
        )
        handle_is_unique = True
        if channel_session.target_handle_key is not None:
            count_row = cast(
                aiosqlite.Row,
                await self.fetchone(
                    "SELECT COUNT(*) AS target_count "
                    "FROM threads AS thread "
                    "JOIN channel_sessions AS channel "
                    "ON channel.agent_id = /*agent_id*/? "
                    "AND channel.id = thread.channel_session_id "
                    "WHERE thread.agent_id = /*agent_id*/? "
                    "AND channel.target_kind = 'dm' "
                    "AND channel.target_handle_key = ?",
                    (channel_session.target_handle_key,),
                ),
            )
            handle_is_unique = cast(int, count_row["target_count"]) == 1
        return ResolvedInboxTarget(
            thread=target,
            channel_session=channel_session,
            handle_is_unique=handle_is_unique,
        )

    async def find_message(
        self,
        channel: str,
        provider_thread_id: str,
        provider_message_id: str,
        *,
        direction: MessageDirection | None = None,
    ) -> Message[InboundAttachment | OutboundAttachment] | None:
        predicates = [
            "agent_id = /*agent_id*/?",
            "channel = ?",
            "provider_thread_id = ?",
            "provider_message_id = ?",
        ]
        parameters: list[object] = [
            channel,
            provider_thread_id,
            provider_message_id,
        ]
        _append_message_filters(
            predicates,
            parameters,
            direction=direction,
            delivery_states=None,
        )
        row = await self._fetch_one_or_conflict(
            f"SELECT {_MESSAGE_COLUMNS} FROM messages WHERE "
            + " AND ".join(predicates)
            + " ORDER BY seq",
            parameters,
            "provider message identity",
        )
        return await self._message_from_row(row) if row is not None else None

    async def list_ready_attachment_paths(self) -> tuple[str, ...]:
        rows = await self.fetchall(
            "SELECT attachment.relative_path FROM inbound_attachments AS attachment "
            "JOIN messages AS message ON message.message_id = attachment.message_id "
            "WHERE message.agent_id = /*agent_id*/? "
            "AND message.direction = 'inbound' AND attachment.state = 'ready' "
            "ORDER BY attachment.attachment_id"
        )
        return tuple(str(row["relative_path"]) for row in rows)

    async def list_unread_message_owners(self) -> tuple[UnreadMessageOwner, ...]:
        predicates = [
            "message.direction = 'inbound'",
            "message.notifies_runtime = 1",
            "message.seq > COALESCE(cursor.delivered_through_seq, 0)",
        ]
        parameters: tuple[object, ...] = ()
        if self.agent_id is not None:
            predicates.append("message.agent_id = ?")
            parameters = (self.agent_id,)
        rows = await self.fetchall(
            "WITH unread_ranked AS (SELECT message.*, "
            "ROW_NUMBER() OVER (PARTITION BY message.agent_id, "
            "message.thread_id ORDER BY message.seq DESC, message.message_id DESC) "
            "AS unread_rank FROM messages AS message "
            "LEFT JOIN consumer_cursors AS cursor "
            "ON cursor.thread_id = message.thread_id WHERE "
            + " AND ".join(predicates)
            + ") SELECT "
            + _MESSAGE_COLUMNS
            + " FROM unread_ranked WHERE unread_rank = 1 "
            "ORDER BY agent_id, thread_id",
            parameters,
        )
        owners: list[UnreadMessageOwner] = []
        for row in rows:
            message = await self._message_from_row(row)
            owners.append(
                UnreadMessageOwner(
                    agent_id=cast(str, row["agent_id"]),
                    trigger_message=cast(Message[InboundAttachment], message),
                )
            )
        return tuple(owners)

    async def list_unread_messages(
        self, *, limit: int
    ) -> tuple[Message[InboundAttachment | OutboundAttachment], ...]:
        rows = await self.fetchall(
            f"SELECT {_MESSAGE_COLUMNS} FROM messages "
            "WHERE agent_id = /*agent_id*/? "
            "AND direction = 'inbound' "
            "AND notifies_runtime = 1 "
            "AND seq > COALESCE(("
            "SELECT delivered_through_seq FROM consumer_cursors "
            "WHERE consumer_cursors.thread_id = messages.thread_id"
            "), 0) "
            "ORDER BY seq DESC LIMIT ?",
            (limit,),
        )
        rows.reverse()
        return tuple([await self._message_from_row(row) for row in rows])

    async def list_messages(
        self,
        thread_id: str,
        *,
        after_seq: int | None = None,
        target: str | None = None,
        around_message_id: str | None = None,
        direction: MessageDirection | None = None,
        delivery_states: frozenset[OutboundDeliveryState] | None = None,
        notifying_only: bool = False,
        latest: bool = False,
        limit: int = 100,
    ) -> tuple[Message[InboundAttachment | OutboundAttachment], ...]:
        predicates = ["agent_id = /*agent_id*/?", "thread_id = ?"]
        parameters: list[object] = [thread_id]
        if after_seq is not None:
            predicates.append("seq > ?")
            parameters.append(after_seq)
        if target is not None:
            predicates.append("target = ?")
            parameters.append(target)
        if notifying_only:
            predicates.append("notifies_runtime = 1")
        _append_message_filters(
            predicates,
            parameters,
            direction=direction,
            delivery_states=delivery_states,
        )
        where_clause = " AND ".join(predicates)
        if around_message_id is None:
            order = "DESC" if latest else "ASC"
            rows = await self.fetchall(
                f"SELECT {_MESSAGE_COLUMNS} FROM messages "
                f"WHERE {where_clause} ORDER BY seq {order} LIMIT ?",
                (*parameters, limit),
            )
            if latest:
                rows.reverse()
            return tuple([await self._message_from_row(row) for row in rows])

        anchor = await self.fetchone(
            f"SELECT seq FROM messages WHERE {where_clause} AND message_id = ?",
            (*parameters, around_message_id),
        )
        if anchor is None:
            raise ValueError(
                f"message not found in requested history: {around_message_id}"
            )
        count_row = await self.fetchone(
            f"SELECT COUNT(*) AS message_count FROM messages WHERE {where_clause}",
            parameters,
        )
        if count_row is None:
            raise RuntimeError("SQLite message history count query returned no row")
        position_row = await self.fetchone(
            f"SELECT COUNT(*) AS anchor_position FROM messages "
            f"WHERE {where_clause} AND seq <= ?",
            (*parameters, cast(int, anchor["seq"])),
        )
        if position_row is None:
            raise RuntimeError("SQLite message anchor position query returned no row")
        message_count = cast(int, count_row["message_count"])
        anchor_position = cast(int, position_row["anchor_position"])
        start_position = max(anchor_position - limit // 2, 1)
        start_position = min(start_position, max(message_count - limit + 1, 1))
        rows = await self.fetchall(
            "WITH filtered AS (SELECT "
            + _MESSAGE_COLUMNS
            + ", ROW_NUMBER() OVER (ORDER BY seq) AS row_number FROM messages "
            + f"WHERE {where_clause}) SELECT {_MESSAGE_COLUMNS} FROM filtered "
            "WHERE row_number BETWEEN ? AND ? ORDER BY row_number",
            (*parameters, start_position, start_position + limit - 1),
        )
        return tuple([await self._message_from_row(row) for row in rows])

    async def _available_message_id(self, message_id: str) -> str:
        """Keep a message id, or scope it to this Agent when another owns it."""

        message_id_row = await self.fetchone(
            "SELECT agent_id FROM messages WHERE message_id = ?",
            (message_id,),
        )
        if message_id_row is not None:
            if message_id_row["agent_id"] == self.agent_id:
                raise ValueError("message id is already bound to another message")
            message_id = self._agent_local_id("message", message_id)
            if (
                await self.fetchone(
                    "SELECT 1 FROM messages WHERE message_id = ?",
                    (message_id,),
                )
                is not None
            ):
                raise ValueError("Agent-scoped message id is already in use")
        return message_id

    async def _resolve_reply(
        self, canonical: Message[InboundAttachment]
    ) -> Message[InboundAttachment]:
        """Point reply_to_message_id at the message it actually refers to."""

        if canonical.reply_to_message_id is not None:
            referenced_id = canonical.reply_to_message_id
            referenced = await self.fetchone(
                "SELECT message_id, thread_id, seq FROM messages "
                "WHERE agent_id = /*agent_id*/? AND message_id = ?",
                (referenced_id,),
            )
            if referenced is None:
                referenced_id = self._agent_local_id("message", referenced_id)
                referenced = await self.fetchone(
                    "SELECT message_id, thread_id, seq FROM messages "
                    "WHERE agent_id = /*agent_id*/? AND message_id = ?",
                    (referenced_id,),
                )
            if referenced is None:
                raise ValueError("reply_to_message_id does not reference a message")
            if referenced["thread_id"] != canonical.thread_id:
                raise ValueError("reply_to_message_id must belong to the same session")
            if cast(int, referenced["seq"]) >= canonical.seq:
                raise ValueError(
                    "reply_to_message_id must reference an earlier message"
                )
            canonical = replace(
                canonical,
                reply_to_message_id=str(referenced["message_id"]),
            )
        return canonical

    async def _scoped_attachments(
        self, canonical: Message[InboundAttachment]
    ) -> tuple[InboundAttachment, ...]:
        """Keep each attachment id, or scope it to this Agent when another owns it."""

        canonical_attachments: list[InboundAttachment] = []
        for attachment in canonical.attachments:
            attachment_id = attachment.attachment_id
            attachment_row = await self.fetchone(
                "SELECT message.agent_id FROM inbound_attachments AS attachment "
                "JOIN messages AS message ON message.message_id = attachment.message_id "
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
        return tuple(canonical_attachments)

    async def _save_inbound_message(
        self,
        message: Message[InboundAttachment],
    ) -> Message[InboundAttachment]:
        validate_inbound_message_input(message)
        thread = await self.get_thread(message.thread_id)
        if thread is None:
            raise ValueError(f"unknown thread: {message.thread_id}")
        channel_session = await self.get_channel_session(thread.channel_session_id)
        if channel_session is None:
            raise ValueError(f"unknown channel session: {thread.channel_session_id}")
        if (
            message.channel_session_id != channel_session.id
            or message.channel != channel_session.channel
            or message.provider_thread_id != channel_session.provider_thread_id
        ):
            raise ValueError("inbound message binding does not match channel session")
        channel = message.channel
        provider_thread_id = message.provider_thread_id
        provider_message_id = message.provider_message_id
        if channel is None or provider_thread_id is None or provider_message_id is None:
            raise RuntimeError("inbound message identity is incomplete")

        existing = await self.find_message(
            channel,
            provider_thread_id,
            provider_message_id,
            direction=MessageDirection.INBOUND,
        )
        if existing is not None:
            return cast(Message[InboundAttachment], existing)

        message_id = await self._available_message_id(message.message_id)
        canonical = replace(
            message,
            message_id=message_id,
            seq=await self._next_message_seq(),
        )
        canonical = await self._resolve_reply(canonical)
        canonical = replace(
            canonical, attachments=await self._scoped_attachments(canonical)
        )
        await self._insert_inbound_message(canonical, self._require_agent_id())
        return canonical

    async def _save_system_message_for_agent(
        self,
        message: Message[InboundAttachment],
        agent_id: str,
    ) -> Message[InboundAttachment]:
        validate_inbound_message_input(message)
        if message.sender_kind is not SenderKind.SYSTEM:
            raise ValueError("system persistence requires a system message")
        binding = await self.fetchone(
            "SELECT thread.channel_session_id, channel.channel, "
            "channel.provider_thread_id, channel.target_kind "
            "FROM threads AS thread JOIN channel_sessions AS channel "
            "ON channel.agent_id = thread.agent_id "
            "AND channel.id = thread.channel_session_id "
            "WHERE thread.agent_id = ? AND thread.id = ?",
            (agent_id, message.thread_id),
        )
        if binding is None:
            raise ValueError(f"unknown thread: {message.thread_id}")
        if (
            message.channel_session_id != binding["channel_session_id"]
            or message.channel != binding["channel"]
            or message.provider_thread_id != binding["provider_thread_id"]
            or message.target_kind.value != binding["target_kind"]
        ):
            raise ValueError("system message binding does not match channel session")
        if (
            await self.fetchone(
                "SELECT 1 FROM messages WHERE message_id = ?",
                (message.message_id,),
            )
            is not None
        ):
            raise ValueError("system message id is already in use")
        canonical = replace(message, seq=await self._next_message_seq())
        await self._insert_inbound_message(canonical, agent_id)
        return canonical

    async def _insert_inbound_message(
        self,
        canonical: Message[InboundAttachment],
        agent_id: str,
    ) -> None:
        await self.execute(
            "INSERT INTO messages ("
            "message_id, seq, direction, agent_id, thread_id, channel_session_id, "
            "channel, provider_thread_id, provider_message_id, provider_time_ms, "
            "received_at_ms, sender, sender_id, sender_display_name, "
            "message_type, target, target_kind, "
            "reply_to_message_id, body, mentions_agent, notifies_runtime, "
            "provider_payload_ref, metadata_json"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                canonical.message_id,
                canonical.seq,
                canonical.direction.value,
                agent_id,
                canonical.thread_id,
                canonical.channel_session_id,
                canonical.channel,
                canonical.provider_thread_id,
                canonical.provider_message_id,
                canonical.provider_time_ms,
                canonical.received_at_ms,
                canonical.sender.name if canonical.sender is not None else None,
                canonical.sender.id if canonical.sender is not None else None,
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

    async def get_message(
        self,
        message_id: str,
        *,
        direction: MessageDirection | None = None,
    ) -> Message[InboundAttachment | OutboundAttachment] | None:
        predicates = ["agent_id = /*agent_id*/?", "message_id = ?"]
        parameters: list[object] = [message_id]
        _append_message_filters(
            predicates,
            parameters,
            direction=direction,
            delivery_states=None,
        )
        row = await self.fetchone(
            f"SELECT {_MESSAGE_COLUMNS} FROM messages WHERE "
            + " AND ".join(predicates),
            parameters,
        )
        return await self._message_from_row(row) if row is not None else None

    async def resolve_message(
        self,
        thread_id: str,
        message_id: str,
        *,
        direction: MessageDirection | None = None,
        delivery_states: frozenset[OutboundDeliveryState] | None = None,
    ) -> Message[InboundAttachment | OutboundAttachment] | None:
        predicates = ["agent_id = /*agent_id*/?", "message_id = ?"]
        parameters: list[object] = [message_id]
        if thread_id:
            predicates.append("thread_id = ?")
            parameters.append(thread_id)
        _append_message_filters(
            predicates,
            parameters,
            direction=direction,
            delivery_states=delivery_states,
        )
        row = await self.fetchone(
            f"SELECT {_MESSAGE_COLUMNS} FROM messages WHERE "
            + " AND ".join(predicates),
            parameters,
        )
        return await self._message_from_row(row) if row is not None else None

    async def get_owned_message(
        self,
        agent_id: str,
        thread_id: str,
        message_id: str,
        *,
        direction: MessageDirection | None = None,
    ) -> Message[InboundAttachment | OutboundAttachment] | None:
        bound_agent_id = self._bound_agent_id()
        if bound_agent_id is not None and bound_agent_id != agent_id:
            return None
        predicates = ["agent_id = ?", "thread_id = ?", "message_id = ?"]
        parameters: list[object] = [agent_id, thread_id, message_id]
        _append_message_filters(
            predicates,
            parameters,
            direction=direction,
            delivery_states=None,
        )
        row = await self.fetchone(
            f"SELECT {_MESSAGE_COLUMNS} FROM messages WHERE "
            + " AND ".join(predicates),
            parameters,
        )
        return await self._message_from_row(row) if row is not None else None

    async def get_latest_message(
        self,
        thread_id: str,
        *,
        direction: MessageDirection | None = None,
        delivery_states: frozenset[OutboundDeliveryState] | None = None,
    ) -> Message[InboundAttachment | OutboundAttachment] | None:
        predicates = ["thread_id = ?"]
        parameters: list[object] = [thread_id]
        agent_predicate = self._agent_predicate()
        _append_message_filters(
            predicates,
            parameters,
            direction=direction,
            delivery_states=delivery_states,
        )
        row = await self.fetchone(
            f"SELECT {_MESSAGE_COLUMNS} FROM messages WHERE {agent_predicate}"
            + " AND ".join(predicates)
            + " ORDER BY seq DESC LIMIT 1",
            parameters,
        )
        return await self._message_from_row(row) if row is not None else None

    async def _message_from_row(
        self,
        row: aiosqlite.Row,
    ) -> Message[InboundAttachment | OutboundAttachment]:
        attachments = (
            await self._attachments(cast(str, row["message_id"]))
            if row["direction"] == MessageDirection.INBOUND.value
            else ()
        )
        return message_from_row(row, attachments)

    async def _attachments(
        self,
        message_id: str,
    ) -> tuple[InboundAttachment, ...]:
        rows = await self.fetchall(
            "SELECT attachment_id, name, kind, state, media_type, relative_path, "
            "size_bytes, error FROM inbound_attachments WHERE message_id = ? "
            "ORDER BY ordinal",
            (message_id,),
        )
        return tuple(inbound_attachment_from_row(row) for row in rows)

    async def _next_message_seq(self) -> int:
        row = await self.fetchone(
            "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM messages"
        )
        if row is None:
            raise RuntimeError("SQLite message sequence query returned no row")
        return cast(int, row["next_seq"])

    async def _insert_outbound(
        self,
        message: Message[OutboundAttachment],
        channel_session: ChannelSession,
    ) -> Message[OutboundAttachment]:
        """Write down an outbound message the first time it is sent."""

        canonical = replace(
            message,
            message_id=str(uuid7()),
            seq=await self._next_message_seq(),
            channel=channel_session.channel,
            provider_thread_id=channel_session.provider_thread_id,
            sender=SenderIdentity(name=self._require_agent_name()),
            target_kind=channel_session.target_kind,
        )
        validate_outbound_insert(canonical)
        delivery_state = canonical.delivery_state
        if delivery_state is None:
            raise RuntimeError("outbound message has no delivery state")
        sender = canonical.sender
        if sender is None:
            raise RuntimeError("outbound message has no sender")
        await self.execute(
            "INSERT INTO messages ("
            "message_id, seq, direction, agent_id, thread_id, "
            "channel_session_id, channel, provider_thread_id, "
            "provider_message_id, sender, sender_id, sender_display_name, "
            "message_type, target, target_kind, "
            "reply_to_message_id, body, command_id, delivery_state, "
            "provider_receipt_ref, created_at_ms, provider_attempted_at_ms, completed_at_ms, "
            "error_kind, error_message, metadata_json, attachments_json"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                canonical.message_id,
                canonical.seq,
                canonical.direction.value,
                self._require_agent_id(),
                canonical.thread_id,
                canonical.channel_session_id,
                canonical.channel,
                canonical.provider_thread_id,
                canonical.provider_message_id,
                sender.name,
                sender.id,
                sender.display_name,
                canonical.message_type,
                canonical.target,
                canonical.target_kind.value,
                canonical.reply_to_message_id,
                canonical.body,
                canonical.command_id,
                delivery_state.value,
                canonical.provider_receipt_ref,
                canonical.created_at_ms,
                canonical.provider_attempted_at_ms,
                canonical.completed_at_ms,
                canonical.error_kind,
                canonical.error_message,
                encode_metadata(canonical.metadata),
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
            ),
        )
        return canonical

    async def _update_outbound(
        self,
        existing: Message[OutboundAttachment],
        message: Message[OutboundAttachment],
    ) -> Message[OutboundAttachment]:
        """Record what became of an outbound message already written down."""

        if (
            existing.command_id != message.command_id
            or existing.thread_id != message.thread_id
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
            "UPDATE messages SET delivery_state = ?, provider_message_id = ?, "
            "provider_receipt_ref = ?, provider_attempted_at_ms = ?, "
            "completed_at_ms = ?, error_kind = ?, error_message = ?, "
            "metadata_json = ? WHERE agent_id = /*agent_id*/? "
            "AND direction = 'outbound' AND message_id = ?",
            (
                delivery_state.value,
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

    async def _save_outbound_message(
        self,
        message: Message[OutboundAttachment],
    ) -> Message[OutboundAttachment]:
        validate_outbound_message_input(message)
        thread = await self.get_thread(message.thread_id)
        if thread is None:
            raise ValueError(f"unknown thread: {message.thread_id}")
        channel_session = await self.get_channel_session(message.channel_session_id)
        if channel_session is None:
            raise ValueError(f"unknown channel session: {message.channel_session_id}")
        if thread.channel_session_id != message.channel_session_id:
            raise ValueError("outbound message binding does not match bcn session")

        existing = cast(
            Message[OutboundAttachment] | None,
            await self.get_message(
                message.message_id,
                direction=MessageDirection.OUTBOUND,
            ),
        )
        if existing is None:
            return await self._insert_outbound(message, channel_session)
        return await self._update_outbound(existing, message)
