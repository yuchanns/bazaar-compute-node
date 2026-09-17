"""What a node sends and what the server answers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PROTOCOL_VERSION = 1
PROTOCOL_HEADER = "X-BCS-Protocol"

# the agent id names the agent in urls, element ids and css anchors; the node
# mints it as a uuid, so anything wider than that is not an id
_AGENT_ID = r"^[A-Za-z0-9_-]{1,64}$"
# what fits on one row of the list
MAX_NAME_CHARS = 100
# a run is named by a UUID; nothing longer is one
MAX_RUN_ID_CHARS = 64
# the pages turn a timestamp into a datetime in the viewer's zone; the last
# year one can hold is the last one that works in every zone
_LATEST_MS = int(datetime(9999, 1, 1, tzinfo=UTC).timestamp() * 1000)


class Correlation(BaseModel):
    """What an event is about; the node says more, the pages need these."""

    model_config = ConfigDict(extra="allow")

    node_id: str | None = Field(default=None, pattern=_AGENT_ID)
    thread_id: str | None = None
    runtime_session_id: str | None = None


class AgentRecord(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    agent_id: str = Field(pattern=_AGENT_ID)
    name: str
    status: str
    channels: list[str] = Field(default_factory=list)
    runtimes: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _name_fits_a_row(cls, name: str) -> str:
        # a name is shown on one row and keyed in the identicon cache; more
        # than this is cut, not refused
        return name[:MAX_NAME_CHARS]


class AuditHealth(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    queued: int | None = None


class Health(BaseModel):
    """What `node.health` says, as far as the pages read it."""

    model_config = ConfigDict(extra="allow", strict=True)

    agents: list[AgentRecord] = Field(default_factory=list)
    version: str | None = None
    system: str | None = None
    audit: AuditHealth = Field(default_factory=AuditHealth)


class TokenTotal(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    # a count the store's INTEGER column can hold, which is also what the
    # pages can do arithmetic on
    total_tokens: int | None = Field(default=None, ge=0, le=2**63 - 1)


class Usage(BaseModel):
    """What `usage.updated` says: the runtime session's running totals."""

    model_config = ConfigDict(extra="allow", strict=True)

    total: TokenTotal = Field(default_factory=TokenTotal)
    cost_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class Inbound(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    target: str | None = None
    target_name: str | None = None


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    name: str | None = None


# the events the pages read into, checked at the door so the pages can read
# them plainly; every other event is kept as it came. the check is strict:
# a value must already be its type, nothing is read as something else
_SHAPES: dict[str, type[BaseModel]] = {
    "node.health": Health,
    "usage.updated": Usage,
    "channel.inbound.persisted": Inbound,
    "tool_call.started": ToolCall,
    "tool_call.completed": ToolCall,
    "tool_call.failed": ToolCall,
}


class Event(BaseModel):
    """One audit event as the node serialises it, plus its place in the run."""

    model_config = ConfigDict(extra="allow")

    seq: int = Field(ge=1, le=2**63 - 1)
    event_name: str = Field(min_length=1)
    state: str
    created_at_ms: int = Field(ge=0, le=_LATEST_MS)
    correlation: Correlation = Field(default_factory=Correlation)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _metadata_fits_the_event(self) -> Event:
        shape = _SHAPES.get(self.event_name)
        if shape is not None:
            # what is kept is what the shape made of it: a name cut to size,
            # a field left out filled with its default
            self.metadata = shape.model_validate(self.metadata).model_dump()
        return self


class ReportEventsRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    run_id: str = Field(min_length=1, max_length=MAX_RUN_ID_CHARS)
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
    "Correlation",
    "Event",
    "ReportEventsRequest",
    "error",
    "ok",
]
