from __future__ import annotations

from collections.abc import Sequence
from typing import cast

from bazaar_contrib_messages import Agent
from bazaar_contrib_messages.models import (
    InboundAttachment,
    Message,
    OutboundAttachment,
)
from bazaar_contrib_messages.operations import UnreadMessageOwner
from bazaar_contrib_messages.states import MessageDirection
from bazaar_contrib_storage import (
    All,
    Call,
    Column,
    Compare,
    In,
    Join,
    Record,
    RowNumber,
    Select,
    Session,
    Sort,
    Value,
)

from .base import equal
from .codec import inbound_attachment_from_row, message_from_row

_MESSAGE_COLUMNS = (
    "message_id",
    "seq",
    "direction",
    "agent_id",
    "thread_id",
    "channel_session_id",
    "channel",
    "provider_thread_id",
    "provider_message_id",
    "edit_version",
    "edit_of_message_id",
    "provider_time_ms",
    "received_at_ms",
    "sender",
    "sender_id",
    "sender_display_name",
    "message_type",
    "target",
    "target_kind",
    "reply_to_message_id",
    "body",
    "mentions_agent",
    "notifies_runtime",
    "provider_payload_ref",
    "delivery_state",
    "provider_receipt_ref",
    "created_at_ms",
    "provider_attempted_at_ms",
    "completed_at_ms",
    "error_kind",
    "error_message",
    "metadata_json",
    "attachments_json",
)


def unread_query(owner: Agent | None) -> Select:
    return Select(
        table="messages",
        alias="message",
        columns={name: Column(name=name, table="message") for name in _MESSAGE_COLUMNS},
        joins=(
            Join(
                table="consumer_cursors",
                alias="cursor",
                kind="left",
                on=Compare(
                    left=Column(name="thread_id", table="cursor"),
                    operator="=",
                    right=Column(name="thread_id", table="message"),
                ),
            ),
        ),
        where=All(
            conditions=(
                equal("direction", "inbound", "message"),
                equal("notifies_runtime", 1, "message"),
                Compare(
                    left=Column(name="seq", table="message"),
                    operator=">",
                    right=Call(
                        name="coalesce",
                        arguments=(
                            Column(name="delivered_through_seq", table="cursor"),
                            Value(value=0),
                        ),
                    ),
                ),
                *(
                    (equal("agent_id", owner.id, "message"),)
                    if owner is not None
                    else ()
                ),
            )
        ),
    )


async def list_unread_message_owners(
    session: Session, owner: Agent | None = None
) -> tuple[UnreadMessageOwner, ...]:
    ranked = unread_query(owner).model_copy(
        update={
            "columns": {
                **unread_query(owner).columns,
                "unread_rank": RowNumber(
                    partition_by=(
                        Column(name="agent_id", table="message"),
                        Column(name="thread_id", table="message"),
                    ),
                    order_by=(
                        Sort(
                            expression=Column(name="seq", table="message"),
                            descending=True,
                        ),
                        Sort(
                            expression=Column(name="message_id", table="message"),
                            descending=True,
                        ),
                    ),
                ),
            }
        }
    )
    rows = await session.select(
        Select(
            table="unread_ranked",
            ctes={"unread_ranked": ranked},
            columns={name: Column(name=name) for name in _MESSAGE_COLUMNS},
            where=equal("unread_rank", 1),
            order_by=(
                Sort(expression=Column(name="agent_id")),
                Sort(expression=Column(name="thread_id")),
            ),
        )
    )
    messages = await messages_from_rows(session, rows)
    return tuple(
        UnreadMessageOwner(
            agent=Agent(cast(str, row["agent_id"])),
            trigger_message=cast(Message[InboundAttachment], message),
        )
        for row, message in zip(rows, messages)
    )


async def messages_from_rows(
    session: Session, rows: Sequence[Record]
) -> tuple[Message[InboundAttachment | OutboundAttachment], ...]:
    ids = tuple(
        row["message_id"]
        for row in rows
        if row["direction"] == MessageDirection.INBOUND.value
    )
    attachments: dict[str, list[InboundAttachment]] = {}
    if ids:
        for row in await session.select(
            Select(
                table="inbound_attachments",
                where=In(expression=Column(name="message_id"), values=ids),
                order_by=(
                    Sort(expression=Column(name="message_id")),
                    Sort(expression=Column(name="ordinal")),
                ),
            )
        ):
            attachments.setdefault(cast(str, row["message_id"]), []).append(
                inbound_attachment_from_row(row)
            )
    return tuple(
        message_from_row(row, tuple(attachments.get(cast(str, row["message_id"]), ())))
        for row in rows
    )
