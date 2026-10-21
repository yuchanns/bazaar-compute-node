from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, replace
from time import time_ns
from typing import cast

from bazaar_contrib_audit import Audit, AuditContext
from bazaar_contrib_channel import ChannelSender, ChannelSendRequest, ChannelTarget
from bazaar_contrib_messages import (
    Actor,
    MessageService,
    MessageSession,
    Thread,
    ThreadConcurrency,
)
from bazaar_contrib_messages.correlation import CorrelationContext
from bazaar_contrib_messages.delivery import from_receipt
from bazaar_contrib_messages.models import ChannelSession, InboundAttachment, Message
from bazaar_contrib_messages.operations import RecordInboundResult
from bazaar_contrib_messages.states import (
    ErrorKind,
    MessageDirection,
    OutboundDeliveryState,
    Review,
    RuntimeEventState,
)
from bazaar_core import Context
from pydantic import JsonValue, TypeAdapter

from .events import CONTACT_REVIEWED, ContactReviewed
from .review import REVIEW_REPLY
from .store import CollaborationStore


class CollaborationService:
    """Manage admission and review for one agent's conversations."""

    def __init__(
        self,
        *,
        messages: MessageService,
        storage: CollaborationStore,
        audit: Audit,
        concurrency: ThreadConcurrency,
        sender: ChannelSender,
        context: Context,
    ):
        self.messages = messages
        self.storage = storage
        self._audit = audit
        self._concurrency = concurrency
        self._sender = sender
        self._context = context

    async def _publish(
        self,
        *,
        event_name: str,
        state: RuntimeEventState,
        correlation: CorrelationContext,
        metadata: Mapping[str, object] | None = None,
        error_kind: ErrorKind | None = None,
        error_message: str | None = None,
    ) -> None:
        # Convert identities only at the shared audit boundary.
        at_ms = time_ns() // 1_000_000
        payload: dict[str, object] = {
            "event_name": event_name,
            "state": state.value,
            "created_at_ms": at_ms,
            "correlation": {
                f"{item.name}_id" if isinstance(value, Actor) else item.name: value.id
                if isinstance(value, Actor)
                else value
                for item in fields(correlation)
                if (value := getattr(correlation, item.name)) is not None
            },
            "metadata": dict(metadata or {}),
        }
        if error_kind is not None:
            payload["error_kind"] = error_kind.value
        if error_message is not None:
            payload["error_message"] = error_message
        await self._audit.publish(
            event_name,
            context=AuditContext(
                agent_id=self.storage.owner.id,
                channel=correlation.channel,
                thread_id=correlation.thread_id,
                message_id=correlation.outbound_message_id,
                session_id=correlation.runtime_session_id,
                turn_id=correlation.turn_id,
            ),
            payload=TypeAdapter(JsonValue).validate_python(payload),
            occurred_at_ms=at_ms,
        )

    async def review_contact(
        self, thread_id: Thread, decision: Review
    ) -> ChannelSession:
        async def update(storage: MessageSession) -> tuple[ChannelSession, Review]:
            thread = await storage.get_thread(thread_id)
            if thread is None:
                raise ValueError(f"thread is not found: {thread_id.id}")
            session = cast(
                ChannelSession,
                await storage.get_channel_session(thread.channel_session_id),
            )
            previous = session.review
            session = replace(
                session,
                review=decision,
                updated_at_ms=max(session.updated_at_ms, now_ms),
            )
            await storage.save_channel_session(session)
            if previous is Review.PENDING and decision is Review.APPROVED:
                await storage.activate_pending_inbound(thread_id)
            return session, previous

        async with self._concurrency.for_thread(thread_id):
            now_ms = time_ns() // 1_000_000
            session, previous = await self.messages.storage.write(update)
            target = await self.messages.storage.resolve_inbox_target(
                session.canonical_target
            )
            await self._publish(
                event_name="channel.session.reviewed",
                state=RuntimeEventState.COMPLETED,
                correlation=CorrelationContext(
                    node_id=self.storage.owner.id,
                    thread_id=thread_id.id,
                    channel=session.channel,
                    channel_session_id=session.id,
                ),
                metadata={
                    "thread_id": thread_id.id,
                    "review": decision.value,
                    "target": session.canonical_target,
                    "target_name": target.display_target,
                },
            )
            if session.review is not previous:
                await self._context.announce(
                    CONTACT_REVIEWED,
                    ContactReviewed(self.storage.owner, thread_id, session.review),
                )
        return session

    async def setting(self, key: str) -> str | None:
        return await self.storage.get_setting(key)

    async def set_setting(self, key: str, value: str) -> None:
        await self.storage.set_setting(key, value, now_ms=time_ns() // 1_000_000)
        # the value is not recorded: it is the operator's words, not a fact
        await self._publish(
            event_name="setting.changed",
            state=RuntimeEventState.COMPLETED,
            correlation=CorrelationContext(
                node_id=self.storage.owner.id,
            ),
            metadata={"key": key},
        )

    async def record_inbound(
        self,
        message: Message[InboundAttachment],
    ) -> RecordInboundResult | None:
        channel, provider_thread_id, provider_message_id = message.inbound_identity()
        delivery_states = {
            OutboundDeliveryState.QUEUED,
            OutboundDeliveryState.SENT,
        }
        if not message.notifies_runtime:
            existing = await self.messages.storage.find_message(
                channel,
                provider_thread_id,
                provider_message_id,
                direction=MessageDirection.OUTBOUND,
            )
            if existing is not None and existing.delivery_state in delivery_states:
                # Quiet context can refer to a message already in readable history.
                return None
        reference = message.metadata.get("provider_reply_to_message_id")
        if message.reply_to_message_id is None and isinstance(reference, str):
            parent = await self.messages.storage.find_message(
                channel,
                provider_thread_id,
                reference,
                direction=MessageDirection.OUTBOUND,
            )
            if parent is None or parent.delivery_state not in delivery_states:
                parent = await self.messages.storage.find_message(
                    channel,
                    provider_thread_id,
                    reference,
                    direction=MessageDirection.INBOUND,
                )
            if parent is not None:
                message = replace(message, reply_to_message_id=parent.message_id)
        recorded = await self.messages.storage.record_inbound(
            message,
            now_ms=time_ns() // 1_000_000,
            opening=(
                Review.APPROVED
                if message.metadata.get("review") == Review.APPROVED.value
                else Review.PENDING
            ),
        )
        message = recorded.message
        if recorded.message_created:
            await self._publish(
                event_name="channel.inbound.persisted",
                state=RuntimeEventState.COMPLETED,
                correlation=CorrelationContext(
                    node_id=self.storage.owner.id,
                    channel=message.channel,
                    channel_session_id=message.channel_session_id,
                    thread_id=message.thread_id,
                    provider_thread_id=message.provider_thread_id,
                    inbound_seq=message.seq,
                ),
                metadata={
                    "notifies_runtime": message.notifies_runtime,
                    "review": recorded.channel_session.review.value,
                    "channel_session_mapping": (
                        "created" if recorded.channel_session_created else "reused"
                    ),
                    "thread_mapping": (
                        "created" if recorded.thread_created else "reused"
                    ),
                    # the sanitizer drops any key called body, and rightly so
                    # for provider payloads; the message text goes by its own name
                    "text": message.body,
                    "target": message.target,
                    "target_kind": message.target_kind.value,
                    "target_name": (
                        None
                        if message.target_presentation is None
                        else message.target_presentation.display_name
                    ),
                    "message_type": message.message_type,
                    "sender": (
                        None
                        if message.sender is None
                        else {
                            "id": message.sender.id,
                            "name": message.sender.name,
                            "display_name": message.sender.display_name,
                            "kind": message.sender_kind.value,
                        }
                    ),
                    "reply_to_message_id": message.reply_to_message_id,
                    "attachments": [
                        {
                            "name": attachment.name,
                            "media_type": attachment.media_type,
                            "size_bytes": attachment.size_bytes,
                        }
                        for attachment in message.attachments
                    ],
                },
            )
        return recorded

    async def answer_unreviewed(
        self, message: Message[InboundAttachment], session: ChannelSession
    ) -> None:
        """Say to a conversation not yet let in what the agent is set to say,
        if anything: the one word it gets, every time it writes."""

        reply = await self.storage.get_setting(REVIEW_REPLY)
        if not reply or message.provider_thread_id is None:
            return
        receipt = await self._sender.send(
            ChannelSendRequest(
                actor_id=message.thread_id,
                body=reply,
                attachments=(),
                target=ChannelTarget(
                    kind=message.target_kind.value, thread_id=message.provider_thread_id
                ),
                channel=message.channel,
                channel_identity=message.channel_identity,
            )
        )
        result = from_receipt(receipt)
        await self._publish(
            event_name="channel.review.replied",
            state=(
                RuntimeEventState.COMPLETED
                if result.state
                in (OutboundDeliveryState.SENT, OutboundDeliveryState.QUEUED)
                else RuntimeEventState.FAILED
            ),
            correlation=CorrelationContext(
                node_id=self.storage.owner.id,
                channel=message.channel,
                channel_session_id=session.id,
                thread_id=message.thread_id,
                inbound_seq=message.seq,
            ),
            error_kind=(
                None
                if result.state
                in (OutboundDeliveryState.SENT, OutboundDeliveryState.QUEUED)
                else ErrorKind.PROVIDER_FAILED
            ),
            error_message=result.error_message,
            metadata={
                "review": session.review.value,
                "delivery_state": result.state.value,
            },
        )
