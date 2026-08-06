from __future__ import annotations

from ...core.audit import AuditEvent
from ...core.observability import IAudit


class DummyAudit(IAudit):
    """Observable append-only audit sink for the Dummy composition."""

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def append(self, event: AuditEvent, *, timeout: float) -> None:
        self.events.append(event)
