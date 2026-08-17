from __future__ import annotations

import pytest
from bcn_test_support import MemoryStorage

from bazaar_compute_node.core.models import BcnSession, ChannelSession, RuntimeAttempt


@pytest.mark.asyncio
async def test_storage_transaction_rolls_back_on_error() -> None:
    storage = MemoryStorage()
    await storage.start(timeout=1)
    scope = storage.scope("workspace-1", "Test Agent")
    channel_session = ChannelSession(
        id="channel-1",
        channel="test",
        provider_thread_id="thread-1",
        created_at_ms=1,
        updated_at_ms=1,
    )
    session = BcnSession(
        id="bcn-1",
        channel_session_id="channel-1",
        workspace_id="workspace-1",
        created_at_ms=1,
        updated_at_ms=1,
    )

    with pytest.raises(RuntimeError):
        async with scope.transaction() as transaction:
            await transaction.save_channel_session(channel_session)
            await transaction.save_bcn_session(session)
            raise RuntimeError("rollback")

    assert storage.channel_sessions == {}
    assert storage.bcn_sessions == {}


@pytest.mark.asyncio
async def test_memory_storage_runtime_attempt_is_independent_and_immutable() -> None:
    storage = MemoryStorage()
    attempt = RuntimeAttempt(
        turn_id="turn-1",
        session_id="runtime-1",
        client_user_message_id="message-1",
        started_at_ms=1,
    )

    async with storage.transaction() as transaction:
        await transaction.save_runtime_attempt(attempt)
        await transaction.save_runtime_attempt(attempt)
        assert await transaction.get_runtime_attempt("turn-1") == attempt
        with pytest.raises(ValueError, match="immutable"):
            await transaction.save_runtime_attempt(
                RuntimeAttempt(
                    turn_id="turn-1",
                    session_id="runtime-2",
                    client_user_message_id="message-1",
                    started_at_ms=1,
                )
            )

    with pytest.raises(RuntimeError, match="rollback"):
        async with storage.transaction() as transaction:
            await transaction.save_runtime_attempt(
                RuntimeAttempt(
                    turn_id="turn-2",
                    session_id="runtime-2",
                    client_user_message_id="message-2",
                    started_at_ms=2,
                )
            )
            raise RuntimeError("rollback")

    assert storage.runtime_attempts == {"turn-1": attempt}
