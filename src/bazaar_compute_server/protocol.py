"""What a node sends and what the server answers."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

PROTOCOL_VERSION = 1
PROTOCOL_HEADER = "X-BCS-Protocol"


class Event(BaseModel):
    """One audit event as the node serialises it, plus its place in the run."""

    model_config = ConfigDict(extra="allow")

    seq: int = Field(ge=1)
    event_name: str = Field(min_length=1)
    state: str
    created_at_ms: int
    correlation: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReportEventsRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    run_id: str = Field(min_length=1)
    events: list[Event]


def ok(result: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "result": result, "protocol": PROTOCOL_VERSION}


def error(code: str, description: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error_code": code,
        "description": description,
        "protocol": PROTOCOL_VERSION,
    }


__all__ = [
    "PROTOCOL_HEADER",
    "PROTOCOL_VERSION",
    "Event",
    "ReportEventsRequest",
    "error",
    "ok",
]
