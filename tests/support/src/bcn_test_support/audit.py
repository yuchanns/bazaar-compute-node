from __future__ import annotations

from bazaar_compute_node.core.audit import AuditEvent
from bazaar_compute_node.core.observability import IAudit


class RecordingAudit(IAudit):
    """Observable append-only audit sink for integration tests."""

    @property
    def name(self) -> str:
        return "test"

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def append(self, event: AuditEvent, *, timeout: float) -> None:
        del timeout
        self.events.append(event)
