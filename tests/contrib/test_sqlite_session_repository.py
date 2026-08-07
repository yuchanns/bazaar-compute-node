from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest
import pytest_asyncio

from bazaar_compute_node.contrib.sqlite import SqliteDatabase
from bazaar_compute_node.core.models import (
    BcnSession,
    BcnSessionState,
    ChannelSession,
    ChannelSessionState,
    ConsumerCursor,
    InboundMessage,
    RuntimeProcessState,
    RuntimeSession,
    StateTransitionError,
)


@pytest_asyncio.fixture
async def database(tmp_path: Path) -> AsyncIterator[SqliteDatabase]:
    database = SqliteDatabase(tmp_path / "node")
    await database.start(timeout=2)
    await database.initialize(node_id="node-1", workspace_id="workspace-1")
    try:
        yield database
    finally:
        await database.stop(timeout=2)


def make_channel_session(
    *,
    channel_session_id: str = "channel-1",
    channel_slug: str = "dummy",
    provider_conversation_key: str = "conversation-1",
    provider_thread_key: str = "thread-1",
    state: ChannelSessionState = ChannelSessionState.ACTIVE,
    updated_at_ms: int = 100,
) -> ChannelSession:
    return ChannelSession(
        channel_session_id=channel_session_id,
        channel_slug=channel_slug,
        provider_conversation_key=provider_conversation_key,
        provider_thread_key=provider_thread_key,
        state=state,
        created_at_ms=100,
        updated_at_ms=updated_at_ms,
        metadata={"source": "test", "nested": {"enabled": True}},
    )


def make_bcn_session(
    *,
    bcn_session_id: str = "bcn-1",
    channel_session_id: str = "channel-1",
    workspace_id: str = "workspace-1",
    state: BcnSessionState = BcnSessionState.CREATED,
    updated_at_ms: int = 100,
) -> BcnSession:
    return BcnSession(
        bcn_session_id=bcn_session_id,
        channel_session_id=channel_session_id,
        workspace_id=workspace_id,
        state=state,
        created_at_ms=100,
        updated_at_ms=updated_at_ms,
        metadata={"role": "test"},
    )


def make_runtime_session(
    *,
    agent_runtime_session_id: str = "runtime-1",
    bcn_session_id: str = "bcn-1",
    channel_session_id: str = "channel-1",
    workspace_id: str = "workspace-1",
    process_state: RuntimeProcessState = RuntimeProcessState.STARTING,
    updated_at_ms: int = 100,
) -> RuntimeSession:
    return RuntimeSession(
        agent_runtime_session_id=agent_runtime_session_id,
        bcn_session_id=bcn_session_id,
        channel_session_id=channel_session_id,
        runtime_slug="dummy",
        workspace_id=workspace_id,
        process_state=process_state,
        created_at_ms=100,
        updated_at_ms=updated_at_ms,
        provider_thread_id="runtime-thread-1",
        process_id=1234,
        metadata={"version": 1},
    )


def make_inbound_message(
    *,
    bcn_session_id: str = "bcn-1",
    channel_session_id: str = "channel-1",
    channel_slug: str = "dummy",
    provider_message_id: str = "provider-message-1",
    message_id: str = "caller-message-1",
    seq: int = 0,
    canonical_target: str = "#dummy:channel-1",
    body: str = "inbound body",
    received_at_ms: int = 200,
) -> InboundMessage:
    return InboundMessage(
        seq=seq,
        message_id=message_id,
        bcn_session_id=bcn_session_id,
        channel_session_id=channel_session_id,
        channel_slug=channel_slug,
        provider_message_id=provider_message_id,
        received_at_ms=received_at_ms,
        sender_id="sender-1",
        sender_display_name="Sender",
        message_type="text",
        canonical_target=canonical_target,
        body=body,
        provider_time_ms=received_at_ms,
        provider_thread_id="thread-1",
        metadata={"source": "test", "nested": {"enabled": True}},
    )


async def save_session_graph(database: SqliteDatabase) -> None:
    async with database.transaction() as transaction:
        await transaction.save_channel_session(make_channel_session())
        await transaction.save_bcn_session(make_bcn_session())
        await transaction.save_runtime_session(make_runtime_session())


async def save_channel_and_bcn_session(
    database: SqliteDatabase,
    *,
    channel_session_id: str,
    bcn_session_id: str,
    channel_slug: str = "dummy",
    provider_conversation_key: str | None = None,
) -> None:
    async with database.transaction() as transaction:
        await transaction.save_channel_session(
            make_channel_session(
                channel_session_id=channel_session_id,
                channel_slug=channel_slug,
                provider_conversation_key=provider_conversation_key or "conversation-1",
            )
        )
        await transaction.save_bcn_session(
            make_bcn_session(
                bcn_session_id=bcn_session_id,
                channel_session_id=channel_session_id,
            )
        )


@pytest.mark.asyncio
async def test_sqlite_session_graph_persists_and_supports_recovery_lookups(
    database: SqliteDatabase,
) -> None:
    await save_session_graph(database)

    async with database.transaction() as transaction:
        assert (
            await transaction.find_channel_session(
                channel_slug="dummy",
                provider_conversation_key="conversation-1",
                provider_thread_key="thread-1",
            )
            == make_channel_session()
        )
        assert (
            await transaction.get_channel_session("channel-1") == make_channel_session()
        )
        assert await transaction.find_bcn_session("channel-1") == make_bcn_session()
        assert await transaction.get_bcn_session("bcn-1") == make_bcn_session()
        assert await transaction.find_runtime_session("bcn-1") == make_runtime_session()
        assert (
            await transaction.get_runtime_session("runtime-1") == make_runtime_session()
        )

    data_dir = database.data_dir
    await database.stop(timeout=2)
    restarted = SqliteDatabase(data_dir)
    await restarted.start(timeout=2)
    try:
        await restarted.initialize(node_id="node-1", workspace_id="workspace-1")
        async with restarted.transaction() as transaction:
            assert await transaction.find_bcn_session("channel-1") == make_bcn_session()
            assert (
                await transaction.find_runtime_session("bcn-1")
                == make_runtime_session()
            )
    finally:
        await restarted.stop(timeout=2)


@pytest.mark.asyncio
async def test_sqlite_inbound_log_generates_node_identity_and_deduplicates(
    database: SqliteDatabase,
) -> None:
    await save_session_graph(database)
    inbound = make_inbound_message()

    async with database.transaction() as transaction:
        first = await transaction.append_inbound_message(inbound)
        second = await transaction.append_inbound_message(
            make_inbound_message(
                provider_message_id="provider-message-2",
                message_id="caller-message-2",
                seq=999,
                canonical_target="#dummy:channel-1",
                body="second inbound body",
                received_at_ms=201,
            )
        )

        assert first.message_id != inbound.message_id
        assert UUID(first.message_id).version == 7
        assert first.seq == 1
        assert second.seq == 2
        assert await transaction.get_latest_inbound_seq("bcn-1") == 2
        assert await transaction.list_inbound_messages("bcn-1", after_seq=0) == (
            first,
            second,
        )

        duplicate = await transaction.append_inbound_message(
            replace(inbound, message_id="retry-message", seq=123)
        )
        assert duplicate == first

        with pytest.raises(ValueError, match="different inbound content"):
            await transaction.append_inbound_message(
                replace(inbound, message_id="conflicting-message", body="tampered")
            )

    await save_channel_and_bcn_session(
        database,
        channel_session_id="channel-2",
        bcn_session_id="bcn-2",
        provider_conversation_key="conversation-2",
    )
    async with database.transaction() as transaction:
        other_session_message = await transaction.append_inbound_message(
            make_inbound_message(
                bcn_session_id="bcn-2",
                channel_session_id="channel-2",
                provider_message_id="provider-message-bcn-2",
                message_id="other-session-message",
            )
        )
        assert other_session_message.seq == 3
        assert await transaction.get_latest_inbound_seq("bcn-1") == 2
        assert len(await transaction.list_inbound_messages("bcn-1")) == 2

    with pytest.raises(ValueError, match="already bound"):
        async with database.transaction() as transaction:
            await transaction.append_inbound_message(
                make_inbound_message(
                    bcn_session_id="bcn-2",
                    channel_session_id="channel-2",
                    provider_message_id=inbound.provider_message_id,
                    message_id="cross-session-message",
                )
            )

    await save_channel_and_bcn_session(
        database,
        channel_session_id="channel-3",
        bcn_session_id="bcn-3",
        channel_slug="other",
    )
    async with database.transaction() as transaction:
        other_provider_namespace = await transaction.append_inbound_message(
            make_inbound_message(
                bcn_session_id="bcn-3",
                channel_session_id="channel-3",
                channel_slug="other",
                provider_message_id=inbound.provider_message_id,
                message_id="other-channel-message",
            )
        )
    assert other_provider_namespace.seq == 4


@pytest.mark.asyncio
async def test_sqlite_cursor_snapshot_check_drains_and_read_does_not(
    database: SqliteDatabase,
) -> None:
    await save_session_graph(database)
    async with database.transaction() as transaction:
        messages_list: list[InboundMessage] = []
        for index in range(1, 4):
            messages_list.append(
                await transaction.append_inbound_message(
                    make_inbound_message(
                        provider_message_id=f"provider-message-{index}",
                        message_id=f"caller-message-{index}",
                        body=f"body-{index}",
                        received_at_ms=200 + index,
                    )
                )
            )
        messages = tuple(messages_list)
        await transaction.save_consumer_cursor(ConsumerCursor(bcn_session_id="bcn-1"))

    async with database.transaction() as transaction:
        cursor = await transaction.get_consumer_cursor("bcn-1")
        assert cursor is not None
        latest_seq = await transaction.get_latest_inbound_seq("bcn-1")
        checked_messages = await transaction.list_inbound_messages(
            "bcn-1", after_seq=cursor.delivered_through_seq
        )
        assert checked_messages == messages
        checked_cursor = replace(
            cursor,
            delivered_through_seq=latest_seq,
            inbox_snapshot_seq=latest_seq,
            inbox_snapshot_source="check",
            inbox_snapshot_at_ms=10,
            last_check_at_ms=10,
            updated_at_ms=10,
        )
        await transaction.save_consumer_cursor(checked_cursor)

    async with database.transaction() as transaction:
        persisted = await transaction.get_consumer_cursor("bcn-1")
        assert persisted == checked_cursor
        assert persisted is not None
        await transaction.append_inbound_message(
            make_inbound_message(
                provider_message_id="provider-message-4",
                message_id="caller-message-4",
                body="body-4",
                received_at_ms=204,
            )
        )
        latest_seq = await transaction.get_latest_inbound_seq("bcn-1")
        history = await transaction.list_inbound_messages(
            "bcn-1",
            target="#dummy:channel-1",
            around_message_id=messages[2].message_id,
            limit=3,
        )
        assert [message.body for message in history] == [
            "body-2",
            "body-3",
            "body-4",
        ]
        read_cursor = replace(
            persisted,
            inbox_snapshot_seq=latest_seq,
            inbox_snapshot_source="read",
            inbox_snapshot_at_ms=20,
            last_read_at_ms=20,
            updated_at_ms=20,
        )
        await transaction.save_consumer_cursor(read_cursor)

    async with database.transaction() as transaction:
        persisted = await transaction.get_consumer_cursor("bcn-1")
        assert persisted is not None
        assert persisted.delivered_through_seq == 3
        assert persisted.inbox_snapshot_seq == 4
        assert persisted.inbox_snapshot_source == "read"
        with pytest.raises(ValueError, match="read snapshot cannot advance"):
            await transaction.save_consumer_cursor(
                replace(
                    persisted,
                    delivered_through_seq=4,
                    updated_at_ms=21,
                )
            )
        with pytest.raises(ValueError, match="cannot exceed"):
            await transaction.save_consumer_cursor(
                replace(
                    persisted,
                    inbox_snapshot_seq=5,
                    inbox_snapshot_at_ms=22,
                    updated_at_ms=22,
                )
            )

        latest_seq = await transaction.get_latest_inbound_seq("bcn-1")
        new_messages = await transaction.list_inbound_messages(
            "bcn-1", after_seq=persisted.delivered_through_seq
        )
        assert latest_seq == 4
        assert [message.body for message in new_messages] == ["body-4"]


@pytest.mark.asyncio
async def test_sqlite_inbound_and_cursor_transaction_rolls_back_together(
    database: SqliteDatabase,
) -> None:
    await save_session_graph(database)

    with pytest.raises(RuntimeError, match="rollback"):
        async with database.transaction() as transaction:
            await transaction.append_inbound_message(make_inbound_message())
            await transaction.save_consumer_cursor(
                ConsumerCursor(bcn_session_id="bcn-1")
            )
            raise RuntimeError("rollback")

    async with database.transaction() as transaction:
        assert await transaction.get_latest_inbound_seq("bcn-1") == 0
        assert await transaction.get_consumer_cursor("bcn-1") is None


@pytest.mark.asyncio
async def test_sqlite_concurrent_check_and_read_keep_cursor_session_scoped(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "node"
    lifecycle_timeout = 10
    first = SqliteDatabase(data_dir)
    second = SqliteDatabase(data_dir)
    await first.start(timeout=lifecycle_timeout)
    await first.initialize(node_id="node-1", workspace_id="workspace-1")
    await second.start(timeout=lifecycle_timeout)
    await second.initialize(node_id="node-1", workspace_id="workspace-1")
    try:
        await save_session_graph(first)
        async with first.transaction() as transaction:
            await transaction.append_inbound_message(make_inbound_message())
            await transaction.append_inbound_message(
                make_inbound_message(
                    provider_message_id="provider-message-2",
                    message_id="caller-message-2",
                    body="body-2",
                    received_at_ms=201,
                )
            )

        async def check() -> tuple[InboundMessage, ...]:
            async with first.transaction() as transaction:
                cursor = await transaction.get_consumer_cursor("bcn-1")
                if cursor is None:
                    cursor = ConsumerCursor(bcn_session_id="bcn-1")
                latest_seq = await transaction.get_latest_inbound_seq("bcn-1")
                messages = await transaction.list_inbound_messages(
                    "bcn-1", after_seq=cursor.delivered_through_seq
                )
                await asyncio.sleep(0.05)
                await transaction.save_consumer_cursor(
                    replace(
                        cursor,
                        delivered_through_seq=latest_seq,
                        inbox_snapshot_seq=latest_seq,
                        inbox_snapshot_source="check",
                        inbox_snapshot_at_ms=100,
                        last_check_at_ms=100,
                        updated_at_ms=100,
                    )
                )
                return messages

        async def read() -> tuple[InboundMessage, ...]:
            async with second.transaction() as transaction:
                cursor = await transaction.get_consumer_cursor("bcn-1")
                if cursor is None:
                    cursor = ConsumerCursor(bcn_session_id="bcn-1")
                latest_seq = await transaction.get_latest_inbound_seq("bcn-1")
                messages = await transaction.list_inbound_messages("bcn-1")
                await asyncio.sleep(0.05)
                await transaction.save_consumer_cursor(
                    replace(
                        cursor,
                        inbox_snapshot_seq=latest_seq,
                        inbox_snapshot_source="read",
                        inbox_snapshot_at_ms=100,
                        last_read_at_ms=100,
                        updated_at_ms=100,
                    )
                )
                return messages

        checked_messages, read_messages = await asyncio.gather(check(), read())
        assert len(checked_messages) == 2
        assert len(read_messages) == 2
        async with first.transaction() as transaction:
            cursor = await transaction.get_consumer_cursor("bcn-1")
            assert cursor is not None
            assert cursor.delivered_through_seq == 2
            assert cursor.inbox_snapshot_seq == 2
            integrity = await transaction.fetchone("PRAGMA integrity_check")
            assert integrity is not None
            assert integrity[0] == "ok"
    finally:
        await first.stop(timeout=lifecycle_timeout)
        await second.stop(timeout=lifecycle_timeout)


@pytest.mark.asyncio
async def test_sqlite_session_graph_rejects_duplicate_bindings(
    database: SqliteDatabase,
) -> None:
    await save_session_graph(database)

    with pytest.raises(ValueError, match="channel provider identity"):
        async with database.transaction() as transaction:
            await transaction.save_channel_session(
                make_channel_session(channel_session_id="channel-2")
            )

    with pytest.raises(ValueError, match="channel session is already bound"):
        async with database.transaction() as transaction:
            await transaction.save_bcn_session(make_bcn_session(bcn_session_id="bcn-2"))

    with pytest.raises(ValueError, match="bcn session is already bound"):
        async with database.transaction() as transaction:
            await transaction.save_runtime_session(
                make_runtime_session(agent_runtime_session_id="runtime-2")
            )


@pytest.mark.asyncio
async def test_sqlite_session_updates_validate_binding_and_state_transitions(
    database: SqliteDatabase,
) -> None:
    await save_session_graph(database)

    async with database.transaction() as transaction:
        await transaction.save_channel_session(
            replace(
                make_channel_session(),
                state=ChannelSessionState.CLOSED,
                updated_at_ms=101,
            )
        )

    running_runtime = replace(
        make_runtime_session(),
        process_state=RuntimeProcessState.RUNNING,
        updated_at_ms=101,
        started_at_ms=101,
    )
    async with database.transaction() as transaction:
        await transaction.save_runtime_session(running_runtime)

    async with database.transaction() as transaction:
        assert await transaction.get_runtime_session("runtime-1") == running_runtime

    with pytest.raises(StateTransitionError):
        async with database.transaction() as transaction:
            await transaction.save_channel_session(
                replace(
                    make_channel_session(),
                    state=ChannelSessionState.ACTIVE,
                    updated_at_ms=102,
                )
            )

    with pytest.raises(ValueError, match="updated_at_ms"):
        async with database.transaction() as transaction:
            await transaction.save_channel_session(
                replace(
                    make_channel_session(state=ChannelSessionState.CLOSED),
                    updated_at_ms=99,
                )
            )

    with pytest.raises(ValueError, match="binding cannot change"):
        async with database.transaction() as transaction:
            await transaction.save_runtime_session(
                replace(make_runtime_session(), runtime_slug="other-runtime")
            )

    with pytest.raises(ValueError, match="workspace"):
        async with database.transaction() as transaction:
            await transaction.save_bcn_session(
                make_bcn_session(bcn_session_id="bcn-2", workspace_id="workspace-2")
            )

    with pytest.raises(ValueError, match="does not match bcn session"):
        async with database.transaction() as transaction:
            await transaction.save_runtime_session(
                make_runtime_session(
                    agent_runtime_session_id="runtime-2",
                    channel_session_id="other-channel",
                )
            )


@pytest.mark.asyncio
async def test_sqlite_session_graph_rolls_back_as_one_transaction(
    database: SqliteDatabase,
) -> None:
    with pytest.raises(RuntimeError, match="rollback"):
        async with database.transaction() as transaction:
            await transaction.save_channel_session(make_channel_session())
            await transaction.save_bcn_session(make_bcn_session())
            await transaction.save_runtime_session(make_runtime_session())
            raise RuntimeError("rollback")

    async with database.transaction() as transaction:
        assert await transaction.get_channel_session("channel-1") is None
        assert await transaction.get_bcn_session("bcn-1") is None
        assert await transaction.get_runtime_session("runtime-1") is None


@pytest.mark.asyncio
async def test_sqlite_concurrent_get_or_create_has_one_winner(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "node"
    first = SqliteDatabase(data_dir)
    second = SqliteDatabase(data_dir)
    await first.start(timeout=2)
    await first.initialize(node_id="node-1", workspace_id="workspace-1")
    await second.start(timeout=2)
    await second.initialize(node_id="node-1", workspace_id="workspace-1")

    async def insert(database: SqliteDatabase, channel_session_id: str) -> object:
        async with database.transaction() as transaction:
            await asyncio.sleep(0.05)
            await transaction.save_channel_session(
                make_channel_session(
                    channel_session_id=channel_session_id,
                    provider_conversation_key="conversation-concurrent",
                    provider_thread_key="",
                )
            )
            return channel_session_id

    try:
        results = await asyncio.gather(
            insert(first, "channel-first"),
            insert(second, "channel-second"),
            return_exceptions=True,
        )
        successful_ids = [result for result in results if isinstance(result, str)]
        assert len(successful_ids) == 1
        errors = [result for result in results if isinstance(result, Exception)]
        assert len(errors) == 1
        assert isinstance(errors[0], ValueError)
        async with first.transaction() as transaction:
            winner = await transaction.find_channel_session(
                channel_slug="dummy",
                provider_conversation_key="conversation-concurrent",
                provider_thread_key="",
            )
        assert winner is not None
        assert winner.channel_session_id in {"channel-first", "channel-second"}
    finally:
        await first.stop(timeout=2)
        await second.stop(timeout=2)
