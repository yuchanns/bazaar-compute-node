from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5

from ....core.models import (
    BcnSession,
    ChannelSession,
    ConsumerCursor,
    MessageDirection,
    RuntimeAttempt,
)
from ..codec import (
    bcn_session_from_row,
    channel_session_from_row,
    consumer_cursor_from_row,
    encode_metadata,
    runtime_attempt_from_row,
    validate_bcn_session_update,
    validate_channel_session_input,
    validate_channel_session_update,
    validate_consumer_cursor_input,
    validate_consumer_cursor_update,
    validate_cursor_bounds,
)
from .base import RepositoryBase


class SessionOperations(RepositoryBase):
    async def find_channel_session(
        self,
        *,
        channel: str,
        provider_thread_id: str,
    ) -> ChannelSession | None:
        row = await self._fetch_one_or_conflict(
            "SELECT id, channel, provider_thread_id, target_kind, following, "
            "created_at_ms, updated_at_ms, last_inbound_at_ms, last_outbound_at_ms, "
            "target_display_name, target_handle, target_handle_key, "
            "provider_identity_ref_json FROM channel_sessions "
            "WHERE agent_id = /*agent_id*/? AND channel = ? "
            "AND provider_thread_id = ? ORDER BY rowid",
            (channel, provider_thread_id),
            "channel provider identity",
        )
        return channel_session_from_row(row) if row is not None else None

    async def get_channel_session(self, session_id: str) -> ChannelSession | None:
        row = await self.fetchone(
            "SELECT id, channel, provider_thread_id, target_kind, following, "
            "created_at_ms, updated_at_ms, last_inbound_at_ms, last_outbound_at_ms, "
            "target_display_name, target_handle, target_handle_key, "
            "provider_identity_ref_json FROM channel_sessions "
            "WHERE agent_id = /*agent_id*/? AND id = ?",
            (session_id,),
        )
        return channel_session_from_row(row) if row is not None else None

    async def get_bcn_session(self, session_id: str) -> BcnSession | None:
        row = await self.fetchone(
            "SELECT id, channel_session_id, workspace_id, created_at_ms, updated_at_ms, "
            "last_activity_at_ms, metadata_json FROM bcn_sessions "
            "WHERE agent_id = /*agent_id*/? AND id = ?",
            (session_id,),
        )
        return bcn_session_from_row(row) if row is not None else None

    async def find_bcn_session(self, channel_session_id: str) -> BcnSession | None:
        row = await self._fetch_one_or_conflict(
            "SELECT id, channel_session_id, workspace_id, created_at_ms, updated_at_ms, "
            "last_activity_at_ms, metadata_json FROM bcn_sessions "
            "WHERE agent_id = /*agent_id*/? AND channel_session_id = ? ORDER BY rowid",
            (channel_session_id,),
            "channel-to-bcn session binding",
        )
        return bcn_session_from_row(row) if row is not None else None

    async def get_runtime_attempt(self, turn_id: str) -> RuntimeAttempt | None:
        row = await self.fetchone(
            "SELECT turn_id, session_id, client_user_message_id, started_at_ms "
            "FROM runtime_attempts WHERE agent_id = /*agent_id*/? AND turn_id = ?",
            (turn_id,),
        )
        return runtime_attempt_from_row(row) if row is not None else None

    async def get_consumer_cursor(self, session_id: str) -> ConsumerCursor | None:
        if await self.get_bcn_session(session_id) is None:
            return None
        row = await self.fetchone(
            "SELECT session_id, delivered_through_seq, last_check_at_ms, "
            "last_read_at_ms, updated_at_ms FROM consumer_cursors WHERE session_id = ?",
            (session_id,),
        )
        return consumer_cursor_from_row(row) if row is not None else None

    async def save_consumer_cursor(self, cursor: ConsumerCursor) -> None:
        validate_consumer_cursor_input(cursor)
        if await self.get_bcn_session(cursor.session_id) is None:
            raise ValueError(f"unknown bcn session: {cursor.session_id}")
        latest_inbound_seq = await self.get_latest_message_seq(
            cursor.session_id,
            direction=MessageDirection.INBOUND,
        )
        validate_cursor_bounds(
            cursor,
            latest_inbound_seq=latest_inbound_seq,
        )
        existing = await self.get_consumer_cursor(cursor.session_id)
        if existing is not None:
            validate_consumer_cursor_update(existing, cursor)
            await self.execute(
                "UPDATE consumer_cursors SET delivered_through_seq = ?, "
                "last_check_at_ms = ?, "
                "last_read_at_ms = ?, updated_at_ms = ? WHERE session_id = ?",
                (
                    cursor.delivered_through_seq,
                    cursor.last_check_at_ms,
                    cursor.last_read_at_ms,
                    cursor.updated_at_ms,
                    cursor.session_id,
                ),
            )
            return
        await self.execute(
            "INSERT INTO consumer_cursors ("
            "session_id, delivered_through_seq, last_check_at_ms, "
            "last_read_at_ms, updated_at_ms"
            ") VALUES (?, ?, ?, ?, ?)",
            (
                cursor.session_id,
                cursor.delivered_through_seq,
                cursor.last_check_at_ms,
                cursor.last_read_at_ms,
                cursor.updated_at_ms,
            ),
        )

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
                "agent_id, id, channel, provider_thread_id, target_kind, following, "
                "provider_identity_ref_json, target_display_name, target_handle, "
                "target_handle_key, created_at_ms, updated_at_ms, "
                "last_inbound_at_ms, last_outbound_at_ms"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    self._require_agent_id(),
                    session.id,
                    session.channel,
                    session.provider_thread_id,
                    session.target_kind.value,
                    int(session.following),
                    encode_metadata(session.metadata),
                    session.target_display_name,
                    session.target_handle,
                    session.target_handle_key,
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
            "provider_identity_ref_json = ?, target_display_name = ?, "
            "target_handle = ?, target_handle_key = ? WHERE id = ?",
            (
                session.target_kind.value,
                int(session.following),
                session.updated_at_ms,
                session.last_inbound_at_ms,
                session.last_outbound_at_ms,
                encode_metadata(session.metadata),
                session.target_display_name,
                session.target_handle,
                session.target_handle_key,
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
                "agent_id, id, channel_session_id, workspace_id, "
                "created_at_ms, updated_at_ms, last_activity_at_ms, "
                "metadata_json"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    self._require_agent_id(),
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
            "(agent_id, turn_id, session_id, client_user_message_id, started_at_ms) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                self._require_agent_id(),
                attempt.turn_id,
                attempt.session_id,
                attempt.client_user_message_id,
                attempt.started_at_ms,
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
