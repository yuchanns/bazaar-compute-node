from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from uuid import UUID

import pytest
import pytest_asyncio

from bazaar_compute_node.contrib.sqlite import SqliteDatabase
from bazaar_compute_node.core.models import (
    AgentState,
    BcnSession,
    ChannelSession,
    ChannelSessionState,
    FreshCheckState,
    InboundMessage,
    OutboundDeliveryState,
    OutboundMessage,
    RuntimeEvent,
    RuntimeEventState,
    RuntimeProcessState,
    RuntimeSession,
    RuntimeTurn,
    RuntimeTurnState,
    StateTransitionError,
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
    *, channel_session_id: str = "channel-1", channel_slug: str = "dummy"
) -> ChannelSession:
    return ChannelSession(
        channel_session_id=channel_session_id,
        channel_slug=channel_slug,
        provider_conversation_key=f"conversation-{channel_session_id}",
        provider_thread_key=f"thread-{channel_session_id}",
        state=ChannelSessionState.ACTIVE,
        created_at_ms=100,
        updated_at_ms=100,
    )


def make_bcn_session(
    *, bcn_session_id: str = "bcn-1", channel_session_id: str = "channel-1"
) -> BcnSession:
    return BcnSession(
        bcn_session_id=bcn_session_id,
        channel_session_id=channel_session_id,
        workspace_id="workspace-1",
        state=AgentState.CREATED,
        created_at_ms=100,
        updated_at_ms=100,
    )


def make_runtime_session(
    *,
    runtime_id: str = "runtime-1",
    bcn_session_id: str = "bcn-1",
    channel_session_id: str = "channel-1",
) -> RuntimeSession:
    return RuntimeSession(
        agent_runtime_session_id=runtime_id,
        bcn_session_id=bcn_session_id,
        channel_session_id=channel_session_id,
        runtime_slug="dummy",
        workspace_id="workspace-1",
        process_state=RuntimeProcessState.STARTING,
        created_at_ms=100,
        updated_at_ms=100,
    )


def make_inbound_message(
    *, bcn_session_id: str = "bcn-1", channel_session_id: str = "channel-1"
) -> InboundMessage:
    return InboundMessage(
        seq=0,
        message_id="provider-local-message",
        bcn_session_id=bcn_session_id,
        channel_session_id=channel_session_id,
        channel_slug="dummy",
        provider_message_id="provider-message-1",
        received_at_ms=101,
        sender_id="sender-1",
        sender_display_name="Sender",
        message_type="text",
        canonical_target=f"#dummy:{bcn_session_id}",
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
            make_channel_session(channel_session_id=channel_session_id)
        )
        await transaction.save_bcn_session(
            make_bcn_session(
                bcn_session_id=bcn_session_id,
                channel_session_id=channel_session_id,
            )
        )
        await transaction.save_runtime_session(
            make_runtime_session(
                runtime_id=runtime_id,
                bcn_session_id=bcn_session_id,
                channel_session_id=channel_session_id,
            )
        )


def make_draft(
    *,
    outbound_message_id: str = "outbound-1",
    bcn_session_id: str = "bcn-1",
    channel_session_id: str = "channel-1",
) -> OutboundMessage:
    return OutboundMessage(
        outbound_message_id=outbound_message_id,
        command_id=f"command-{outbound_message_id}",
        bcn_session_id=bcn_session_id,
        channel_session_id=channel_session_id,
        target=f"#dummy:{bcn_session_id}",
        body="outbound body",
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


@pytest.mark.asyncio
async def test_sqlite_runtime_turn_repository_enforces_transitions_and_active_turn(
    database: SqliteDatabase,
) -> None:
    await save_runtime_graph(database)

    async with database.transaction() as transaction:
        starting = RuntimeTurn(
            turn_id="turn-1",
            agent_runtime_session_id="runtime-1",
            state=RuntimeTurnState.STARTING,
            started_at_ms=110,
            client_user_message_id="message-1",
        )
        await transaction.save_runtime_turn(starting)
        assert await transaction.get_runtime_turn("turn-1") == starting

        second_starting = replace(starting, turn_id="turn-2")
        with pytest.raises(ValueError, match="active turn"):
            await transaction.save_runtime_turn(second_starting)

        running = starting.transition_to(
            RuntimeTurnState.RUNNING,
            at_ms=120,
            latest_event_name="runtime.turn.started",
        )
        await transaction.save_runtime_turn(running)
        unknown = running.transition_to(
            RuntimeTurnState.UNKNOWN,
            at_ms=130,
            error_kind="provider_unknown",
            error_message="stream ended unexpectedly",
        )
        await transaction.save_runtime_turn(unknown)
        reconciling = unknown.transition_to(
            RuntimeTurnState.RECONCILING,
            at_ms=140,
        )
        await transaction.save_runtime_turn(reconciling)
        completed = reconciling.transition_to(
            RuntimeTurnState.COMPLETED,
            at_ms=150,
            latest_event_name="runtime.turn.completed",
        )
        await transaction.save_runtime_turn(completed)
        assert await transaction.get_runtime_turn("turn-1") == completed

        next_turn = replace(starting, turn_id="turn-2", started_at_ms=160)
        await transaction.save_runtime_turn(next_turn)

        invalid_initial = replace(
            starting,
            turn_id="turn-invalid",
            state=RuntimeTurnState.RUNNING,
        )
        with pytest.raises(ValueError, match="start in starting"):
            await transaction.save_runtime_turn(invalid_initial)

        with pytest.raises(StateTransitionError):
            completed.transition_to(RuntimeTurnState.RUNNING, at_ms=160)


async def save_starting_turn(database: SqliteDatabase, turn_id: str) -> RuntimeTurn:
    turn = RuntimeTurn(
        turn_id=turn_id,
        agent_runtime_session_id="runtime-1",
        state=RuntimeTurnState.STARTING,
        started_at_ms=200,
    )
    async with database.transaction() as transaction:
        await transaction.save_runtime_turn(turn)
    return turn


@pytest.mark.asyncio
async def test_sqlite_active_turn_invariant_serializes_two_connections() -> None:
    first_database = SqliteDatabase()
    second_database = SqliteDatabase()
    await first_database.start(timeout=2)
    await first_database.initialize(node_id="node-1", workspace_id="workspace-1")
    await save_runtime_graph(first_database)
    await second_database.start(timeout=2)
    await second_database.initialize(node_id="node-1", workspace_id="workspace-1")

    async def attempt(database: SqliteDatabase, turn_id: str) -> BaseException | None:
        try:
            await save_starting_turn(database, turn_id)
        except BaseException as error:  # noqa: BLE001
            return error
        return None

    try:
        results = await asyncio.gather(
            attempt(first_database, "turn-a"),
            attempt(second_database, "turn-b"),
        )
        assert sum(result is None for result in results) == 1
        assert (
            sum(
                isinstance(result, ValueError) and "active turn" in str(result)
                for result in results
            )
            == 1
        )
    finally:
        await second_database.stop(timeout=2)
        await first_database.stop(timeout=2)


@pytest.mark.asyncio
async def test_sqlite_runtime_event_repository_is_append_only_and_validates_references(
    database: SqliteDatabase,
) -> None:
    await save_runtime_graph(database)
    async with database.transaction() as transaction:
        inbound = await transaction.append_inbound_message(make_inbound_message())
        turn = RuntimeTurn(
            turn_id="turn-event",
            agent_runtime_session_id="runtime-1",
            state=RuntimeTurnState.STARTING,
            started_at_ms=110,
        )
        await transaction.save_runtime_turn(turn)

        started = RuntimeEvent(
            event_seq=999,
            event_id="event-1",
            created_at_ms=120,
            level="info",
            event_name="runtime.turn.started",
            state=RuntimeEventState.STARTED,
            node_id="node-1",
            channel_slug="dummy",
            runtime_slug="dummy",
            channel_session_id="channel-1",
            bcn_session_id="bcn-1",
            agent_runtime_session_id="runtime-1",
            turn_id="turn-event",
            inbound_seq=inbound.seq,
        )
        canonical_started = await transaction.append_runtime_event(started)
        assert canonical_started.event_seq == 1
        assert canonical_started == replace(started, event_seq=1)

        duplicate = await transaction.append_runtime_event(
            replace(started, event_seq=500)
        )
        assert duplicate == canonical_started

        completed = replace(
            started,
            event_seq=0,
            event_id="event-2",
            created_at_ms=130,
            event_name="runtime.turn.completed",
            state=RuntimeEventState.COMPLETED,
        )
        canonical_completed = await transaction.append_runtime_event(completed)
        assert canonical_completed.event_seq == 2

        with pytest.raises(ValueError, match="different event content"):
            await transaction.append_runtime_event(
                replace(started, event_seq=700, event_name="tampered")
            )

        with pytest.raises(ValueError, match="runtime slug"):
            await transaction.append_runtime_event(
                replace(
                    started,
                    event_seq=0,
                    event_id="event-bad-runtime",
                    runtime_slug="other-runtime",
                )
            )

        with pytest.raises(ValueError, match="unknown inbound sequence"):
            await transaction.append_runtime_event(
                replace(
                    started,
                    event_seq=0,
                    event_id="event-bad-inbound",
                    inbound_seq=999,
                )
            )

        startup_failure = RuntimeEvent(
            event_seq=0,
            event_id="event-startup-failure",
            created_at_ms=140,
            level="error",
            event_name="runtime.session.start.failed",
            state=RuntimeEventState.FAILED,
            node_id="node-1",
            error_kind="provider_failed",
            error_message="runtime did not start",
        )
        await transaction.append_runtime_event(startup_failure)

    with pytest.raises(RuntimeError, match="rollback event"):
        async with database.transaction() as transaction:
            await transaction.append_runtime_event(
                RuntimeEvent(
                    event_seq=0,
                    event_id="event-rollback",
                    created_at_ms=150,
                    level="error",
                    event_name="runtime.rollback",
                    state=RuntimeEventState.FAILED,
                    node_id="node-1",
                )
            )
            raise RuntimeError("rollback event")

    async with database.transaction() as transaction:
        count = await transaction.fetchone(
            "SELECT COUNT(*) AS count FROM runtime_events"
        )
        assert count is not None
        assert count["count"] == 3
