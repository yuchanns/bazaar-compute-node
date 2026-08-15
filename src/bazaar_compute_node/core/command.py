from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .models import InboundMessage, OutboundMessage
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
