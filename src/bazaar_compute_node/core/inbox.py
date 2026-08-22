from __future__ import annotations

from dataclasses import dataclass

from .models import InboxTargetSummary


def _validate_pagination_integer(value: int, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")


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
        _validate_pagination_integer(self.total, "total")
        _validate_pagination_integer(self.offset, "offset")
        if len(self.targets) > self.total:
            raise ValueError("shown cannot exceed total")

    @property
    def shown(self) -> int:
        return len(self.targets)

    @property
    def has_more(self) -> bool:
        return self.offset + self.shown < self.total
