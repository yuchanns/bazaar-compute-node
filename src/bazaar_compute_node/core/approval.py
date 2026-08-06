from __future__ import annotations

from dataclasses import dataclass

from .models import ApprovalRequest


@dataclass(frozen=True, slots=True)
class ApprovalBinding:
    """Route one runtime approval request to its current Channel session."""

    request_id: str
    bcn_session_id: str
    channel_session_id: str
    agent_runtime_session_id: str
    turn_id: str | None = None

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.request_id, "request_id"),
            (self.bcn_session_id, "bcn_session_id"),
            (self.channel_session_id, "channel_session_id"),
            (self.agent_runtime_session_id, "agent_runtime_session_id"),
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
            and self.bcn_session_id == request.bcn_session_id
            and self.agent_runtime_session_id == request.agent_runtime_session_id
            and self.turn_id == request.turn_id
        )
