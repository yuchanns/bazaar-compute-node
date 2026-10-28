"""Provider-neutral turn, content, tool and usage events emitted by harnesses."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from .outcomes import HarnessFailure


class ContentDeltaKind(StrEnum):
    AGENT_MESSAGE = "agent_message"
    PLAN = "plan"
    REASONING_TEXT = "reasoning_text"
    REASONING_SUMMARY = "reasoning_summary"


class ToolCallDeltaKind(StrEnum):
    INPUT = "input"
    OUTPUT = "output"
    PROGRESS = "progress"


class FileChangeEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: str
    kind: str
    patch: str | None = None


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    call_id: str
    name: str
    parent_call_id: str | None = None
    input: JsonValue = None
    output: JsonValue = None


class TokenUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    cache_write_input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_output_tokens: int | None = None
    total_tokens: int | None = None


class TurnStarted(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["started"] = "started"
    event_name: str
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class TurnCompleted(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["completed"] = "completed"
    event_name: str
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class TurnFailed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["failed"] = "failed"
    event_name: str
    failure: HarnessFailure
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class TurnCancelled(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["cancelled"] = "cancelled"
    event_name: str
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class ContentDelta(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["content.delta"] = "content.delta"
    kind: ContentDeltaKind
    text: str
    index: int | None = None


class ContextCompactionStarted(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["compaction.started"] = "compaction.started"
    compaction_id: str | None = None


class ContextCompactionCompleted(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["compaction.completed"] = "compaction.completed"
    compaction_id: str | None = None


class ToolCallStarted(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["tool.started"] = "tool.started"
    call: ToolCall


class ToolCallCompleted(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["tool.completed"] = "tool.completed"
    call: ToolCall


class ToolCallFailed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["tool.failed"] = "tool.failed"
    call: ToolCall
    error_message: str | None = None


class ToolCallTextDelta(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["tool.text.delta"] = "tool.text.delta"
    call_id: str
    kind: ToolCallDeltaKind
    text: str


class ToolCallPatchUpdated(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["tool.patch.updated"] = "tool.patch.updated"
    call_id: str
    changes: tuple[FileChangeEntry, ...]


class ToolCallInteraction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["tool.interaction"] = "tool.interaction"
    call_id: str
    stdin: str
    process_id: str | None = None


class UsageUpdated(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["usage.updated"] = "usage.updated"
    total: TokenUsage
    last: TokenUsage | None = None
    model_context_window: int | None = None
    cost_usd: float | None = None


type HarnessEventPayload = Annotated[
    TurnStarted
    | TurnCompleted
    | TurnFailed
    | TurnCancelled
    | ContentDelta
    | ContextCompactionStarted
    | ContextCompactionCompleted
    | ToolCallStarted
    | ToolCallCompleted
    | ToolCallFailed
    | ToolCallTextDelta
    | ToolCallPatchUpdated
    | ToolCallInteraction
    | UsageUpdated,
    Field(discriminator="type"),
]


class HarnessOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    session_id: str
    turn_id: str
    payload: HarnessEventPayload
    provider: str | None = None
    provider_event: str | None = None
    provider_session_id: str | None = None
    provider_turn_id: str | None = None
    occurred_at_ms: int | None = None
    raw: JsonValue = None


class HarnessSourceEvent(BaseModel):
    """One native protocol message, recorded before selection or projection."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    provider: str
    source_id: str
    sequence: int
    direction: Literal["sent", "received"]
    data: str
    occurred_at_ms: int
    monotonic_ns: int
