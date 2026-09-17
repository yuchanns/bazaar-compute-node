from __future__ import annotations

import asyncio
from pathlib import Path

import aiosqlite
import pytest

from bazaar_compute_node.contrib.server.audit import ServerAudit
from bazaar_compute_node.core.audit import AuditEvent
from bazaar_compute_node.core.correlation import CorrelationContext
from bazaar_compute_node.core.lifecycle import TimeoutBudget
from bazaar_compute_node.core.models import RuntimeEventState

from ._serving import enrol, serving


@pytest.mark.asyncio
async def test_a_node_sink_and_the_server_agree_on_the_wire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # the two sides share no code; this is the one place they meet
    async with serving(tmp_path) as (base, storage):
        enrolment = await enrol(storage, "kana")
        monkeypatch.setenv("BCN_SERVER_TOKEN", enrolment.token)
        audit = ServerAudit(
            {"url": base, "token_env": "BCN_SERVER_TOKEN"},
            timeout_budget=TimeoutBudget(5, 5, 5, 1),
        )
        try:
            await audit.start(timeout=1)
            for index in range(3):
                await audit.append(
                    AuditEvent(
                        event_name="channel.inbound.persisted",
                        state=RuntimeEventState.COMPLETED,
                        created_at_ms=index,
                        correlation=CorrelationContext(
                            node_id="agent-1", thread_id="t-1"
                        ),
                        metadata={"text": f"消息 {index}"},
                    ),
                    timeout=1,
                )
            async with asyncio.timeout(5):
                while audit.health["sent"] != 3:
                    await asyncio.sleep(0.01)
            assert audit.health["last_error"] is None
            assert await storage.count_events(enrolment.computer.id) == 3
            async with (
                aiosqlite.connect(tmp_path / "bcs.sqlite3") as connection,
                connection.execute(
                    "SELECT agent_id, thread_id, payload FROM events ORDER BY seq"
                ) as cursor,
            ):
                rows = [tuple(row) async for row in cursor]
            assert [row[:2] for row in rows] == [("agent-1", "t-1")] * 3
            assert '"text":"消息 2"' in rows[2][2]
        finally:
            await audit.stop(timeout=1)
