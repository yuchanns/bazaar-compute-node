from __future__ import annotations

import pytest
from bcn_test_support import MemoryStorage

from bazaar_compute_node.core.models import (
    AgentState,
    BcnSession,
    ChannelSession,
    ChannelSessionState,
)


@pytest.mark.asyncio
async def test_storage_transaction_rolls_back_on_error() -> None:
    storage = MemoryStorage()
    await storage.initialize(node_id="node-1", workspace_id="workspace-1")
    channel_session = ChannelSession(
        channel_session_id="channel-1",
        channel_slug="test",
        provider_conversation_key="conversation-1",
        provider_thread_key="",
        state=ChannelSessionState.ACTIVE,
        created_at_ms=1,
        updated_at_ms=1,
    )
    session = BcnSession(
        bcn_session_id="bcn-1",
        channel_session_id="channel-1",
        workspace_id="workspace-1",
        state=AgentState.CREATED,
        created_at_ms=1,
        updated_at_ms=1,
    )

    with pytest.raises(RuntimeError):
        async with storage.transaction() as transaction:
            await transaction.save_channel_session(channel_session)
            await transaction.save_bcn_session(session)
            raise RuntimeError("rollback")

    assert storage.channel_sessions == {}
    assert storage.bcn_sessions == {}
