from __future__ import annotations

import pytest
from bcn_test_support import MemoryStorage

from bazaar_compute_node.core.models import RuntimeAttempt


@pytest.mark.asyncio
async def test_memory_storage_runtime_attempt_is_independent_and_immutable() -> None:
    storage = MemoryStorage()
    attempt = RuntimeAttempt(
        turn_id="turn-1",
        session_id="runtime-1",
        client_user_message_id="message-1",
        started_at_ms=1,
    )

    await storage.save_runtime_attempt(attempt)
    await storage.save_runtime_attempt(attempt)
    assert await storage.get_runtime_attempt("turn-1") == attempt
    with pytest.raises(ValueError, match="immutable"):
        await storage.save_runtime_attempt(
            RuntimeAttempt(
                turn_id="turn-1",
                session_id="runtime-2",
                client_user_message_id="message-1",
                started_at_ms=1,
            )
        )
