from __future__ import annotations

from collections.abc import Callable, Mapping

from bazaar_compute_node.core.audit import AuditEvent, AuditRecorder
from bazaar_compute_node.core.lifecycle import TimeoutBudget
from bazaar_compute_node.core.observability import IAudit


class RecordingAudit(IAudit):
    """Observable append-only audit sink for integration tests."""

    @property
    def name(self) -> str:
        return "test"

    @property
    def health(self) -> Mapping[str, object]:
        return {"events": len(self.events)}

    def __init__(self, options: Mapping[str, object] | None = None) -> None:
        self.options = dict(options or {})
        self.events: list[AuditEvent] = []
        self.started = False

    async def start(self, *, timeout: float) -> None:
        del timeout
        self.started = True

    async def stop(self, *, timeout: float) -> None:
        del timeout
        self.started = False

    async def append(self, event: AuditEvent, *, timeout: float) -> None:
        del timeout
        self.events.append(event)


def recorder_for(
    sink: RecordingAudit, *, clock: Callable[[], int] = lambda: 0
) -> AuditRecorder:
    """An audit recorder over the sink, with budgets no test waits on."""

    return AuditRecorder(
        sink=sink,
        timeout_budget=TimeoutBudget(
            startup_seconds=1,
            provider_call_seconds=1,
            command_seconds=1,
            shutdown_seconds=1,
        ),
        clock=clock,
    )
