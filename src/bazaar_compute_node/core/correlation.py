from __future__ import annotations

from dataclasses import dataclass

from .actor import Actor


@dataclass(frozen=True, slots=True)
class CorrelationContext:
    """Provider-neutral identifiers shared by runtime, channel, and command flows."""

    node_id: str | None = None
    channel: str | None = None
    channel_session_id: str | None = None
    thread_id: str | None = None
    actor: Actor | None = None
    runtime_session_id: str | None = None
    turn_id: str | None = None
    request_id: str | None = None
    command_id: str | None = None
    inbound_seq: int | None = None
    outbound_message_id: str | None = None
    provider_request_id: str | None = None
    provider_thread_id: str | None = None
    provider_turn_id: str | None = None
