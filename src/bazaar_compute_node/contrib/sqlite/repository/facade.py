from __future__ import annotations

from dataclasses import replace

from ....core.models import (
    BcnSession,
    ChannelSession,
    ChannelTargetKind,
    ConsumerCursor,
    InboundMessage,
)
from ....core.storage import RecordInboundResult
from ....core.storage_operations import StorageOperationMixin
from .handoffs import HandoffOperations
from .messages import MessageOperations
from .reminders import ReminderOperations
from .sessions import SessionOperations


class SqliteRepository(
    StorageOperationMixin,
    SessionOperations,
    MessageOperations,
    ReminderOperations,
    HandoffOperations,
):
    async def record_inbound(
        self,
        message: InboundMessage,
        *,
        now_ms: int,
    ) -> RecordInboundResult:
        existing_message = await self.find_inbound_message(
            message.channel,
            message.provider_thread_id,
            message.provider_message_id,
        )
        if existing_message is not None:
            message = existing_message
        channel_session = await self.find_channel_session(
            channel=message.channel,
            provider_thread_id=message.provider_thread_id,
        )
        channel_session_created = channel_session is None
        if channel_session is None:
            channel_session = ChannelSession(
                id=message.channel_session_id,
                channel=message.channel,
                provider_thread_id=message.provider_thread_id,
                created_at_ms=now_ms,
                updated_at_ms=now_ms,
                target_kind=message.target_kind,
                following=(
                    message.target_kind is ChannelTargetKind.DM
                    or message.mentions_agent
                ),
            )
            await self.save_channel_session(channel_session)
        elif (
            existing_message is None
            and message.mentions_agent
            and not channel_session.following
        ):
            channel_session = replace(
                channel_session,
                following=True,
                updated_at_ms=now_ms,
            )
            await self.save_channel_session(channel_session)

        bcn_session = await self.find_bcn_session(channel_session.id)
        bcn_session_created = bcn_session is None
        if bcn_session is None:
            bcn_session = BcnSession(
                id=message.session_id,
                channel_session_id=channel_session.id,
                workspace_id=self._require_agent_id(),
                created_at_ms=now_ms,
                updated_at_ms=now_ms,
            )
            await self.save_bcn_session(bcn_session)

        if existing_message is None:
            notifies_runtime = message.notifies_runtime and (
                message.target_kind is ChannelTargetKind.DM
                or channel_session.following
                or message.mentions_agent
            )
            canonical_target = message.canonical_target
            if channel_session.id != message.channel_session_id:
                canonical_target = (
                    f"{channel_session.target_kind.value}:{channel_session.id}"
                )
            message = replace(
                message,
                session_id=bcn_session.id,
                channel_session_id=channel_session.id,
                canonical_target=canonical_target,
                notifies_runtime=notifies_runtime,
            )

        if (
            message.notifies_runtime
            and await self.get_consumer_cursor(bcn_session.id) is None
        ):
            await self.save_consumer_cursor(ConsumerCursor(session_id=bcn_session.id))

        if existing_message is None:
            message = await self.append_inbound_message(message)
            channel_session = replace(
                channel_session,
                last_inbound_at_ms=message.received_at_ms,
                updated_at_ms=now_ms,
            )
            bcn_session = replace(
                bcn_session,
                last_activity_at_ms=message.received_at_ms,
                updated_at_ms=now_ms,
            )
            await self.save_channel_session(channel_session)
            await self.save_bcn_session(bcn_session)

        return RecordInboundResult(
            channel_session=channel_session,
            bcn_session=bcn_session,
            message=message,
            channel_session_created=channel_session_created,
            bcn_session_created=bcn_session_created,
            message_created=existing_message is None,
        )
