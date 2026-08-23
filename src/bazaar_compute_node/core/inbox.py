from __future__ import annotations

from dataclasses import dataclass

from .models import InboxTargetSummary


@dataclass(frozen=True, slots=True)
class InboxTargetPage:
    """An agent-scoped page of inbox target summaries returned by storage."""

    targets: tuple[InboxTargetSummary, ...]
    total: int
    offset: int

    def __post_init__(self) -> None:
        if not isinstance(self.targets, tuple):
            raise TypeError("targets must be a tuple")
        if any(not isinstance(target, InboxTargetSummary) for target in self.targets):
            raise TypeError("targets must contain InboxTargetSummary values")
        if len(self.targets) > self.total:
            raise ValueError("shown cannot exceed total")

    @property
    def shown(self) -> int:
        return len(self.targets)

    @property
    def has_more(self) -> bool:
        return self.offset + self.shown < self.total
