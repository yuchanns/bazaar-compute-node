from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path

import pytest
import pytest_asyncio

from bazaar_compute_node.contrib.sqlite import SqliteDatabase
from bazaar_compute_node.core.models import (
    BcnSession,
    BcnSessionState,
    ChannelSession,
    ChannelSessionState,
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
    provider_conversation_key: str = "conversation-1",
    provider_thread_key: str = "thread-1",
    state: ChannelSessionState = ChannelSessionState.ACTIVE,
    updated_at_ms: int = 100,
) -> ChannelSession:
    return ChannelSession(
        channel_session_id=channel_session_id,
        channel_slug="dummy",
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


async def save_session_graph(database: SqliteDatabase) -> None:
    async with database.transaction() as transaction:
        await transaction.save_channel_session(make_channel_session())
        await transaction.save_bcn_session(make_bcn_session())
        await transaction.save_runtime_session(make_runtime_session())


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
