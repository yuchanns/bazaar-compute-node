from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from .lifecycle import IAsyncLifecycle, TimeoutBudget
from .timerwheel import TimerWheel

if TYPE_CHECKING:
    from .audit import AuditEvent


class LogLevel(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class AuditContext:
    """What the node hands an audit sink when it builds one."""

    options: Mapping[str, object]
    timer_wheel: TimerWheel
    timeout_budget: TimeoutBudget


class IAudit(IAsyncLifecycle, Protocol):
    """Durable append-only operational event boundary."""

    @property
    def name(self) -> str:
        """Return the stable entry-point identity of this adapter."""
        ...

    @property
    def health(self) -> Mapping[str, object]:
        """Describe how the sink is doing, for the node health record."""
        ...

    async def append(self, event: AuditEvent, *, timeout: float) -> None:
        """Persist one event; cancellation must propagate to the caller."""
        ...
