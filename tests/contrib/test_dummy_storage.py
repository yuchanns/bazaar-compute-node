from __future__ import annotations

import pytest

from bazaar_compute_node.contrib.dummy import DummyStorage
from bazaar_compute_node.core.models import (
    BcnSession,
    BcnSessionState,
    ChannelSession,
    ChannelSessionState,
)


@pytest.mark.asyncio
async def test_dummy_storage_transaction_rolls_back_on_error() -> None:
    storage = DummyStorage()
    await storage.initialize(node_id="node-1", workspace_id="workspace-1")
    channel_session = ChannelSession(
        channel_session_id="channel-1",
        channel_slug="dummy",
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
        state=BcnSessionState.CREATED,
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
