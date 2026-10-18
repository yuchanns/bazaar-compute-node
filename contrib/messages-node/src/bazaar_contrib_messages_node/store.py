from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Literal

from bazaar_contrib_messages import MessageSession
from bazaar_contrib_messages.actor import Agent
from bazaar_contrib_messages.actor import Thread as ThreadActor
from bazaar_contrib_messages.inbox import InboxTargetPage
from bazaar_contrib_messages.models import (
    ChannelSession,
    ConsumerCursor,
    InboundAttachment,
    Message,
    OutboundAttachment,
    Thread,
)
from bazaar_contrib_messages.operations import (
    KnownSender,
    MaterializeOutboundResult,
    ReadMessageHistoryResult,
    RecordInboundResult,
    ResolvedInboxTarget,
    UnreadMessageOwner,
)
from bazaar_contrib_messages.results import (
    InboxListResult,
    MessageCheckResult,
    MessageDraft,
    MessageSearchRequest,
    MessageSearchResult,
    MessageSendFreshnessHold,
    OutboundFreshnessPass,
    UnreadSummary,
)
from bazaar_contrib_messages.states import (
    MessageDirection,
    OutboundDeliveryState,
    Review,
)
from bazaar_contrib_storage import Database, Session

from .repository import Repository


class MessageStore:
    """Agent-owned operations with reader, writer and snapshot scopes."""

    def __init__(self, database: Database, owner: Agent, name: str):
        self._database = database
        self.owner = owner
        self.name = name

    async def _run[T](
        self,
        operation: Callable[[Repository], Awaitable[T]],
        *,
        mode: Literal["read", "snapshot", "write", "transaction"] = "read",
        query_budget: float | None = None,
    ) -> T:
        async def transaction(session: Session) -> T:
            return await operation(Repository(session, self.owner, self.name))

        if mode in {"read", "snapshot"}:
            return await self._database.read(
                transaction, snapshot=mode == "snapshot", query_budget=query_budget
            )
        return await self._database.write(
            transaction, transactional=mode == "transaction"
        )

    async def check_messages(
        self, thread_ids: Sequence[ThreadActor], *, checked_at_ms: int
    ) -> tuple[MessageCheckResult, ...]:
        return await self._run(
            lambda repository: repository.check_messages(
                thread_ids, checked_at_ms=checked_at_ms
            ),
            mode="transaction",
        )

    async def read_unread_summary(
        self, thread_id: ThreadActor | None, *, limit: int
    ) -> UnreadSummary:
        return await self._run(
            lambda repository: repository.read_unread_summary(thread_id, limit=limit),
            mode="snapshot",
        )

    async def read_message_history(
        self,
        *,
        raw_target: str,
        around_message_id: str | None,
        limit: int,
        review: Review | None = Review.APPROVED,
    ) -> ReadMessageHistoryResult:
        return await self._run(
            lambda repository: repository.read_message_history(
                raw_target=raw_target,
                around_message_id=around_message_id,
                limit=limit,
                review=review,
            ),
            mode="snapshot",
        )

    async def read_inbox_catalog(
        self,
        *,
        limit: int | None,
        offset: int = 0,
        review: Review | None = Review.APPROVED,
        thread_id: ThreadActor | None = None,
        pending_only: bool = False,
    ) -> InboxListResult:
        return await self._run(
            lambda repository: repository.read_inbox_catalog(
                limit=limit,
                offset=offset,
                review=review,
                thread_id=thread_id,
                pending_only=pending_only,
            ),
            mode="snapshot",
        )

    async def check_outbound_freshness(
        self,
        target_id: ThreadActor,
        *,
        snapshot_seq: int | None,
        payload: MessageDraft,
        draft_replaced: bool,
    ) -> OutboundFreshnessPass | MessageSendFreshnessHold:
        return await self._run(
            lambda repository: repository.check_outbound_freshness(
                target_id,
                snapshot_seq=snapshot_seq,
                payload=payload,
                draft_replaced=draft_replaced,
            ),
            mode="transaction",
        )

    async def materialize_outbound_if_fresh(
        self,
        target_id: ThreadActor,
        expected_target_seq: int,
        *,
        payload: MessageDraft,
        attempted_at_ms: int,
    ) -> MaterializeOutboundResult:
        return await self._run(
            lambda repository: repository.materialize_outbound_if_fresh(
                target_id,
                expected_target_seq,
                payload=payload,
                attempted_at_ms=attempted_at_ms,
            ),
            mode="transaction",
        )

    async def finalize_outbound_delivery(
        self, outbound: Message[OutboundAttachment]
    ) -> Message[OutboundAttachment]:
        return await self._run(
            lambda repository: repository.finalize_outbound_delivery(outbound),
            mode="transaction",
        )

    async def find_channel_session(
        self,
        *,
        channel: str,
        provider_thread_id: str,
        channel_identity: str | None = None,
    ) -> ChannelSession | None:
        return await self._run(
            lambda repository: repository.find_channel_session(
                channel=channel,
                provider_thread_id=provider_thread_id,
                channel_identity=channel_identity,
            )
        )

    async def list_channel_sessions_without_identity(
        self, channel: str
    ) -> tuple[ChannelSession, ...]:
        return await self._run(
            lambda repository: repository.list_channel_sessions_without_identity(
                channel
            )
        )

    async def backfill_channel_identity(
        self, channel_session_id: str, *, channel_identity: str, provider_thread_id: str
    ) -> None:
        return await self._run(
            lambda repository: repository.backfill_channel_identity(
                channel_session_id,
                channel_identity=channel_identity,
                provider_thread_id=provider_thread_id,
            ),
            mode="transaction",
        )

    async def get_channel_session(
        self, channel_session_id: str
    ) -> ChannelSession | None:
        return await self._run(
            lambda repository: repository.get_channel_session(channel_session_id)
        )

    async def get_thread(self, actor: ThreadActor) -> Thread | None:
        return await self._run(lambda repository: repository.get_thread(actor))

    async def find_thread(self, channel_session_id: str) -> Thread | None:
        return await self._run(
            lambda repository: repository.find_thread(channel_session_id)
        )

    async def get_consumer_cursor(self, actor: ThreadActor) -> ConsumerCursor | None:
        return await self._run(lambda repository: repository.get_consumer_cursor(actor))

    async def save_consumer_cursor(self, cursor: ConsumerCursor) -> None:
        return await self._run(
            lambda repository: repository.save_consumer_cursor(cursor), mode="write"
        )

    async def save_channel_session(self, session: ChannelSession) -> None:
        return await self._run(
            lambda repository: repository.save_channel_session(session), mode="write"
        )

    async def write[T](self, operation: Callable[[MessageSession], Awaitable[T]]) -> T:
        return await self._run(operation, mode="write")

    async def save_thread(self, session: Thread) -> None:
        return await self._run(
            lambda repository: repository.save_thread(session), mode="write"
        )

    async def get_latest_message_seq(
        self,
        actor: ThreadActor,
        *,
        direction: MessageDirection | None = None,
        delivery_states: frozenset[OutboundDeliveryState] | None = None,
    ) -> int:
        return await self._run(
            lambda repository: repository.get_latest_message_seq(
                actor, direction=direction, delivery_states=delivery_states
            )
        )

    async def count_messages(
        self,
        actor: ThreadActor,
        *,
        after_seq: int | None = None,
        target: str | None = None,
        direction: MessageDirection | None = None,
        delivery_states: frozenset[OutboundDeliveryState] | None = None,
        notifying_only: bool = False,
    ) -> int:
        return await self._run(
            lambda repository: repository.count_messages(
                actor,
                after_seq=after_seq,
                target=target,
                direction=direction,
                delivery_states=delivery_states,
                notifying_only=notifying_only,
            )
        )

    async def list_thread_ids(
        self, *, thread_id: ThreadActor | None = None
    ) -> tuple[ThreadActor, ...]:
        return await self._run(
            lambda repository: repository.list_thread_ids(thread_id=thread_id)
        )

    async def list_inbox_targets(
        self, *, limit: int | None = 100, offset: int = 0, review: Review | None
    ) -> InboxTargetPage:
        return await self._run(
            lambda repository: repository.list_inbox_targets(
                limit=limit, offset=offset, review=review
            )
        )

    async def resolve_inbox_target(self, raw_target: str) -> ResolvedInboxTarget:
        return await self._run(
            lambda repository: repository.resolve_inbox_target(raw_target)
        )

    async def resolve_inbox_targets(
        self, raw_target: str
    ) -> tuple[ResolvedInboxTarget, ...]:
        return await self._run(
            lambda repository: repository.resolve_inbox_targets(raw_target)
        )

    async def find_known_sender(self, token: str) -> KnownSender | None:
        return await self._run(lambda repository: repository.find_known_sender(token))

    async def find_message(
        self,
        channel: str,
        provider_thread_id: str,
        provider_message_id: str,
        *,
        direction: MessageDirection | None = None,
    ) -> Message[InboundAttachment | OutboundAttachment] | None:
        return await self._run(
            lambda repository: repository.find_message(
                channel, provider_thread_id, provider_message_id, direction=direction
            )
        )

    async def list_ready_attachment_paths(self) -> tuple[str, ...]:
        return await self._run(
            lambda repository: repository.list_ready_attachment_paths()
        )

    async def list_unread_message_owners(self) -> tuple[UnreadMessageOwner, ...]:
        return await self._run(
            lambda repository: repository.list_unread_message_owners()
        )

    async def list_unread_messages(
        self, *, limit: int
    ) -> tuple[Message[InboundAttachment | OutboundAttachment], ...]:
        return await self._run(
            lambda repository: repository.list_unread_messages(limit=limit)
        )

    async def count_unread_messages(self) -> int:
        return await self._run(lambda repository: repository.count_unread_messages())

    async def list_messages(
        self,
        actor: ThreadActor,
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
        return await self._run(
            lambda repository: repository.list_messages(
                actor,
                after_seq=after_seq,
                target=target,
                around_message_id=around_message_id,
                direction=direction,
                delivery_states=delivery_states,
                notifying_only=notifying_only,
                latest=latest,
                limit=limit,
            )
        )

    async def get_message(
        self, message_id: str, *, direction: MessageDirection | None = None
    ) -> Message[InboundAttachment | OutboundAttachment] | None:
        return await self._run(
            lambda repository: repository.get_message(message_id, direction=direction)
        )

    async def resolve_message(
        self,
        actor: ThreadActor | None,
        message_id: str,
        *,
        direction: MessageDirection | None = None,
        delivery_states: frozenset[OutboundDeliveryState] | None = None,
    ) -> Message[InboundAttachment | OutboundAttachment] | None:
        return await self._run(
            lambda repository: repository.resolve_message(
                actor, message_id, direction=direction, delivery_states=delivery_states
            )
        )

    async def get_owned_message(
        self,
        owner: Agent,
        actor: ThreadActor,
        message_id: str,
        *,
        direction: MessageDirection | None = None,
    ) -> Message[InboundAttachment | OutboundAttachment] | None:
        return await self._run(
            lambda repository: repository.get_owned_message(
                owner, actor, message_id, direction=direction
            )
        )

    async def save_message(self, message: Message) -> Message:
        return await self._run(
            lambda repository: repository.save_message(message), mode="write"
        )

    async def search_messages(
        self,
        request: MessageSearchRequest,
        *,
        actor: ThreadActor | None = None,
        review: Review | None = Review.APPROVED,
        query_budget: float | None = None,
    ) -> MessageSearchResult:
        return await self._run(
            lambda repository: repository.search_messages(
                request, actor=actor, review=review
            ),
            mode="snapshot",
            query_budget=query_budget,
        )

    async def update_inbound_attachments(
        self, message_id: str, attachments: tuple[InboundAttachment, ...]
    ) -> None:
        await self._run(
            lambda repository: repository.update_inbound_attachments(
                message_id, attachments
            ),
            mode="transaction",
        )

    async def record_inbound(
        self, message: Message[InboundAttachment], *, now_ms: int, opening: Review
    ) -> RecordInboundResult:
        return await self._run(
            lambda repository: repository.record_inbound(
                message, now_ms=now_ms, opening=opening
            ),
            mode="transaction",
        )
