from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .models import ApprovalRequest, ApprovalResult


class IApprovalHandler(Protocol):
    """Neutral callback used by a runtime adapter for approval requests."""

    async def request_approval(
        self, request: ApprovalRequest, *, timeout: float
    ) -> ApprovalResult:
        """Route one request to the current Channel approval policy."""
        ...


@dataclass(frozen=True, slots=True)
class ApprovalBinding:
    """Route one runtime approval request to its current Channel session."""

    request_id: str
    bcn_session_id: str
    channel_session_id: str
    runtime_session_id: str
    turn_id: str | None = None

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.request_id, "request_id"),
            (self.bcn_session_id, "bcn_session_id"),
            (self.channel_session_id, "channel_session_id"),
            (self.runtime_session_id, "runtime_session_id"),
        ):
            if not isinstance(value, str) or not value:
                raise ValueError(f"{field_name} must be a non-empty string")
        if self.turn_id is not None and (
            not isinstance(self.turn_id, str) or not self.turn_id
        ):
            raise ValueError("turn_id must be a non-empty string when present")

    def matches(self, request: ApprovalRequest) -> bool:
        """Ensure a response is returned to the same runtime request context."""

        return (
            self.request_id == request.request_id
            and self.bcn_session_id == request.session_id
            and self.runtime_session_id == request.runtime_session_id
            and self.turn_id == request.turn_id
        )
