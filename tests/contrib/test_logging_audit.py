from __future__ import annotations

import io
import json
import logging

import pytest

from bazaar_compute_node.contrib.logging import LoggingAudit
from bazaar_compute_node.core.audit import AuditEvent
from bazaar_compute_node.core.correlation import CorrelationContext
from bazaar_compute_node.core.models import RuntimeEventState


@pytest.mark.asyncio
async def test_logging_audit_emits_structured_event() -> None:
    stream = io.StringIO()
    logger = logging.getLogger("bazaar_compute_node.test.audit")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(logging.StreamHandler(stream))
    audit = LoggingAudit(logger)

    await audit.append(
        AuditEvent(
            event_name="runtime.turn.completed",
            state=RuntimeEventState.COMPLETED,
            created_at_ms=42,
            correlation=CorrelationContext(
                node_id="node-1",
                bcn_session_id="bcn-1",
                turn_id="turn-1",
            ),
            metadata={"operation": "turn"},
        ),
        timeout=1,
    )

    payload = json.loads(stream.getvalue())
    assert payload == {
        "correlation": {
            "bcn_session_id": "bcn-1",
            "node_id": "node-1",
            "turn_id": "turn-1",
        },
        "created_at_ms": 42,
        "event_name": "runtime.turn.completed",
        "metadata": {"operation": "turn"},
        "state": "completed",
    }
