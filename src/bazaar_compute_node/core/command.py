from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .models import InboundMessage, InboxTargetSummary, OutboundMessage
from .reminder import (
    ReminderCancelRequest,
    ReminderCancelResult,
    ReminderCheckRequest,
    ReminderCheckResult,
    ReminderListRequest,
    ReminderListResult,
    ReminderScheduleRequest,
    ReminderScheduleResult,
    ReminderSnoozeRequest,
    ReminderSnoozeResult,
    ReminderUpdateRequest,
    ReminderUpdateResult,
)


@dataclass(frozen=True, slots=True)
class MessageCheckResult:
    """Drain result with a snapshot independent from the delivery cursor."""

    messages: tuple[InboundMessage, ...]
    snapshot_seq: int
    delivered_through_seq: int
    referenced_messages: tuple[InboundMessage, ...] = ()

    def __post_init__(self) -> None:
        if self.snapshot_seq < 0 or self.delivered_through_seq < 0:
            raise ValueError("message sequence values must be non-negative")
        if self.delivered_through_seq > self.snapshot_seq:
            raise ValueError("delivered_through_seq cannot exceed snapshot_seq")


@dataclass(frozen=True, slots=True)
class MessageReadResult:
    """Non-draining history result with the observed inbox snapshot."""

    messages: tuple[InboundMessage, ...]
    snapshot_seq: int
    first_seq: int | None = None
    last_seq: int | None = None
    referenced_messages: tuple[InboundMessage, ...] = ()

    def __post_init__(self) -> None:
        if self.snapshot_seq < 0:
            raise ValueError("snapshot_seq must be non-negative")
        if (self.first_seq is None) != (self.last_seq is None):
            raise ValueError("first_seq and last_seq must be provided together")
        if (
            self.first_seq is not None
            and self.last_seq is not None
            and (self.first_seq < 0 or self.last_seq < self.first_seq)
        ):
            raise ValueError("history sequence bounds are invalid")


def _validate_pagination_integer(value: int, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class InboxListResult:
    """A non-draining, paginated catalog of targets owned by one agent."""

    targets: tuple[InboxTargetSummary, ...]
    total: int
    shown: int
    offset: int
    has_more: bool

    def __post_init__(self) -> None:
        if not isinstance(self.targets, tuple):
            raise TypeError("targets must be a tuple")
        if any(not isinstance(target, InboxTargetSummary) for target in self.targets):
            raise TypeError("targets must contain InboxTargetSummary values")
        _validate_pagination_integer(self.total, "total")
        _validate_pagination_integer(self.shown, "shown")
        _validate_pagination_integer(self.offset, "offset")
        if self.shown != len(self.targets):
            raise ValueError("shown must equal the number of targets")
        if self.shown > self.total:
            raise ValueError("shown cannot exceed total")
        if not isinstance(self.has_more, bool):
            raise TypeError("has_more must be a bool")
        expected_has_more = self.offset + self.shown < self.total
        if self.has_more != expected_has_more:
            raise ValueError("has_more does not match pagination bounds")


class SessionNotFoundError(ValueError):
    """A command referenced a bcn session that is not persisted on this node."""


class ICommandService(Protocol):
    """Session-scoped command surface used by the local wrapper."""

    async def check(self, session_id: str) -> MessageCheckResult:
        """Read new messages and advance only the delivery cursor."""
        ...

    async def read(
        self,
        session_id: str,
        *,
        target: str,
        around_message_id: str | None = None,
        limit: int = 100,
    ) -> MessageReadResult:
        """Read history without advancing the delivery cursor."""
        ...

    async def list_inbox(
        self,
        caller_session_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> InboxListResult:
        """List owned message targets without draining or changing inbox state."""
        ...

    async def send(
        self,
        *,
        session_id: str,
        command_id: str,
        target: str,
        body: str,
        created_at_ms: int,
        attachment_paths: tuple[str, ...] = (),
        reply_to_message_id: str | None = None,
    ) -> OutboundMessage:
        """Run the session fresh-check before calling the Channel port."""
        ...

    async def unfollow(self, session_id: str, *, target: str) -> bool:
        """Disable future group notifications and report whether state changed."""
        ...


class IReminderService(Protocol):
    """Session-scoped Reminder command surface used by the local wrapper."""

    async def schedule(
        self,
        session_id: str,
        request: ReminderScheduleRequest,
    ) -> ReminderScheduleResult: ...

    async def check(
        self,
        session_id: str,
        request: ReminderCheckRequest,
    ) -> ReminderCheckResult: ...

    async def list(
        self,
        session_id: str,
        request: ReminderListRequest,
    ) -> ReminderListResult: ...

    async def snooze(
        self,
        session_id: str,
        request: ReminderSnoozeRequest,
    ) -> ReminderSnoozeResult: ...

    async def update(
        self,
        session_id: str,
        request: ReminderUpdateRequest,
    ) -> ReminderUpdateResult: ...

    async def cancel(
        self,
        session_id: str,
        request: ReminderCancelRequest,
    ) -> ReminderCancelResult: ...
