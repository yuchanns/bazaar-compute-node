from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from uuid import UUID

import pytest
import pytest_asyncio

from bazaar_compute_node.contrib.sqlite import SqliteDatabase
from bazaar_compute_node.core.models import (
    BcnSession,
    ChannelSession,
    FreshCheckState,
    InboundMessage,
    OutboundAttachment,
    OutboundDeliveryState,
    OutboundMessage,
    RuntimeSession,
)


@pytest_asyncio.fixture
async def database() -> AsyncIterator[SqliteDatabase]:
    database = SqliteDatabase()
    await database.start(timeout=2)
    await database.initialize(node_id="node-1", workspace_id="workspace-1")
    try:
        yield database
    finally:
        await database.stop(timeout=2)


def make_channel_session(
    *, session_id: str = "channel-1", channel: str = "test"
) -> ChannelSession:
    return ChannelSession(
        id=session_id,
        channel=channel,
        provider_thread_id=f"thread-{session_id}",
        created_at_ms=100,
        updated_at_ms=100,
    )


def make_bcn_session(
    *, session_id: str = "bcn-1", channel_session_id: str = "channel-1"
) -> BcnSession:
    return BcnSession(
        id=session_id,
        channel_session_id=channel_session_id,
        workspace_id="workspace-1",
        created_at_ms=100,
        updated_at_ms=100,
    )


def make_runtime_session(
    *,
    session_id: str = "runtime-1",
    bcn_session_id: str = "bcn-1",
    channel_session_id: str = "channel-1",
) -> RuntimeSession:
    return RuntimeSession(
        id=session_id,
        bcn_session_id=bcn_session_id,
        channel_session_id=channel_session_id,
        runtime="test",
        workspace_id="workspace-1",
        created_at_ms=100,
        updated_at_ms=100,
    )


def make_inbound_message(
    *, session_id: str = "bcn-1", channel_session_id: str = "channel-1"
) -> InboundMessage:
    return InboundMessage(
        seq=0,
        message_id="provider-local-message",
        session_id=session_id,
        channel_session_id=channel_session_id,
        channel="test",
        provider_thread_id=f"thread-{channel_session_id}",
        provider_message_id="provider-message-1",
        received_at_ms=101,
        sender="Sender",
        message_type="text",
        canonical_target=f"#test:{session_id}",
        body="inbound body",
    )


async def save_runtime_graph(
    database: SqliteDatabase,
    *,
    bcn_session_id: str = "bcn-1",
    channel_session_id: str = "channel-1",
    runtime_id: str = "runtime-1",
) -> None:
    async with database.transaction() as transaction:
        await transaction.save_channel_session(
            make_channel_session(session_id=channel_session_id)
        )
        await transaction.save_bcn_session(
            make_bcn_session(
                session_id=bcn_session_id,
                channel_session_id=channel_session_id,
            )
        )
        await transaction.save_runtime_session(
            make_runtime_session(
                session_id=runtime_id,
                bcn_session_id=bcn_session_id,
                channel_session_id=channel_session_id,
            )
        )


def make_draft(
    *,
    outbound_message_id: str = "outbound-1",
    session_id: str = "bcn-1",
    channel_session_id: str = "channel-1",
) -> OutboundMessage:
    return OutboundMessage(
        outbound_message_id=outbound_message_id,
        command_id=f"command-{outbound_message_id}",
        session_id=session_id,
        channel_session_id=channel_session_id,
        target=f"#test:{session_id}",
        body="outbound body",
        attachments=(
            OutboundAttachment(
                name="report.txt",
                relative_path="reports/report.txt",
                media_type="text/plain",
                size_bytes=7,
                sha256="a" * 64,
            ),
        ),
        reply_to_message_id="inbound-local-1",
        state=OutboundDeliveryState.DRAFT,
        fresh_check_state=FreshCheckState.REQUIRED,
        created_at_ms=110,
    )


@pytest.mark.asyncio
async def test_sqlite_outbound_repository_persists_delivery_and_fresh_check_audit(
    database: SqliteDatabase,
) -> None:
    await save_runtime_graph(database)

    async with database.transaction() as transaction:
        draft = await transaction.save_outbound_message(make_draft())
        assert UUID(draft.outbound_message_id).version == 7
        assert (
            await transaction.get_outbound_message(draft.outbound_message_id) == draft
        )

        pending = draft.record_fresh_check(
            FreshCheckState.PASSED,
            snapshot_seq=4,
            current_inbound_seq=4,
        ).transition_to(OutboundDeliveryState.PENDING, at_ms=120)
        pending = replace(pending, provider_attempted_at_ms=121)
        pending = await transaction.save_outbound_message(pending)

        queued = pending.transition_to(
            OutboundDeliveryState.QUEUED,
            at_ms=125,
            provider_receipt_ref="queue-receipt-1",
        )
        queued = await transaction.save_outbound_message(queued)
        assert (
            await transaction.get_outbound_message(draft.outbound_message_id) == queued
        )

        sent = queued.transition_to(
            OutboundDeliveryState.SENT,
            at_ms=130,
            provider_message_id="provider-outbound-1",
            provider_receipt_ref="receipt-1",
        )
        sent = await transaction.save_outbound_message(sent)
        assert await transaction.get_outbound_message(draft.outbound_message_id) == sent

        unknown_draft = await transaction.save_outbound_message(
            make_draft(outbound_message_id="outbound-unknown")
        )
        unknown_pending = unknown_draft.record_fresh_check(
            FreshCheckState.PASSED,
            snapshot_seq=4,
            current_inbound_seq=3,
        ).transition_to(OutboundDeliveryState.PENDING, at_ms=140)
        unknown_pending = await transaction.save_outbound_message(unknown_pending)
        unknown = unknown_pending.transition_to(
            OutboundDeliveryState.UNKNOWN,
            at_ms=150,
            error_kind="provider_unknown",
            error_message="delivery outcome was not confirmed",
            next_action="reconcile before retrying",
        )
        unknown = await transaction.save_outbound_message(unknown)
        failed = unknown.transition_to(
            OutboundDeliveryState.FAILED,
            at_ms=160,
            error_kind="provider_failed",
            error_message="reconciliation confirmed failure",
        )
        failed = await transaction.save_outbound_message(failed)
        assert (
            await transaction.get_outbound_message(failed.outbound_message_id) == failed
        )

        rejected_draft = await transaction.save_outbound_message(
            make_draft(outbound_message_id="outbound-rejected")
        )
        rejected = rejected_draft.record_fresh_check(
            FreshCheckState.FAILED,
            snapshot_seq=None,
            current_inbound_seq=5,
        ).transition_to(
            OutboundDeliveryState.REJECTED,
            at_ms=170,
            error_kind="fresh_check_failed",
            error_message="new inbound message arrived",
            next_action="read before retrying",
        )
        rejected = await transaction.save_outbound_message(rejected)
        persisted_rejected = await transaction.get_outbound_message(
            rejected.outbound_message_id
        )
        assert persisted_rejected == rejected
        assert persisted_rejected is not None
        assert persisted_rejected.provider_attempted_at_ms is None
        assert persisted_rejected.fresh_check_state is FreshCheckState.FAILED

        invalid_pending = replace(
            make_draft(outbound_message_id="invalid-pending"),
            state=OutboundDeliveryState.PENDING,
        )
        with pytest.raises(ValueError, match="passed fresh check"):
            await transaction.save_outbound_message(invalid_pending)


@pytest.mark.asyncio
async def test_sqlite_outbound_repository_rolls_back_and_rejects_identity_changes(
    database: SqliteDatabase,
) -> None:
    await save_runtime_graph(database)
    draft = make_draft(outbound_message_id="rollback-outbound")

    with pytest.raises(RuntimeError, match="rollback outbound"):
        async with database.transaction() as transaction:
            await transaction.save_outbound_message(draft)
            raise RuntimeError("rollback outbound")

    async with database.transaction() as transaction:
        assert await transaction.get_outbound_message(draft.outbound_message_id) is None
        persisted = await transaction.save_outbound_message(draft)
        with pytest.raises(ValueError, match="identity cannot change"):
            await transaction.save_outbound_message(replace(persisted, body="tampered"))
        with pytest.raises(ValueError, match="identity cannot change"):
            await transaction.save_outbound_message(replace(persisted, attachments=()))
