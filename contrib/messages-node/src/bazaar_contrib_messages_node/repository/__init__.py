from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import replace
from math import log
from typing import Literal, cast
from uuid import uuid7

from bazaar_contrib_messages.actor import Agent
from bazaar_contrib_messages.actor import Thread as ThreadActor
from bazaar_contrib_messages.inbox import InboxTargetPage
from bazaar_contrib_messages.models import (
    ChannelSession,
    ConsumerCursor,
    InboundAttachment,
    Message,
    OutboundAttachment,
    SenderIdentity,
    Thread,
)
from bazaar_contrib_messages.operations import (
    AmbiguousInboxTargetError,
    InboxTargetResolutionError,
    KnownSender,
    RecordInboundResult,
    ResolvedInboxTarget,
    UnreadMessageOwner,
)
from bazaar_contrib_messages.results import (
    MessageSearchHit,
    MessageSearchRequest,
    MessageSearchResult,
    MessageSearchTimeoutError,
)
from bazaar_contrib_messages.states import (
    ChannelTargetKind,
    MessageDirection,
    OutboundDeliveryState,
    Review,
    SenderKind,
)
from bazaar_contrib_storage import (
    All,
    AnyOf,
    Binary,
    Call,
    Case,
    Column,
    Compare,
    Contains,
    Expression,
    In,
    Insert,
    IsNull,
    Join,
    Predicate,
    Record,
    RowId,
    RowNumber,
    Search,
    Select,
    Sort,
    StorageQueryTimeoutError,
    Subquery,
    Update,
    Value,
)

from ..operations import _HISTORY_DELIVERY_STATES, StorageOperationMixin
from ..queries import concurrent_reads
from .base import RepositoryBase, equal
from .codec import (
    channel_session_from_row,
    consumer_cursor_from_row,
    encode_metadata,
    inbox_target_summary_from_row,
    message_from_row,
    thread_from_row,
    validate_channel_session_input,
    validate_channel_session_update,
    validate_consumer_cursor_input,
    validate_consumer_cursor_update,
    validate_cursor_bounds,
    validate_inbound_message_input,
    validate_message_input,
    validate_outbound_insert,
    validate_outbound_message_input,
    validate_outbound_update,
    validate_thread_update,
)
from .unread import (
    _MESSAGE_COLUMNS,
    list_unread_message_owners,
    messages_from_rows,
    unread_query,
)


class Repository(StorageOperationMixin, RepositoryBase):
    async def find_channel_session(
        self,
        *,
        channel: str,
        provider_thread_id: str,
        channel_identity: str | None = None,
    ) -> ChannelSession | None:
        row = await self.one(
            Select(
                table="channel_sessions",
                where=self.scope(
                    equal("channel", channel),
                    equal("channel_identity", channel_identity),
                    equal("provider_thread_id", provider_thread_id),
                ),
            ),
            conflict="channel provider identity",
        )
        return channel_session_from_row(row) if row is not None else None

    async def list_channel_sessions_without_identity(
        self, channel: str
    ) -> tuple[ChannelSession, ...]:
        rows = await self._session.select(
            Select(
                table="channel_sessions",
                where=self.scope(
                    equal("channel", channel), equal("channel_identity", None)
                ),
                order_by=(Sort(expression=RowId()),),
            )
        )
        return tuple(channel_session_from_row(row) for row in rows)

    async def backfill_channel_identity(
        self, channel_session_id: str, *, channel_identity: str, provider_thread_id: str
    ) -> None:
        session = await self.get_channel_session(channel_session_id)
        if session is None or session.channel_identity is not None:
            return
        await self._session.update(
            Update(
                table="channel_sessions",
                values={
                    "channel_identity": Value(value=channel_identity),
                    "provider_thread_id": Value(value=provider_thread_id),
                },
                where=self.scope(equal("id", channel_session_id)),
            )
        )
        # a message is found again by the thread it arrived on, so the ones
        # already written move with their conversation
        await self._session.update(
            Update(
                table="messages",
                values={"provider_thread_id": Value(value=provider_thread_id)},
                where=self.scope(
                    equal("channel_session_id", channel_session_id),
                    equal("provider_thread_id", session.provider_thread_id),
                ),
            )
        )

    async def get_channel_session(
        self, channel_session_id: str
    ) -> ChannelSession | None:
        row = await self.one(
            Select(
                table="channel_sessions",
                where=self.scope(equal("id", channel_session_id)),
            )
        )
        return channel_session_from_row(row) if row is not None else None

    async def get_thread(self, actor: ThreadActor) -> Thread | None:
        row = await self.one(
            Select(table="threads", where=self.scope(equal("id", actor.id)))
        )
        return thread_from_row(row) if row is not None else None

    async def find_thread(self, channel_session_id: str) -> Thread | None:
        row = await self.one(
            Select(
                table="threads",
                where=self.scope(equal("channel_session_id", channel_session_id)),
            ),
            conflict="channel-to-thread binding",
        )
        return thread_from_row(row) if row is not None else None

    async def get_consumer_cursor(self, actor: ThreadActor) -> ConsumerCursor | None:
        if await self.get_thread(actor) is None:
            return None
        row = await self.one(
            Select(table="consumer_cursors", where=equal("thread_id", actor.id))
        )
        return consumer_cursor_from_row(row) if row is not None else None

    async def save_consumer_cursor(self, cursor: ConsumerCursor) -> None:
        validate_consumer_cursor_input(cursor)
        if await self.get_thread(ThreadActor(cursor.thread_id)) is None:
            raise ValueError(f"unknown thread: {cursor.thread_id}")
        latest_inbound_seq = await self.get_latest_message_seq(
            ThreadActor(cursor.thread_id),
            direction=MessageDirection.INBOUND,
        )
        validate_cursor_bounds(
            cursor,
            latest_inbound_seq=latest_inbound_seq,
        )
        existing = await self.get_consumer_cursor(ThreadActor(cursor.thread_id))
        if existing is not None:
            validate_consumer_cursor_update(existing, cursor)
            await self._session.update(
                Update(
                    table="consumer_cursors",
                    values={
                        "delivered_through_seq": Value(
                            value=cursor.delivered_through_seq
                        ),
                        "last_check_at_ms": Value(value=cursor.last_check_at_ms),
                        "last_read_at_ms": Value(value=cursor.last_read_at_ms),
                        "updated_at_ms": Value(value=cursor.updated_at_ms),
                    },
                    where=All(conditions=(equal("thread_id", cursor.thread_id),)),
                )
            )
            return
        await self._session.insert(
            Insert(
                table="consumer_cursors",
                rows=(
                    {
                        "thread_id": cursor.thread_id,
                        "delivered_through_seq": cursor.delivered_through_seq,
                        "last_check_at_ms": cursor.last_check_at_ms,
                        "last_read_at_ms": cursor.last_read_at_ms,
                        "updated_at_ms": cursor.updated_at_ms,
                    },
                ),
            )
        )

    async def save_channel_session(self, session: ChannelSession) -> None:
        validate_channel_session_input(session)
        existing = await self.get_channel_session(session.id)
        if existing is None:
            duplicate = await self.find_channel_session(
                channel=session.channel,
                channel_identity=session.channel_identity,
                provider_thread_id=session.provider_thread_id,
            )
            if duplicate is not None:
                raise ValueError(
                    f"channel provider identity is already bound to {duplicate.id}"
                )
            await self._session.insert(
                Insert(
                    table="channel_sessions",
                    rows=(
                        {
                            "agent_id": self.owner.id,
                            "id": session.id,
                            "channel": session.channel,
                            "channel_identity": session.channel_identity,
                            "provider_thread_id": session.provider_thread_id,
                            "target_kind": session.target_kind.value,
                            "following": int(session.following),
                            "review": session.review.value,
                            "provider_identity_ref_json": encode_metadata(
                                session.metadata
                            ),
                            "target_display_name": session.target_display_name,
                            "target_handle": session.target_handle,
                            "target_handle_key": session.target_handle_key,
                            "created_at_ms": session.created_at_ms,
                            "updated_at_ms": session.updated_at_ms,
                            "last_inbound_at_ms": session.last_inbound_at_ms,
                            "last_outbound_at_ms": session.last_outbound_at_ms,
                        },
                    ),
                )
            )
            return

        session = validate_channel_session_update(existing, session)
        await self._session.update(
            Update(
                table="channel_sessions",
                values={
                    "target_kind": Value(value=session.target_kind.value),
                    "following": Value(value=int(session.following)),
                    "review": Value(value=session.review.value),
                    "updated_at_ms": Value(value=session.updated_at_ms),
                    "last_inbound_at_ms": Value(value=session.last_inbound_at_ms),
                    "last_outbound_at_ms": Value(value=session.last_outbound_at_ms),
                    "provider_identity_ref_json": Value(
                        value=encode_metadata(session.metadata)
                    ),
                    "target_display_name": Value(value=session.target_display_name),
                    "target_handle": Value(value=session.target_handle),
                    "target_handle_key": Value(value=session.target_handle_key),
                },
                where=All(conditions=(equal("id", session.id),)),
            )
        )

    async def save_thread(self, session: Thread) -> None:
        self._require_workspace(session.workspace_id)
        channel_session = await self.get_channel_session(session.channel_session_id)
        if channel_session is None:
            raise ValueError(f"unknown channel session: {session.channel_session_id}")

        existing = await self.get_thread(ThreadActor(session.id))
        if existing is None:
            duplicate = await self.find_thread(session.channel_session_id)
            if duplicate is not None:
                raise ValueError(f"channel session is already bound to {duplicate.id}")
            await self._session.insert(
                Insert(
                    table="threads",
                    rows=(
                        {
                            "agent_id": self.owner.id,
                            "id": session.id,
                            "channel_session_id": session.channel_session_id,
                            "workspace_id": session.workspace_id,
                            "created_at_ms": session.created_at_ms,
                            "updated_at_ms": session.updated_at_ms,
                            "last_activity_at_ms": session.last_activity_at_ms,
                            "metadata_json": encode_metadata(session.metadata),
                        },
                    ),
                )
            )
            return

        session = validate_thread_update(existing, session)
        await self._session.update(
            Update(
                table="threads",
                values={
                    "updated_at_ms": Value(value=session.updated_at_ms),
                    "last_activity_at_ms": Value(value=session.last_activity_at_ms),
                    "metadata_json": Value(value=encode_metadata(session.metadata)),
                },
                where=All(conditions=(equal("id", session.id),)),
            )
        )

    async def get_latest_message_seq(
        self,
        actor: ThreadActor,
        *,
        direction: MessageDirection | None = None,
        delivery_states: frozenset[OutboundDeliveryState] | None = None,
    ) -> int:
        rows = await self._session.select(
            Select(
                table="messages",
                columns={
                    "latest_seq": Call(
                        name="coalesce",
                        arguments=(
                            Call(name="max", arguments=(Column(name="seq"),)),
                            Value(value=0),
                        ),
                    )
                },
                where=self._message_filters(
                    actor, direction=direction, delivery_states=delivery_states
                ),
            )
        )
        return cast(int, rows[0]["latest_seq"])

    def _message_filters(
        self,
        actor: ThreadActor | None = None,
        *,
        after_seq: int | None = None,
        target: str | None = None,
        direction: MessageDirection | None = None,
        delivery_states: frozenset[OutboundDeliveryState] | None = None,
        notifying_only: bool = False,
        table: str | None = None,
    ) -> All:
        predicates: list[Predicate] = []
        if actor is not None:
            predicates.append(equal("thread_id", actor.id, table))
        if after_seq is not None:
            predicates.append(
                Compare(
                    left=Column(name="seq", table=table),
                    operator=">",
                    right=Value(value=after_seq),
                )
            )
        if target is not None:
            predicates.append(equal("target", target, table))
        if notifying_only:
            predicates.append(equal("notifies_runtime", 1, table))
        if direction is not None:
            predicates.append(equal("direction", direction.value, table))
        if delivery_states is not None and direction is not MessageDirection.INBOUND:
            states = In(
                expression=Column(name="delivery_state", table=table),
                values=tuple(sorted(state.value for state in delivery_states)),
            )
            predicates.append(
                states
                if direction is MessageDirection.OUTBOUND
                else AnyOf(
                    conditions=(
                        equal("direction", "inbound", table),
                        All(conditions=(equal("direction", "outbound", table), states)),
                    )
                )
            )
        return self.scope(*predicates, table=table)

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
        return await self.count(
            Select(
                table="messages",
                columns={"total": Call(name="count")},
                where=self._message_filters(
                    actor,
                    after_seq=after_seq,
                    target=target,
                    direction=direction,
                    delivery_states=delivery_states,
                    notifying_only=notifying_only,
                ),
            )
        )

    async def list_thread_ids(
        self, *, thread_id: ThreadActor | None = None
    ) -> tuple[ThreadActor, ...]:
        rows = await self._session.select(
            Select(
                table="threads",
                alias="thread",
                columns={"id": Column(name="id", table="thread")},
                joins=self._channel_join(),
                where=self.scope(
                    equal("review", "approved", "channel"),
                    *((equal("id", thread_id.id, "thread"),) if thread_id else ()),
                    table="thread",
                ),
                order_by=(Sort(expression=Column(name="id", table="thread")),),
            )
        )
        return tuple(ThreadActor(cast(str, row["id"])) for row in rows)

    def _channel_join(self) -> tuple[Join, ...]:
        return (
            Join(
                table="channel_sessions",
                alias="channel",
                on=All(
                    conditions=(
                        equal("agent_id", self.owner.id, "channel"),
                        Compare(
                            left=Column(name="id", table="channel"),
                            operator="=",
                            right=Column(name="channel_session_id", table="thread"),
                        ),
                    )
                ),
            ),
        )

    async def list_inbox_targets(
        self,
        *,
        limit: int | None = 100,
        offset: int = 0,
        review: Review | None,
        thread_id: ThreadActor | None = None,
        pending_only: bool = False,
    ) -> InboxTargetPage:
        ranked = Select(
            table="messages",
            columns={
                **{
                    name: Column(name=name)
                    for name in (
                        "thread_id",
                        "message_id",
                        "target",
                        "sender",
                        "sender_id",
                        "sender_display_name",
                        "provider_time_ms",
                    )
                },
                "activity_at_ms": Call(
                    name="coalesce",
                    arguments=(
                        Column(name="received_at_ms"),
                        Column(name="created_at_ms"),
                    ),
                ),
                "message_rank": RowNumber(
                    partition_by=(Column(name="thread_id"),),
                    order_by=(
                        Sort(expression=Column(name="seq"), descending=True),
                        Sort(expression=Column(name="message_id"), descending=True),
                    ),
                ),
            },
            where=self._message_filters(delivery_states=_HISTORY_DELIVERY_STATES),
        )
        latest = Select(table="latest_message_ranked", where=equal("message_rank", 1))
        pending = self._unread_query().model_copy(
            update={
                "columns": {
                    "thread_id": Column(name="thread_id", table="message"),
                    "pending_count": Call(name="count"),
                },
                "group_by": (Column(name="thread_id", table="message"),),
            }
        )
        catalog = Select(
            table="threads",
            alias="thread",
            columns={
                "thread_id": Column(name="id", table="thread"),
                "target": Call(
                    name="coalesce",
                    arguments=(
                        Column(name="target", table="latest"),
                        Binary(
                            operator="||",
                            left=Binary(
                                operator="||",
                                left=Column(name="target_kind", table="channel"),
                                right=Value(value=":"),
                            ),
                            right=Column(name="id", table="channel"),
                        ),
                    ),
                ),
                **{
                    name: Column(name=name, table="channel")
                    for name in ("target_kind", "channel", "review")
                },
                "pending_count": Call(
                    name="coalesce",
                    arguments=(
                        Column(name="pending_count", table="pending"),
                        Value(value=0),
                    ),
                ),
                "last_activity_at_ms": Call(
                    name="max",
                    arguments=tuple(
                        Call(
                            name="coalesce",
                            arguments=(Column(name=name, table=table), Value(value=0)),
                        )
                        for table, name in (
                            ("thread", "last_activity_at_ms"),
                            ("latest", "activity_at_ms"),
                            ("channel", "last_inbound_at_ms"),
                            ("channel", "last_outbound_at_ms"),
                            ("thread", "updated_at_ms"),
                            ("channel", "updated_at_ms"),
                            ("thread", "created_at_ms"),
                            ("channel", "created_at_ms"),
                        )
                    ),
                ),
                **{
                    "latest_" + name: Column(name=name, table="latest")
                    for name in (
                        "message_id",
                        "sender",
                        "sender_id",
                        "sender_display_name",
                        "provider_time_ms",
                    )
                },
                "latest_received_at_ms": Column(name="activity_at_ms", table="latest"),
            },
            joins=(
                *self._channel_join(),
                *(
                    Join(
                        table=table,
                        alias=alias,
                        kind="left",
                        on=Compare(
                            left=Column(name="thread_id", table=alias),
                            operator="=",
                            right=Column(name="id", table="thread"),
                        ),
                    )
                    for table, alias in (
                        ("latest_message", "latest"),
                        ("pending_message", "pending"),
                    )
                ),
            ),
            where=self.scope(
                *((equal("id", thread_id.id, "thread"),) if thread_id else ()),
                *(
                    (
                        Compare(
                            left=Column(name="pending_count", table="pending"),
                            operator=">",
                            right=Value(value=0),
                        ),
                    )
                    if pending_only
                    else ()
                ),
                table="thread",
            ),
        )
        ctes = {
            "latest_message_ranked": ranked,
            "latest_message": latest,
            "pending_message": pending,
            "target_catalog": catalog,
        }
        counts = await self._session.select(
            Select(
                table="target_catalog",
                ctes=ctes,
                columns={"review": Column(name="review"), "total": Call(name="count")},
                group_by=(Column(name="review"),),
            )
        )
        by_state = {
            Review(cast(str, row["review"])): cast(int, row["total"]) for row in counts
        }
        rows = await self._session.select(
            Select(
                table="target_catalog",
                ctes=ctes,
                where=equal("review", review.value) if review is not None else None,
                order_by=(
                    Sort(
                        expression=Column(name="last_activity_at_ms"), descending=True
                    ),
                    Sort(expression=Column(name="thread_id")),
                ),
                limit=limit,
                offset=offset,
            )
        )
        return InboxTargetPage(
            targets=tuple(inbox_target_summary_from_row(row) for row in rows),
            total=sum(by_state.values()) if review is None else by_state.get(review, 0),
            offset=offset,
            pending_review=by_state.get(Review.PENDING, 0),
        )

    async def resolve_inbox_target(self, raw_target: str) -> ResolvedInboxTarget:
        target, *others = await self.resolve_inbox_targets(raw_target)
        if others:
            raise AmbiguousInboxTargetError(
                "inbox target resolves to more than one owned session"
            )
        return target

    async def resolve_inbox_targets(
        self, raw_target: str
    ) -> tuple[ResolvedInboxTarget, ...]:
        predicates: tuple[Predicate, ...] = (
            In(expression=Column(name="id", table="channel"), values=()),
        )
        if raw_target.startswith("#"):
            label, separator, channel_session_id = raw_target.rpartition(":")
            if separator and len(label) > 1 and channel_session_id:
                predicates = (
                    equal("target_kind", "group", "channel"),
                    equal("id", channel_session_id, "channel"),
                )
        elif raw_target.startswith("dm:@") and len(raw_target) > 4:
            predicates = (
                equal("target_kind", "dm", "channel"),
                equal("target_handle_key", raw_target[4:].casefold(), "channel"),
            )
        else:
            target_kind, separator, channel_session_id = raw_target.partition(":")
            if separator and target_kind in {"dm", "group"} and channel_session_id:
                predicates = (
                    equal("target_kind", target_kind, "channel"),
                    equal("id", channel_session_id, "channel"),
                )
        columns: dict[str, Expression] = {
            name: Column(name=name, table="thread")
            for name in (
                "id",
                "channel_session_id",
                "workspace_id",
                "created_at_ms",
                "updated_at_ms",
                "last_activity_at_ms",
                "metadata_json",
            )
        }
        fields = (
            "id",
            "channel",
            "provider_thread_id",
            "created_at_ms",
            "updated_at_ms",
            "channel_identity",
            "target_kind",
            "following",
            "review",
            "last_inbound_at_ms",
            "last_outbound_at_ms",
            "target_display_name",
            "target_handle",
            "target_handle_key",
            "provider_identity_ref_json",
        )
        columns.update(
            {"channel_" + name: Column(name=name, table="channel") for name in fields}
        )
        columns["handle_count"] = Case(
            when=IsNull(expression=Column(name="target_handle_key", table="channel")),
            then=Value(value=1),
            otherwise=Subquery(
                query=Select(
                    table="threads",
                    alias="matched_thread",
                    columns={"total": Call(name="count")},
                    joins=(
                        Join(
                            table="channel_sessions",
                            alias="matched_channel",
                            on=All(
                                conditions=(
                                    equal("agent_id", self.owner.id, "matched_channel"),
                                    Compare(
                                        left=Column(name="id", table="matched_channel"),
                                        operator="=",
                                        right=Column(
                                            name="channel_session_id",
                                            table="matched_thread",
                                        ),
                                    ),
                                )
                            ),
                        ),
                    ),
                    where=self.scope(
                        equal("target_kind", "dm", "matched_channel"),
                        Compare(
                            left=Column(
                                name="target_handle_key", table="matched_channel"
                            ),
                            operator="=",
                            right=Column(name="target_handle_key", table="channel"),
                        ),
                        table="matched_thread",
                    ),
                )
            ),
        )
        rows = await self._session.select(
            Select(
                table="threads",
                alias="thread",
                columns=columns,
                joins=self._channel_join(),
                where=self.scope(*predicates, table="thread"),
                order_by=(Sort(expression=Column(name="id", table="thread")),),
            )
        )
        if not rows:
            raise InboxTargetResolutionError(
                "inbox target does not resolve to an owned session"
            )
        return tuple(
            ResolvedInboxTarget(
                thread=thread_from_row(row),
                channel_session=channel_session_from_row(
                    {name: row["channel_" + name] for name in fields}
                ),
                handle_is_unique=row["handle_count"] == 1,
            )
            for row in rows
        )

    async def find_known_sender(self, token: str) -> KnownSender | None:
        # A handle precedes a provider id; a display name is never an address.
        for predicate in (
            Compare(
                left=Call(name="lower", arguments=(Column(name="sender"),)),
                operator="=",
                right=Call(name="lower", arguments=(Value(value=token),)),
            ),
            equal("sender_id", token),
        ):
            row = await self.one(
                Select(
                    table="messages",
                    where=self.scope(equal("direction", "inbound"), predicate),
                    order_by=(Sort(expression=Column(name="seq"), descending=True),),
                    limit=1,
                )
            )
            if row is None:
                continue
            channel = await self.get_channel_session(
                cast(str, row["channel_session_id"])
            )
            if channel is None:
                continue
            sender = cast(str | None, row["sender"])
            sender_id = cast(str | None, row["sender_id"])
            if sender is None and sender_id is None:
                continue
            metadata_json = cast(str | None, row["metadata_json"])
            sender_kind = SenderKind.UNKNOWN
            if metadata_json:
                sender_kind = SenderKind(
                    json.loads(metadata_json).get(
                        "sender_kind", SenderKind.UNKNOWN.value
                    )
                )
            return KnownSender(
                sender=SenderIdentity(
                    id=sender_id,
                    name=sender,
                    display_name=cast(str | None, row["sender_display_name"]),
                ),
                channel=channel.channel,
                channel_identity=channel.channel_identity,
                sender_kind=sender_kind,
            )
        return None

    async def find_message(
        self,
        channel: str,
        provider_thread_id: str,
        provider_message_id: str,
        *,
        direction: MessageDirection | None = None,
    ) -> Message[InboundAttachment | OutboundAttachment] | None:
        row = await self.one(
            Select(
                table="messages",
                where=All(
                    conditions=(
                        self._message_filters(direction=direction),
                        equal("channel", channel),
                        equal("provider_thread_id", provider_thread_id),
                        equal("provider_message_id", provider_message_id),
                    )
                ),
                order_by=(Sort(expression=Column(name="seq")),),
            ),
            conflict="provider message identity",
        )
        return await self._message_from_row(row) if row is not None else None

    async def update_inbound_attachments(
        self, message_id: str, attachments: tuple[InboundAttachment, ...]
    ) -> None:
        for attachment in attachments:
            await self._session.update(
                Update(
                    table="inbound_attachments",
                    values={
                        "state": Value(value=attachment.state),
                        "relative_path": Value(value=attachment.relative_path),
                        "size_bytes": Value(value=attachment.size_bytes),
                        "error": Value(value=attachment.error),
                    },
                    where=All(
                        conditions=(
                            equal("attachment_id", attachment.attachment_id),
                            Compare(
                                left=Column(name="message_id"),
                                operator="=",
                                right=Subquery(
                                    query=Select(
                                        table="messages",
                                        columns={
                                            "message_id": Column(name="message_id")
                                        },
                                        where=self.scope(
                                            equal("message_id", message_id),
                                            equal("direction", "inbound"),
                                        ),
                                    )
                                ),
                            ),
                        )
                    ),
                )
            )

    async def list_ready_attachment_paths(self) -> tuple[str, ...]:
        rows = await self._session.select(
            Select(
                table="inbound_attachments",
                alias="attachment",
                columns={
                    "relative_path": Column(name="relative_path", table="attachment")
                },
                joins=(
                    Join(
                        table="messages",
                        alias="message",
                        on=Compare(
                            left=Column(name="message_id", table="message"),
                            operator="=",
                            right=Column(name="message_id", table="attachment"),
                        ),
                    ),
                ),
                where=self.scope(
                    equal("direction", "inbound", "message"),
                    equal("state", "ready", "attachment"),
                    table="message",
                ),
                order_by=(
                    Sort(expression=Column(name="attachment_id", table="attachment")),
                ),
            )
        )
        return tuple(str(row["relative_path"]) for row in rows)

    def _unread_query(self) -> Select:
        return unread_query(self.owner)

    async def list_unread_message_owners(self) -> tuple[UnreadMessageOwner, ...]:
        return await list_unread_message_owners(self._session, self.owner)

    async def list_unread_messages(
        self, *, limit: int
    ) -> tuple[Message[InboundAttachment | OutboundAttachment], ...]:
        rows = await self._session.select(
            self._unread_query().model_copy(
                update={
                    "order_by": (
                        Sort(
                            expression=Column(name="seq", table="message"),
                            descending=True,
                        ),
                    ),
                    "limit": limit,
                }
            )
        )
        return await self._messages_from_rows(tuple(reversed(rows)))

    async def count_unread_messages(self) -> int:
        return await self.count(
            self._unread_query().model_copy(
                update={"columns": {"total": Call(name="count")}}
            )
        )

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
        where = self._message_filters(
            actor,
            after_seq=after_seq,
            target=target,
            direction=direction,
            delivery_states=delivery_states,
            notifying_only=notifying_only,
        )
        offset = 0
        if around_message_id is not None:
            anchor = await self.one(
                Select(
                    table="messages",
                    where=All(
                        conditions=(where, equal("message_id", around_message_id))
                    ),
                )
            )
            if anchor is None:
                raise ValueError(
                    f"message not found in requested history: {around_message_id}"
                )
            message_count = await self.count(
                Select(
                    table="messages", columns={"total": Call(name="count")}, where=where
                )
            )
            anchor_position = await self.count(
                Select(
                    table="messages",
                    columns={"total": Call(name="count")},
                    where=All(
                        conditions=(
                            where,
                            Compare(
                                left=Column(name="seq"),
                                operator="<=",
                                right=Value(value=anchor["seq"]),
                            ),
                        )
                    ),
                )
            )
            start_position = max(anchor_position - limit // 2, 1)
            start_position = min(start_position, max(message_count - limit + 1, 1))
            offset = start_position - 1
        rows = await self._session.select(
            Select(
                table="messages",
                where=where,
                order_by=(
                    Sort(
                        expression=Column(name="seq"),
                        descending=latest and around_message_id is None,
                    ),
                ),
                limit=limit,
                offset=offset,
            )
        )
        if latest and around_message_id is None:
            rows = tuple(reversed(rows))
        return await self._messages_from_rows(rows)

    async def get_message(
        self, message_id: str, *, direction: MessageDirection | None = None
    ) -> Message[InboundAttachment | OutboundAttachment] | None:
        row = await self.one(
            Select(
                table="messages",
                where=All(
                    conditions=(
                        self._message_filters(direction=direction),
                        equal("message_id", message_id),
                    )
                ),
            )
        )
        return await self._message_from_row(row) if row is not None else None

    async def resolve_message(
        self,
        actor: ThreadActor | None,
        message_id: str,
        *,
        direction: MessageDirection | None = None,
        delivery_states: frozenset[OutboundDeliveryState] | None = None,
    ) -> Message[InboundAttachment | OutboundAttachment] | None:
        row = await self.one(
            Select(
                table="messages",
                where=All(
                    conditions=(
                        self._message_filters(
                            actor, direction=direction, delivery_states=delivery_states
                        ),
                        equal("message_id", message_id),
                    )
                ),
            )
        )
        return await self._message_from_row(row) if row is not None else None

    async def get_owned_message(
        self,
        owner: Agent,
        actor: ThreadActor,
        message_id: str,
        *,
        direction: MessageDirection | None = None,
    ) -> Message[InboundAttachment | OutboundAttachment] | None:
        if self.owner != owner:
            return None
        return await self.resolve_message(actor, message_id, direction=direction)

    async def save_message(self, message: Message) -> Message:
        validate_message_input(message)
        if message.direction is MessageDirection.INBOUND:
            if message.sender_kind is SenderKind.SYSTEM:
                return await self._save_system_message_for_agent(
                    cast(Message[InboundAttachment], message),
                    self.owner,
                )
            return await self._save_inbound_message(
                cast(Message[InboundAttachment], message)
            )
        return await self._save_outbound_message(
            cast(Message[OutboundAttachment], message)
        )

    async def _available_message_id(self, message_id: str) -> str:
        """Keep a message id, or scope it to this Agent when another owns it."""

        message_id_row = await self.one(
            Select(table="messages", where=equal("message_id", message_id))
        )
        if message_id_row is not None:
            if message_id_row["agent_id"] == self.owner.id:
                raise ValueError("message id is already bound to another message")
            message_id = self._agent_local_id("message", message_id)
            if (
                await self.one(
                    Select(table="messages", where=equal("message_id", message_id))
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
            referenced = await self.one(
                Select(
                    table="messages",
                    where=self.scope(equal("message_id", referenced_id)),
                )
            )
            if referenced is None:
                referenced_id = self._agent_local_id("message", referenced_id)
                referenced = await self.one(
                    Select(
                        table="messages",
                        where=self.scope(equal("message_id", referenced_id)),
                    )
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
            attachment_row = await self.one(
                Select(
                    table="inbound_attachments",
                    alias="attachment",
                    columns={"agent_id": Column(name="agent_id", table="message")},
                    joins=(
                        Join(
                            table="messages",
                            alias="message",
                            on=Compare(
                                left=Column(name="message_id", table="message"),
                                operator="=",
                                right=Column(name="message_id", table="attachment"),
                            ),
                        ),
                    ),
                    where=equal("attachment_id", attachment_id, "attachment"),
                )
            )
            if attachment_row is not None:
                if attachment_row["agent_id"] == self.owner.id:
                    raise ValueError("attachment id is already in use by this Agent")
                attachment_id = self._agent_local_id("attachment", attachment_id)
                if (
                    await self.one(
                        Select(
                            table="inbound_attachments",
                            where=equal("attachment_id", attachment_id),
                        )
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
        thread = await self.get_thread(ThreadActor(message.thread_id))
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
        await self._insert_inbound_message(canonical, self.owner)
        return canonical

    async def _save_system_message_for_agent(
        self,
        message: Message[InboundAttachment],
        agent: Agent,
    ) -> Message[InboundAttachment]:
        validate_inbound_message_input(message)
        if message.sender_kind is not SenderKind.SYSTEM:
            raise ValueError("system persistence requires a system message")
        binding = await self.one(
            Select(
                table="threads",
                alias="thread",
                columns={
                    "channel_session_id": Column(
                        name="channel_session_id", table="thread"
                    ),
                    **{
                        name: Column(name=name, table="channel")
                        for name in ("channel", "provider_thread_id", "target_kind")
                    },
                },
                joins=self._channel_join(),
                where=All(
                    conditions=(
                        equal("agent_id", agent.id, "thread"),
                        equal("id", message.thread_id, "thread"),
                    )
                ),
            )
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
            await self.one(
                Select(table="messages", where=equal("message_id", message.message_id))
            )
            is not None
        ):
            raise ValueError("system message id is already in use")
        canonical = replace(message, seq=await self._next_message_seq())
        await self._insert_inbound_message(canonical, agent)
        return canonical

    async def _insert_inbound_message(
        self,
        canonical: Message[InboundAttachment],
        agent: Agent,
    ) -> None:
        await self._session.insert(
            Insert(
                table="messages",
                rows=(
                    {
                        "message_id": canonical.message_id,
                        "seq": canonical.seq,
                        "direction": canonical.direction.value,
                        "agent_id": agent.id,
                        "thread_id": canonical.thread_id,
                        "channel_session_id": canonical.channel_session_id,
                        "channel": canonical.channel,
                        "provider_thread_id": canonical.provider_thread_id,
                        "provider_message_id": canonical.provider_message_id,
                        "provider_time_ms": canonical.provider_time_ms,
                        "received_at_ms": canonical.received_at_ms,
                        "sender": canonical.sender.name
                        if canonical.sender is not None
                        else None,
                        "sender_id": canonical.sender.id
                        if canonical.sender is not None
                        else None,
                        "sender_display_name": canonical.sender.display_name
                        if canonical.sender is not None
                        else None,
                        "message_type": canonical.message_type,
                        "target": canonical.target,
                        "target_kind": canonical.target_kind.value,
                        "reply_to_message_id": canonical.reply_to_message_id,
                        "body": canonical.body,
                        "mentions_agent": int(canonical.mentions_agent),
                        "notifies_runtime": int(canonical.notifies_runtime),
                        "provider_payload_ref": canonical.provider_payload_ref,
                        "metadata_json": encode_metadata(canonical.metadata),
                    },
                ),
            )
        )
        for ordinal, attachment in enumerate(canonical.attachments):
            await self._session.insert(
                Insert(
                    table="inbound_attachments",
                    rows=(
                        {
                            "attachment_id": attachment.attachment_id,
                            "message_id": canonical.message_id,
                            "ordinal": ordinal,
                            "name": attachment.name,
                            "kind": attachment.kind,
                            "state": attachment.state,
                            "media_type": attachment.media_type,
                            "relative_path": attachment.relative_path,
                            "size_bytes": attachment.size_bytes,
                            "error": attachment.error,
                        },
                    ),
                )
            )

    async def _message_from_row(
        self, row: Record
    ) -> Message[InboundAttachment | OutboundAttachment]:
        return (await self._messages_from_rows((row,)))[0]

    async def _messages_from_rows(
        self, rows: Sequence[Record]
    ) -> tuple[Message[InboundAttachment | OutboundAttachment], ...]:
        return await messages_from_rows(self._session, rows)

    async def _next_message_seq(self) -> int:
        row = await self.one(
            Select(
                table="messages",
                columns={
                    "next_seq": Binary(
                        operator="+",
                        left=Call(
                            name="coalesce",
                            arguments=(
                                Call(name="max", arguments=(Column(name="seq"),)),
                                Value(value=0),
                            ),
                        ),
                        right=Value(value=1),
                    )
                },
            )
        )
        if row is None:
            raise RuntimeError("Message sequence query returned no row")
        return cast(int, row["next_seq"])

    async def _insert_outbound(
        self,
        message: Message[OutboundAttachment],
        channel_session: ChannelSession,
    ) -> Message[OutboundAttachment]:
        """Write down an outbound message the first time it is sent."""

        canonical = replace(
            message,
            message_id=message.message_id or str(uuid7()),
            seq=await self._next_message_seq(),
            channel=channel_session.channel,
            provider_thread_id=channel_session.provider_thread_id,
            sender=SenderIdentity(name=self.name),
            target_kind=channel_session.target_kind,
        )
        validate_outbound_insert(canonical)
        delivery_state = canonical.delivery_state
        if delivery_state is None:
            raise RuntimeError("outbound message has no delivery state")
        sender = canonical.sender
        if sender is None:
            raise RuntimeError("outbound message has no sender")
        await self._session.insert(
            Insert(
                table="messages",
                rows=(
                    {
                        "message_id": canonical.message_id,
                        "seq": canonical.seq,
                        "direction": canonical.direction.value,
                        "agent_id": self.owner.id,
                        "thread_id": canonical.thread_id,
                        "channel_session_id": canonical.channel_session_id,
                        "channel": canonical.channel,
                        "provider_thread_id": canonical.provider_thread_id,
                        "provider_message_id": canonical.provider_message_id,
                        "sender": sender.name,
                        "sender_id": sender.id,
                        "sender_display_name": sender.display_name,
                        "message_type": canonical.message_type,
                        "target": canonical.target,
                        "target_kind": canonical.target_kind.value,
                        "reply_to_message_id": canonical.reply_to_message_id,
                        "body": canonical.body,
                        "delivery_state": delivery_state.value,
                        "provider_receipt_ref": canonical.provider_receipt_ref,
                        "created_at_ms": canonical.created_at_ms,
                        "provider_attempted_at_ms": canonical.provider_attempted_at_ms,
                        "completed_at_ms": canonical.completed_at_ms,
                        "error_kind": canonical.error_kind,
                        "error_message": canonical.error_message,
                        "metadata_json": encode_metadata(canonical.metadata),
                        "attachments_json": json.dumps(
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
                    },
                ),
            )
        )
        return canonical

    async def _update_outbound(
        self,
        existing: Message[OutboundAttachment],
        message: Message[OutboundAttachment],
    ) -> Message[OutboundAttachment]:
        """Record what became of an outbound message already written down."""

        if (
            existing.thread_id != message.thread_id
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
        await self._session.update(
            Update(
                table="messages",
                values={
                    "delivery_state": Value(value=delivery_state.value),
                    "provider_message_id": Value(value=canonical.provider_message_id),
                    "provider_receipt_ref": Value(value=canonical.provider_receipt_ref),
                    "provider_attempted_at_ms": Value(
                        value=canonical.provider_attempted_at_ms
                    ),
                    "completed_at_ms": Value(value=canonical.completed_at_ms),
                    "error_kind": Value(value=canonical.error_kind),
                    "error_message": Value(value=canonical.error_message),
                    "metadata_json": Value(value=encode_metadata(canonical.metadata)),
                },
                where=All(
                    conditions=(
                        equal("agent_id", self.owner.id),
                        equal("direction", "outbound"),
                        equal("message_id", canonical.message_id),
                    )
                ),
            )
        )
        return canonical

    async def _save_outbound_message(
        self,
        message: Message[OutboundAttachment],
    ) -> Message[OutboundAttachment]:
        validate_outbound_message_input(message)
        thread = await self.get_thread(ThreadActor(message.thread_id))
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

    async def search_messages(
        self,
        request: MessageSearchRequest,
        *,
        actor: ThreadActor | None = None,
        review: Review | None = Review.APPROVED,
    ) -> MessageSearchResult:
        try:
            query = request.query.strip()
            fragments = query.split()
            terms = list(
                dict.fromkeys(fragment for fragment in fragments if len(fragment) >= 3)
            )
            sort: Literal["time", "relevance"] = request.sort if terms else "time"
            predicates: list[Predicate] = [
                self._message_filters(
                    actor, delivery_states=_HISTORY_DELIVERY_STATES, table="message"
                )
            ]
            if request.raw_target is not None:
                target = await self.resolve_inbox_target(request.raw_target)
                if actor is not None and target.thread.id != actor.id:
                    raise InboxTargetResolutionError(
                        f"inbox target is not this conversation: {request.raw_target}"
                    )
                if review is not None and target.channel_session.review is not review:
                    raise InboxTargetResolutionError(
                        f"inbox target is not {review.value}: {request.raw_target}"
                    )
                if actor is None:
                    predicates.append(equal("thread_id", target.thread.id, "message"))
            if review is not None:
                predicates.append(equal("review", review.value, "channel"))
            if request.sender == "self":
                predicates.append(equal("direction", "outbound", "message"))
            elif request.sender is not None:
                token = request.sender.removeprefix("@")
                predicates.extend(
                    (
                        equal("direction", "inbound", "message"),
                        AnyOf(
                            conditions=(
                                Compare(
                                    left=Call(
                                        name="lower",
                                        arguments=(
                                            Column(name="sender", table="message"),
                                        ),
                                    ),
                                    operator="=",
                                    right=Call(
                                        name="lower",
                                        arguments=(Value(value=token),),
                                    ),
                                ),
                                equal("sender_id", token, "message"),
                            )
                        ),
                    )
                )
            search_time = Call(
                name="coalesce",
                arguments=tuple(
                    Column(name=name, table="message")
                    for name in (
                        "provider_time_ms",
                        "received_at_ms",
                        "created_at_ms",
                    )
                ),
            )
            if request.after_ms is not None:
                predicates.append(
                    Compare(
                        left=search_time,
                        operator=">=",
                        right=Value(value=request.after_ms),
                    )
                )
            if request.before_ms is not None:
                predicates.append(
                    Compare(
                        left=search_time,
                        operator="<=",
                        right=Value(value=request.before_ms),
                    )
                )
            joins = (
                Join(
                    table="threads",
                    alias="thread",
                    on=All(
                        conditions=(
                            Compare(
                                left=Column(name="agent_id", table="thread"),
                                operator="=",
                                right=Column(name="agent_id", table="message"),
                            ),
                            Compare(
                                left=Column(name="id", table="thread"),
                                operator="=",
                                right=Column(name="thread_id", table="message"),
                            ),
                        )
                    ),
                ),
                *self._channel_join(),
            )
            source = Select(
                table="messages",
                alias="message",
                joins=joins,
                where=All(conditions=tuple(predicates)),
            )
            body = Column(name="body", table="message")
            lower_body = Call(name="lower", arguments=(body,))
            length = Call(name="length", arguments=(body,))
            rank: Expression = Value(value=0)
            if sort == "relevance":
                statistics = await self._session.select(
                    source.model_copy(
                        update={
                            "columns": {
                                "documents": Call(name="count"),
                                "average": Call(name="avg", arguments=(length,)),
                                **{
                                    f"frequency_{index}": Call(
                                        name="sum",
                                        arguments=(
                                            Case(
                                                when=Contains(
                                                    expression=body, text=term
                                                ),
                                                then=Value(value=1),
                                                otherwise=Value(value=0),
                                            ),
                                        ),
                                    )
                                    for index, term in enumerate(terms)
                                },
                            }
                        }
                    )
                )
                documents = cast(int, statistics[0]["documents"])
                average = cast(float, statistics[0]["average"]) or 1.0
                ranks: list[Expression] = []
                for index, term in enumerate(terms):
                    document_frequency = (
                        cast(int | None, statistics[0][f"frequency_{index}"]) or 0
                    )
                    weight = log(
                        1
                        + (documents - document_frequency + 0.5)
                        / (document_frequency + 0.5)
                    )
                    frequency = Binary(
                        operator="/",
                        left=Binary(
                            operator="-",
                            left=Call(name="length", arguments=(lower_body,)),
                            right=Call(
                                name="length",
                                arguments=(
                                    Call(
                                        name="replace",
                                        arguments=(
                                            lower_body,
                                            Call(
                                                name="lower",
                                                arguments=(Value(value=term),),
                                            ),
                                            Value(value=""),
                                        ),
                                    ),
                                ),
                            ),
                        ),
                        right=Binary(
                            operator="*",
                            left=Value(value=1.0),
                            right=Call(name="length", arguments=(Value(value=term),)),
                        ),
                    )
                    ranks.append(
                        Binary(
                            operator="/",
                            left=Binary(
                                operator="*",
                                left=Binary(
                                    operator="*",
                                    left=Value(value=weight),
                                    right=frequency,
                                ),
                                right=Value(value=2.2),
                            ),
                            right=Binary(
                                operator="+",
                                left=frequency,
                                right=Binary(
                                    operator="*",
                                    left=Value(value=1.2),
                                    right=Binary(
                                        operator="+",
                                        left=Value(value=0.25),
                                        right=Binary(
                                            operator="/",
                                            left=Binary(
                                                operator="*",
                                                left=Value(value=0.75),
                                                right=length,
                                            ),
                                            right=Value(value=average),
                                        ),
                                    ),
                                ),
                            ),
                        )
                    )
                rank = ranks[0]
                for expression in ranks[1:]:
                    rank = Binary(operator="+", left=rank, right=expression)
            if terms:
                predicates.append(
                    Search(
                        index="message_search",
                        table="message",
                        columns=("body",),
                        terms=tuple(
                            fragment for fragment in fragments if len(fragment) >= 3
                        ),
                        row_key="seq",
                    )
                )
            for fragment in fragments:
                if len(fragment) < 3:
                    predicates.append(Contains(expression=body, text=fragment))
            columns: dict[str, Expression] = {
                name: Column(name=name, table="message") for name in _MESSAGE_COLUMNS
            }
            columns["search_at_ms"] = search_time
            order = (
                (Sort(expression=rank, descending=True),) if sort == "relevance" else ()
            )
            rows = await self._session.select(
                source.model_copy(
                    update={
                        "columns": columns,
                        "where": All(conditions=tuple(predicates)),
                        "order_by": (
                            *order,
                            Sort(expression=search_time, descending=True),
                            Sort(
                                expression=Column(name="seq", table="message"),
                                descending=True,
                            ),
                        ),
                        "limit": request.limit + 1,
                        "offset": request.offset,
                    }
                )
            )
            has_more = len(rows) > request.limit
            messages = tuple(message_from_row(row) for row in rows[: request.limit])
            canonical_targets = tuple(
                dict.fromkeys(message.target for message in messages)
            )
            resolved = await concurrent_reads(
                *(self.resolve_inbox_target(target) for target in canonical_targets)
            )
            targets = {
                target: resolved.display_target
                for target, resolved in zip(canonical_targets, resolved, strict=True)
            }
            hits: list[MessageSearchHit] = []
            for row, message in zip(rows[: request.limit], messages, strict=True):
                body = " ".join(message.body.split())
                positions = [
                    match.start()
                    for fragment in fragments
                    if (match := re.search(re.escape(fragment), body, re.IGNORECASE))
                ]
                start = max(0, min(positions, default=0) - 80)
                end = min(len(body), start + 240 - int(start > 0))
                if end < len(body):
                    end -= 1
                snippet = (
                    ("…" if start else "")
                    + body[start:end]
                    + ("…" if end < len(body) else "")
                )
                hits.append(
                    MessageSearchHit(
                        message_id=message.message_id,
                        thread_id=message.thread_id,
                        target=targets[message.target],
                        canonical_target=message.target,
                        channel=cast(str, message.channel),
                        target_kind=message.target_kind,
                        direction=message.direction,
                        sender=message.sender,
                        sender_kind=message.sender_kind,
                        at_ms=cast(int, row["search_at_ms"]),
                        body=message.body,
                        snippet=snippet,
                    )
                )
            return MessageSearchResult(
                query=query,
                sort=sort,
                messages=tuple(hits),
                offset=request.offset,
                has_more=has_more,
                next_offset=request.offset + len(hits) if has_more else None,
            )
        except StorageQueryTimeoutError as error:
            hint = "Search exceeded its execution budget; narrow the keywords or time range"
            if actor is None and request.raw_target is None:
                hint += ", or specify a target"
            raise MessageSearchTimeoutError(hint) from error

    async def _inbound_channel_session(
        self,
        message: Message[InboundAttachment],
        *,
        channel: str,
        provider_thread_id: str,
        now_ms: int,
        new_message: bool,
        opening: Review,
    ) -> tuple[ChannelSession, bool]:
        """Find or open the channel session an inbound message belongs to."""

        channel_session = await self.find_channel_session(
            channel=channel,
            channel_identity=message.channel_identity,
            provider_thread_id=provider_thread_id,
        )
        if channel_session is None:
            channel_session = ChannelSession(
                id=message.channel_session_id,
                channel=channel,
                channel_identity=message.channel_identity,
                provider_thread_id=provider_thread_id,
                created_at_ms=now_ms,
                updated_at_ms=now_ms,
                target_kind=message.target_kind,
                following=(
                    message.target_kind is ChannelTargetKind.DM
                    or message.mentions_agent
                ),
                review=opening,
            )
            if message.target_presentation is not None:
                channel_session = channel_session.with_target_presentation(
                    message.target_presentation,
                    updated_at_ms=now_ms,
                )
            await self.save_channel_session(channel_session)
            return channel_session, True
        if new_message:
            if message.target_presentation is not None:
                channel_session = channel_session.with_target_presentation(
                    message.target_presentation,
                    updated_at_ms=now_ms,
                )
            if message.mentions_agent and not channel_session.following:
                channel_session = replace(
                    channel_session,
                    following=True,
                    updated_at_ms=now_ms,
                )
            # one turned away who writes again is asked about again
            if channel_session.review is Review.DENIED:
                channel_session = replace(
                    channel_session, review=Review.PENDING, updated_at_ms=now_ms
                )
        return channel_session, False

    async def _save_new_inbound(
        self,
        message: Message[InboundAttachment],
        channel_session: ChannelSession,
        thread: Thread,
        *,
        now_ms: int,
    ) -> tuple[Message[InboundAttachment], ChannelSession, Thread]:
        """Write down a message never seen before, and when its sessions last moved."""

        message = cast(
            Message[InboundAttachment],
            await self.save_message(message),
        )
        channel_session = replace(
            channel_session,
            last_inbound_at_ms=message.received_at_ms,
            updated_at_ms=now_ms,
        )
        thread = replace(
            thread,
            last_activity_at_ms=message.received_at_ms,
            updated_at_ms=now_ms,
        )
        await self.save_channel_session(channel_session)
        await self.save_thread(thread)
        return message, channel_session, thread

    async def record_inbound(
        self,
        message: Message[InboundAttachment],
        *,
        now_ms: int,
        opening: Review,
    ) -> RecordInboundResult:
        if message.direction is not MessageDirection.INBOUND:
            raise ValueError("record_inbound requires an inbound message")
        channel = message.channel
        provider_thread_id = message.provider_thread_id
        provider_message_id = message.provider_message_id
        if channel is None or provider_thread_id is None or provider_message_id is None:
            raise RuntimeError("inbound message identity is incomplete")
        existing_message = await self.find_message(
            channel,
            provider_thread_id,
            provider_message_id,
            direction=MessageDirection.INBOUND,
        )
        # the conversation is found by what the message as it arrived says of
        # itself; a copy read back from the log has no bot on it
        channel_session, channel_session_created = await self._inbound_channel_session(
            message,
            channel=channel,
            provider_thread_id=provider_thread_id,
            now_ms=now_ms,
            new_message=existing_message is None,
            opening=opening,
        )
        if existing_message is not None:
            message = replace(
                cast(Message[InboundAttachment], existing_message),
                channel_identity=message.channel_identity,
            )

        thread = await self.find_thread(channel_session.id)
        thread_created = thread is None
        if thread is None:
            thread = Thread(
                id=message.thread_id,
                channel_session_id=channel_session.id,
                workspace_id=self.owner.id,
                created_at_ms=now_ms,
                updated_at_ms=now_ms,
            )
            await self.save_thread(thread)

        if existing_message is None:
            # a conversation not approved is kept quiet, mentioned or not
            notifies_runtime = (
                message.notifies_runtime
                and channel_session.review is Review.APPROVED
                and (
                    message.target_kind is ChannelTargetKind.DM
                    or channel_session.following
                    or message.mentions_agent
                )
            )
            message = replace(
                message,
                thread_id=thread.id,
                channel_session_id=channel_session.id,
                target=channel_session.canonical_target,
                target_presentation=None,
                notifies_runtime=notifies_runtime,
            )

        if (
            message.notifies_runtime
            and await self.get_consumer_cursor(ThreadActor(thread.id)) is None
        ):
            await self.save_consumer_cursor(ConsumerCursor(thread_id=thread.id))

        if existing_message is None:
            message, channel_session, thread = await self._save_new_inbound(
                message, channel_session, thread, now_ms=now_ms
            )

        return RecordInboundResult(
            channel_session=channel_session,
            thread=thread,
            message=message,
            channel_session_created=channel_session_created,
            thread_created=thread_created,
            message_created=existing_message is None,
        )
