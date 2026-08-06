from __future__ import annotations

import pytest

from bazaar_compute_node.contrib.dummy import DummyStorage
from bazaar_compute_node.core.models import (
    BcnSession,
    BcnSessionState,
)


@pytest.mark.asyncio
async def test_dummy_storage_transaction_rolls_back_on_error() -> None:
    storage = DummyStorage()
    session = BcnSession(
        bcn_session_id="bcn-1",
        channel_session_id="channel-1",
        workspace_uuid="workspace-1",
        state=BcnSessionState.CREATED,
        created_at_ms=1,
        updated_at_ms=1,
    )

    with pytest.raises(RuntimeError):
        async with storage.transaction() as transaction:
            await transaction.save_bcn_session(session)
            raise RuntimeError("rollback")

    assert storage.bcn_sessions == {}
