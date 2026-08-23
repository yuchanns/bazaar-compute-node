from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CorrelationContext:
    """Provider-neutral identifiers shared by runtime, channel, and command flows."""

    node_id: str | None = None
    channel: str | None = None
    channel_session_id: str | None = None
    bcn_session_id: str | None = None
    runtime_session_id: str | None = None
    turn_id: str | None = None
    request_id: str | None = None
    command_id: str | None = None
    inbound_seq: int | None = None
    outbound_message_id: str | None = None
    provider_request_id: str | None = None
    provider_thread_id: str | None = None
    provider_turn_id: str | None = None

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.node_id, "node_id"),
            (self.channel, "channel"),
            (self.channel_session_id, "channel_session_id"),
            (self.bcn_session_id, "bcn_session_id"),
            (self.runtime_session_id, "runtime_session_id"),
            (self.turn_id, "turn_id"),
            (self.request_id, "request_id"),
            (self.command_id, "command_id"),
            (self.outbound_message_id, "outbound_message_id"),
            (self.provider_request_id, "provider_request_id"),
            (self.provider_thread_id, "provider_thread_id"),
            (self.provider_turn_id, "provider_turn_id"),
        ):
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError(
                    f"{field_name} must be a non-empty string when present"
                )
